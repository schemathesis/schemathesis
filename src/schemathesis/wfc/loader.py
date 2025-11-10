"""WFC authentication document loading and validation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import jsonschema

from .auth import AuthDocument, AuthenticationInfo, Header, LoginEndpoint, PayloadUsernamePassword, TokenHandling
from .errors import WFCLoadError, WFCValidationError

# Load WFC JSON Schema
_SCHEMA_PATH = Path(__file__).parent / "schema.json"
with _SCHEMA_PATH.open(encoding="utf-8") as _fd:
    WFC_SCHEMA = json.loads(_fd.read())

WFC_VALIDATOR = jsonschema.validators.Draft202012Validator(WFC_SCHEMA)


def load_from_file(path: str | Path) -> AuthDocument:
    """Load WFC authentication document from a JSON or YAML file.

    Args:
        path: Path to the WFC auth file (.json or .yaml/.yml)

    Returns:
        Loaded and validated auth document

    Raises:
        WFCLoadError: If file cannot be read or parsed
        WFCValidationError: If document structure is invalid

    """
    path_obj = Path(path)

    if not path_obj.exists():
        raise WFCLoadError(f"WFC auth file not found: {path}")

    if not path_obj.is_file():
        raise WFCLoadError(f"WFC auth path is not a file: {path}")

    try:
        with path_obj.open(encoding="utf-8") as fd:
            content = fd.read()
    except (OSError, IOError) as exc:
        raise WFCLoadError(f"Failed to read WFC auth file: {exc}") from exc

    suffix = path_obj.suffix.lower()
    if suffix == ".json":
        try:
            data = json.loads(content)
        except json.JSONDecodeError as exc:
            raise WFCLoadError(f"Invalid JSON in WFC auth file: {exc}") from exc
    elif suffix in (".yaml", ".yml"):
        try:
            import yaml
        except ImportError as exc:
            raise WFCLoadError("PyYAML is required to load YAML files. Install it with: pip install pyyaml") from exc
        try:
            data = yaml.safe_load(content)
        except yaml.YAMLError as exc:
            raise WFCLoadError(f"Invalid YAML in WFC auth file: {exc}") from exc
    else:
        raise WFCLoadError(f"Unsupported file extension: {suffix}. Use .json, .yaml, or .yml")

    if not isinstance(data, dict):
        raise WFCLoadError(f"WFC auth document must be an object, got {type(data).__name__}")

    return load_from_dict(data)


def load_from_dict(data: dict[str, Any]) -> AuthDocument:
    """Load WFC authentication document from a dictionary.

    Args:
        data: Parsed WFC auth document

    Returns:
        Loaded and validated auth document

    Raises:
        WFCValidationError: If document structure is invalid

    """
    from jsonschema.exceptions import ValidationError

    # Validate against JSON Schema
    try:
        WFC_VALIDATOR.validate(data)
    except ValidationError as exc:
        raise WFCValidationError.from_validation_error(exc) from None

    # Parse auth template if present
    auth_template = None
    if "authTemplate" in data:
        auth_template = _parse_auth_info(data["authTemplate"])

    # Parse auth entries
    auth_list = [_parse_auth_info(auth_data) for auth_data in data["auth"]]

    # Parse configs if present
    configs = dict(data["configs"]) if "configs" in data else None

    # Create document
    doc = AuthDocument(
        auth=auth_list,
        schema_version=data.get("schemaVersion"),
        auth_template=auth_template,
        configs=configs,
    )

    # Apply template merging
    if auth_template is not None:
        apply_template(doc)

    # Validate logical constraints
    validate_document(doc)

    return doc


def _parse_auth_info(data: dict[str, Any]) -> AuthenticationInfo:
    """Parse AuthenticationInfo from dictionary.

    Args:
        data: Auth info dictionary (already JSON schema validated)

    Returns:
        Parsed AuthenticationInfo

    """
    name = data.get("name", "template")  # template for authTemplate case
    require_mock_handling = data.get("requireMockHandling")

    # Parse fixedHeaders
    fixed_headers = None
    if "fixedHeaders" in data:
        fixed_headers = [Header(name=h["name"], value=h["value"]) for h in data["fixedHeaders"]]

    # Parse loginEndpointAuth
    login_endpoint_auth = None
    if "loginEndpointAuth" in data:
        login_endpoint_auth = _parse_login_endpoint(data["loginEndpointAuth"])

    return AuthenticationInfo(
        name=name,
        require_mock_handling=require_mock_handling,
        fixed_headers=fixed_headers,
        login_endpoint_auth=login_endpoint_auth,
    )


def _parse_login_endpoint(data: dict[str, Any]) -> LoginEndpoint:
    """Parse LoginEndpoint from dictionary.

    Args:
        data: Login endpoint dictionary (already JSON schema validated)

    Returns:
        Parsed LoginEndpoint

    """
    verb = data["verb"]
    endpoint = data.get("endpoint")
    external_endpoint_url = data.get("externalEndpointURL")
    payload_raw = data.get("payloadRaw")

    # Parse payloadUserPwd
    payload_user_pwd = None
    if "payloadUserPwd" in data:
        pwd_data = data["payloadUserPwd"]
        payload_user_pwd = PayloadUsernamePassword(
            username=pwd_data["username"],
            password=pwd_data["password"],
            username_field=pwd_data["usernameField"],
            password_field=pwd_data["passwordField"],
        )

    # Parse headers
    headers = None
    if "headers" in data:
        headers = [Header(name=h["name"], value=h["value"]) for h in data["headers"]]

    content_type = data.get("contentType")

    # Parse token
    token = None
    if "token" in data:
        token_data = data["token"]
        token = TokenHandling(
            extract_from=token_data["extractFrom"],
            extract_selector=token_data["extractSelector"],
            send_in=token_data["sendIn"],
            send_name=token_data["sendName"],
            send_format=token_data.get("sendFormat", "{token}"),
        )

    expect_cookies = data.get("expectCookies")

    return LoginEndpoint(
        verb=verb,
        endpoint=endpoint,
        external_endpoint_url=external_endpoint_url,
        payload_raw=payload_raw,
        payload_user_pwd=payload_user_pwd,
        headers=headers,
        content_type=content_type,
        token=token,
        expect_cookies=expect_cookies,
    )


def apply_template(doc: AuthDocument) -> None:
    """Apply authTemplate to all auth entries in the document.

    Performs deep merging where template fields are applied to entries
    that don't explicitly define them. Mutates the document in place.

    Args:
        doc: Auth document with template to apply

    """
    if doc.auth_template is None:
        return

    template = doc.auth_template

    for auth_info in doc.auth:
        # Merge requireMockHandling
        if auth_info.require_mock_handling is None and template.require_mock_handling is not None:
            auth_info.require_mock_handling = template.require_mock_handling

        # Merge fixedHeaders (concatenate lists)
        if template.fixed_headers:
            # Add template headers that aren't already present (by name)
            existing_names = {h.name for h in auth_info.fixed_headers}
            for header in template.fixed_headers:
                if header.name not in existing_names:
                    auth_info.fixed_headers.append(Header(name=header.name, value=header.value))

        # Merge loginEndpointAuth (deep merge)
        if template.login_endpoint_auth is not None:
            if auth_info.login_endpoint_auth is None:
                # Clone entire template login config
                auth_info.login_endpoint_auth = _clone_login_endpoint(template.login_endpoint_auth)
            else:
                # Merge individual fields
                _merge_login_endpoint(auth_info.login_endpoint_auth, template.login_endpoint_auth)


def _clone_login_endpoint(src: LoginEndpoint) -> LoginEndpoint:
    """Create a deep copy of a LoginEndpoint."""
    return LoginEndpoint(
        verb=src.verb,
        endpoint=src.endpoint,
        external_endpoint_url=src.external_endpoint_url,
        payload_raw=src.payload_raw,
        payload_user_pwd=_clone_payload_user_pwd(src.payload_user_pwd) if src.payload_user_pwd else None,
        headers=[Header(name=h.name, value=h.value) for h in src.headers] if src.headers else None,
        content_type=src.content_type,
        token=_clone_token_handling(src.token) if src.token else None,
        expect_cookies=src.expect_cookies,
    )


def _clone_payload_user_pwd(src: PayloadUsernamePassword) -> PayloadUsernamePassword:
    """Create a deep copy of PayloadUsernamePassword."""
    return PayloadUsernamePassword(
        username=src.username,
        password=src.password,
        username_field=src.username_field,
        password_field=src.password_field,
    )


def _clone_token_handling(src: TokenHandling) -> TokenHandling:
    """Create a deep copy of TokenHandling."""
    return TokenHandling(
        extract_from=src.extract_from,
        extract_selector=src.extract_selector,
        send_in=src.send_in,
        send_name=src.send_name,
        send_format=src.send_format,
    )


def _merge_login_endpoint(dest: LoginEndpoint, src: LoginEndpoint) -> None:
    """Merge source login endpoint fields into destination (in place).

    Only applies fields from src that are not set in dest.

    Args:
        dest: Destination login endpoint (modified in place)
        src: Source login endpoint (template)

    """
    # Merge simple fields (only if dest field is None)
    if dest.endpoint is None and src.endpoint is not None:
        dest.endpoint = src.endpoint
    if dest.external_endpoint_url is None and src.external_endpoint_url is not None:
        dest.external_endpoint_url = src.external_endpoint_url
    if dest.payload_raw is None and src.payload_raw is not None:
        dest.payload_raw = src.payload_raw
    if dest.payload_user_pwd is None and src.payload_user_pwd is not None:
        dest.payload_user_pwd = _clone_payload_user_pwd(src.payload_user_pwd)
    if dest.content_type is None and src.content_type is not None:
        dest.content_type = src.content_type
    if dest.token is None and src.token is not None:
        dest.token = _clone_token_handling(src.token)
    if dest.expect_cookies is None and src.expect_cookies is not None:
        dest.expect_cookies = src.expect_cookies

    # Merge headers (concatenate lists)
    if src.headers:
        existing_names = {h.name for h in dest.headers}
        for header in src.headers:
            if header.name not in existing_names:
                dest.headers.append(Header(name=header.name, value=header.value))


def validate_document(doc: AuthDocument) -> None:
    """Validate WFC auth document for logical consistency.

    This performs validation beyond what JSON Schema can express.

    Args:
        doc: Auth document to validate

    Raises:
        WFCValidationError: If document has logical errors

    """
    # Validate each auth entry
    for idx, auth_info in enumerate(doc.auth):
        _validate_auth_info(auth_info, f"auth[{idx}]")

    # Check for duplicate names
    names = [auth.name for auth in doc.auth]
    duplicates = [name for name in names if names.count(name) > 1]
    if duplicates:
        unique_duplicates = sorted(set(duplicates))
        raise WFCValidationError(
            f"Duplicate auth names found: {', '.join(repr(n) for n in unique_duplicates)}. "
            f"Each auth entry must have a unique name."
        )


def _validate_auth_info(auth_info: AuthenticationInfo, context: str) -> None:
    """Validate a single AuthenticationInfo for logical consistency.

    Args:
        auth_info: Auth info to validate
        context: Context string for error messages

    Raises:
        WFCValidationError: If auth info has logical errors

    """
    has_fixed = auth_info.fixed_headers is not None and len(auth_info.fixed_headers) > 0
    has_login = auth_info.login_endpoint_auth is not None

    if not has_fixed and not has_login:
        raise WFCValidationError(
            f"{context} ('{auth_info.name}'): Must specify either 'fixedHeaders' or 'loginEndpointAuth'"
        )

    if has_fixed and has_login:
        raise WFCValidationError(
            f"{context} ('{auth_info.name}'): Cannot specify both 'fixedHeaders' and 'loginEndpointAuth'. "
            f"Choose one authentication method."
        )

    # Validate login endpoint if present
    if has_login:
        assert auth_info.login_endpoint_auth is not None
        _validate_login_endpoint(auth_info.login_endpoint_auth, f"{context}.loginEndpointAuth")


def _validate_login_endpoint(login: LoginEndpoint, context: str) -> None:
    """Validate LoginEndpoint for logical consistency.

    Args:
        login: Login endpoint to validate
        context: Context string for error messages

    Raises:
        WFCValidationError: If login endpoint has logical errors

    """
    # Validate endpoint XOR externalEndpointURL
    has_endpoint = login.endpoint is not None
    has_external = login.external_endpoint_url is not None

    if not has_endpoint and not has_external:
        raise WFCValidationError(f"{context}: Must specify either 'endpoint' or 'externalEndpointURL'")

    if has_endpoint and has_external:
        raise WFCValidationError(f"{context}: Cannot specify both 'endpoint' and 'externalEndpointURL'. Choose one.")

    # Validate payload XOR payloadUserPwd (both can be None)
    has_payload_raw = login.payload_raw is not None
    has_payload_pwd = login.payload_user_pwd is not None

    if has_payload_raw and has_payload_pwd:
        raise WFCValidationError(f"{context}: Cannot specify both 'payloadRaw' and 'payloadUserPwd'. Choose one.")

    # Validate token XOR expectCookies (both can be None/False)
    has_token = login.token is not None
    has_cookies = login.expect_cookies is True

    if has_token and has_cookies:
        raise WFCValidationError(f"{context}: Cannot specify both 'token' and 'expectCookies=true'. Choose one.")

    # If token is specified, validate extractSelector format
    if has_token:
        assert login.token is not None
        _validate_token_handling(login.token, f"{context}.token")


def _validate_token_handling(token: TokenHandling, context: str) -> None:
    """Validate TokenHandling for logical consistency.

    Args:
        token: Token handling to validate
        context: Context string for error messages

    Raises:
        WFCValidationError: If token handling has logical errors

    """
    # Validate extractSelector format based on extractFrom
    if token.extract_from == "body":
        # For body extraction, selector should be a JSON Pointer (start with /)
        if not token.extract_selector.startswith("/"):
            raise WFCValidationError(
                f"{context}.extractSelector: When extractFrom='body', selector must be a JSON Pointer "
                f"(RFC 6901) starting with '/'. Got: '{token.extract_selector}'"
            )
    # For header extraction, any string is valid (case-insensitive header name)

    # Validate sendFormat contains {token} placeholder
    if "{token}" not in token.send_format:
        raise WFCValidationError(
            f"{context}.sendFormat: Must contain '{{token}}' placeholder. Got: '{token.send_format}'"
        )
