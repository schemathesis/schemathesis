"""WFC authentication document loading and validation."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

import jsonschema_rs

from .auth import AuthenticationInfo, Header, LoginEndpoint, PayloadUsernamePassword, TokenHandling
from .errors import WFCLoadError, WFCValidationError

_SCHEMA_PATH = Path(__file__).parent / "schema.json"


@lru_cache(maxsize=1)
def _validator() -> jsonschema_rs.Validator:
    schema = json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))
    return jsonschema_rs.validator_for(schema)


def load_from_file(path: str | Path) -> list[AuthenticationInfo]:
    """Load a WFC authentication document from a JSON or YAML file."""
    path_obj = Path(path)
    if not path_obj.exists():
        raise WFCLoadError(f"WFC auth file not found: {path}")
    if not path_obj.is_file():
        raise WFCLoadError(f"WFC auth path is not a file: {path}")

    try:
        content = path_obj.read_text(encoding="utf-8")
    except OSError as exc:
        raise WFCLoadError(f"Failed to read WFC auth file: {exc}") from exc

    suffix = path_obj.suffix.lower()
    if suffix == ".json":
        try:
            data = json.loads(content)
        except json.JSONDecodeError as exc:
            raise WFCLoadError(f"Invalid JSON in WFC auth file: {exc}") from exc
    elif suffix in (".yaml", ".yml"):
        import yaml

        try:
            data = yaml.safe_load(content)
        except yaml.YAMLError as exc:
            raise WFCLoadError(f"Invalid YAML in WFC auth file: {exc}") from exc
    else:
        raise WFCLoadError(f"Unsupported file extension: {suffix}. Use .json, .yaml, or .yml")

    if not isinstance(data, dict):
        raise WFCLoadError(f"WFC auth document must be an object, got {type(data).__name__}")

    return load_from_dict(data)


def load_from_dict(data: dict[str, Any]) -> list[AuthenticationInfo]:
    """Load a WFC authentication document from a parsed dictionary."""
    try:
        _validator().validate(data)
    except jsonschema_rs.ValidationError as exc:
        raise WFCValidationError(exc.message) from None

    entries = [_parse_auth_info(entry) for entry in data["auth"]]
    validate_document(entries)
    return entries


def _parse_auth_info(data: dict[str, Any]) -> AuthenticationInfo:
    fixed_headers = [Header(name=h["name"], value=h["value"]) for h in data.get("fixedHeaders", [])]
    login_endpoint_auth = None
    if "loginEndpointAuth" in data:
        login_endpoint_auth = _parse_login_endpoint(data["loginEndpointAuth"])
    return AuthenticationInfo(
        name=data["name"],
        fixed_headers=fixed_headers,
        login_endpoint_auth=login_endpoint_auth,
    )


def _parse_login_endpoint(data: dict[str, Any]) -> LoginEndpoint:
    credentials = None
    if "payloadUserPwd" in data:
        raw = data["payloadUserPwd"]
        credentials = PayloadUsernamePassword(
            username=raw["username"],
            password=raw["password"],
            username_field=raw["usernameField"],
            password_field=raw["passwordField"],
        )
    headers = [Header(name=h["name"], value=h["value"]) for h in data.get("headers", [])]
    token = None
    if "token" in data:
        token_data = data["token"]
        token = TokenHandling(
            extract_from=token_data["extractFrom"],
            extract_selector=token_data["extractSelector"],
            send_in=token_data["sendIn"],
            send_name=token_data["sendName"],
            send_template=token_data.get("sendTemplate", "{token}"),
        )
    return LoginEndpoint(
        verb=data["verb"],
        endpoint=data.get("endpoint"),
        external_endpoint_url=data.get("externalEndpointURL"),
        payload_raw=data.get("payloadRaw"),
        credentials=credentials,
        headers=headers,
        content_type=data.get("contentType"),
        token=token,
        expect_cookies=data.get("expectCookies"),
    )


def validate_document(entries: list[AuthenticationInfo]) -> None:
    """Validate logical constraints beyond what JSON Schema expresses."""
    for index, auth_info in enumerate(entries):
        _validate_auth_info(auth_info, f"auth[{index}]")

    names = [auth.name for auth in entries]
    duplicates = sorted({name for name in names if names.count(name) > 1})
    if duplicates:
        joined = ", ".join(repr(n) for n in duplicates)
        raise WFCValidationError(f"Duplicate auth names found: {joined}. Each auth entry must have a unique name.")


def _validate_auth_info(auth_info: AuthenticationInfo, context: str) -> None:
    has_fixed = bool(auth_info.fixed_headers)
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
    if has_login:
        assert auth_info.login_endpoint_auth is not None
        _validate_login_endpoint(auth_info.login_endpoint_auth, f"{context}.loginEndpointAuth")


def _validate_login_endpoint(login: LoginEndpoint, context: str) -> None:
    has_endpoint = login.endpoint is not None
    has_external = login.external_endpoint_url is not None
    if not has_endpoint and not has_external:
        raise WFCValidationError(f"{context}: Must specify either 'endpoint' or 'externalEndpointURL'")
    if has_endpoint and has_external:
        raise WFCValidationError(f"{context}: Cannot specify both 'endpoint' and 'externalEndpointURL'. Choose one.")

    if login.payload_raw is not None and login.credentials is not None:
        raise WFCValidationError(f"{context}: Cannot specify both 'payloadRaw' and 'payloadUserPwd'. Choose one.")

    has_token = login.token is not None
    if has_token and login.expect_cookies is True:
        raise WFCValidationError(f"{context}: Cannot specify both 'token' and 'expectCookies=true'. Choose one.")
    if not has_token and login.expect_cookies is not True:
        raise WFCValidationError(f"{context}: Must specify either 'token' or 'expectCookies=true'.")
    if has_token:
        assert login.token is not None
        _validate_token_handling(login.token, f"{context}.token")


def _validate_token_handling(token: TokenHandling, context: str) -> None:
    if token.extract_from == "body" and not token.extract_selector.startswith("/"):
        raise WFCValidationError(
            f"{context}.extractSelector: When extractFrom='body', selector must be a JSON Pointer "
            f"(RFC 6901) starting with '/'. Got: '{token.extract_selector}'"
        )
    if "{token}" not in token.send_template:
        raise WFCValidationError(
            f"{context}.sendTemplate: Must contain '{{token}}' placeholder. Got: '{token.send_template}'"
        )
