"""Web Fuzzing Commons authentication data structures.

These dataclasses represent the WFC authentication schema as defined in:
https://github.com/WebFuzzing/Commons/blob/master/src/main/resources/wfc/schemas/auth.yaml
"""

from __future__ import annotations

from typing import Literal

HttpVerb = Literal["POST", "GET", "PATCH", "DELETE", "PUT"]


class Header:
    """HTTP header information."""

    __slots__ = ("name", "value")

    def __init__(self, name: str, value: str) -> None:
        self.name = name
        self.value = value


class PayloadUsernamePassword:
    """Payload with username and password information.

    This will be automatically formatted into a proper payload based on content type.
    """

    __slots__ = ("username", "password", "username_field", "password_field")

    def __init__(
        self,
        username: str,
        password: str,
        username_field: str,
        password_field: str,
    ) -> None:
        self.username = username
        self.password = password
        self.username_field = username_field
        self.password_field = password_field


class TokenHandling:
    """Token extraction from login response and injection into authenticated requests.

    Handles the full lifecycle of token-based authentication:
    1. Extract token from login endpoint response (body or header)
    2. Format token with optional prefix (e.g., "Bearer {token}")
    3. Inject token into subsequent requests (header or query parameter)
    """

    __slots__ = ("extract_from", "extract_selector", "send_in", "send_name", "send_format")

    def __init__(
        self,
        extract_from: Literal["body", "header"],
        extract_selector: str,
        send_in: Literal["header", "query"],
        send_name: str,
        send_format: str = "{token}",
    ) -> None:
        # Where to extract token from login response
        self.extract_from = extract_from
        """Specify from where the token should be extracted in the HTTP response."""

        # JSON Pointer (RFC 6901) for body, or header name for header
        self.extract_selector = extract_selector
        """How to extract the token from the HTTP response.

        For 'body' location: JSON Pointer (RFC 6901) like '/data/token' or '/access_token'
        For 'header' location: header name like 'X-Auth-Token'
        """

        # Where to send token in authenticated requests
        self.send_in = send_in
        """Where the token should be placed in authenticated requests."""

        # Header or query parameter name
        self.send_name = send_name
        """Header or query parameter name where the token should be sent.

        Typically 'Authorization' for headers.
        """

        # Template with {token} placeholder (e.g., "Bearer {token}")
        self.send_format = send_format
        """Template with {token} placeholder for formatting the token value.

        Examples: 'Bearer {token}', 'JWT {token}', or just '{token}'
        """


class LoginEndpoint:
    """Configuration for calling a login endpoint to obtain authentication credentials.

    Supports both token-based authentication (extract token from response) and
    cookie-based authentication (use session cookies from response).
    """

    __slots__ = (
        "verb",
        "endpoint",
        "external_endpoint_url",
        "payload_raw",
        "payload_user_pwd",
        "headers",
        "content_type",
        "token",
        "expect_cookies",
    )

    def __init__(
        self,
        verb: HttpVerb,
        endpoint: str | None = None,
        external_endpoint_url: str | None = None,
        payload_raw: str | None = None,
        payload_user_pwd: PayloadUsernamePassword | None = None,
        headers: list[Header] | None = None,
        content_type: str | None = None,
        token: TokenHandling | None = None,
        expect_cookies: bool | None = None,
    ) -> None:
        self.verb = verb
        """HTTP verb to use for the login request (typically POST)."""

        self.endpoint = endpoint
        """Login endpoint path (e.g., '/login') on the same server as the API.

        Mutually exclusive with external_endpoint_url.
        """

        self.external_endpoint_url = external_endpoint_url
        """Full URL if the login endpoint is on a different server.

        Mutually exclusive with endpoint.
        """

        self.payload_raw = payload_raw
        """Raw payload string to send in the login request.

        Mutually exclusive with payload_user_pwd.
        """

        self.payload_user_pwd = payload_user_pwd
        """Structured username/password payload.

        Will be automatically formatted based on content_type.
        Mutually exclusive with payload_raw.
        """

        self.headers = [] if headers is None else headers
        """HTTP headers to include in the login request."""

        self.content_type = content_type
        """Content-Type for the login request (e.g., 'application/json')."""

        self.token = token
        """Token extraction and injection configuration.

        Used for token-based auth. Mutually exclusive with expect_cookies=True.
        """

        self.expect_cookies = expect_cookies
        """Whether to expect and use cookies from the login response.

        If True, cookies from login will be used in subsequent requests.
        Mutually exclusive with token configuration.
        """


class AuthenticationInfo:
    """Authentication configuration for a single user or auth scenario.

    Supports two authentication patterns:
    1. Static authentication (fixed_headers) - for API keys, pre-set tokens
    2. Dynamic authentication (login_endpoint_auth) - for login-based flows
    """

    __slots__ = ("name", "require_mock_handling", "fixed_headers", "login_endpoint_auth")

    def __init__(
        self,
        name: str,
        require_mock_handling: bool | None = None,
        fixed_headers: list[Header] | None = None,
        login_endpoint_auth: LoginEndpoint | None = None,
    ) -> None:
        self.name = name
        """Unique identifier for this authentication configuration."""

        self.require_mock_handling = require_mock_handling
        """Whether authentication requires mocking external service responses.

        Only applicable for white-box testing scenarios.
        """

        self.fixed_headers = [] if fixed_headers is None else fixed_headers
        """Static authentication headers (e.g., API keys, basic auth).

        Used when auth info is static and doesn't require a login flow.
        """

        self.login_endpoint_auth = login_endpoint_auth
        """Dynamic authentication via login endpoint.

        Used when authentication requires calling a login endpoint first.
        """


class AuthDocument:
    """Root document for WFC authentication configuration.

    Supports multiple authentication configurations with an optional template
    to reduce duplication across entries.
    """

    __slots__ = ("auth", "schema_version", "auth_template", "configs")

    def __init__(
        self,
        auth: list[AuthenticationInfo],
        schema_version: str | None = None,
        auth_template: AuthenticationInfo | None = None,
        configs: dict[str, str] | None = None,
    ) -> None:
        self.auth = auth
        """List of authentication configurations."""

        self.schema_version = schema_version
        """WFC schema version for this document."""

        self.auth_template = auth_template
        """Template applied to all auth entries that don't override fields.

        Used to avoid duplication when multiple auth configs share common settings.
        """

        self.configs = configs
        """Optional custom configuration key-value pairs."""
