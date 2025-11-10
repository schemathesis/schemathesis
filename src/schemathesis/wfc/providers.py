"""WFC authentication providers for Schemathesis."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from schemathesis.auths import AuthContext
    from schemathesis.generation.case import Case

    from .auth import Header, LoginEndpoint


@dataclass
class FixedHeaderAuthProvider:
    """Auth provider for WFC fixed headers (static credentials).

    This provider applies pre-configured headers to every request,
    suitable for API keys, static tokens, or other fixed authentication.
    """

    headers: list[Header]

    __slots__ = ("headers",)

    def get(self, case: Case, context: AuthContext) -> list[tuple[str, str]]:
        """Return the list of headers as tuples.

        Args:
            case: Generated test case
            context: Authentication context

        Returns:
            List of (name, value) tuples for headers

        """
        return [(h.name, h.value) for h in self.headers]

    def set(self, case: Case, data: list[tuple[str, str]], context: AuthContext) -> None:
        """Apply fixed headers to the test case.

        Args:
            case: Test case to modify
            data: List of (name, value) tuples from get()
            context: Authentication context

        """
        for name, value in data:
            case.headers[name] = value


@dataclass
class LoginEndpointAuthProvider:
    """Auth provider for WFC login endpoint authentication (dynamic credentials).

    This provider calls a login endpoint to obtain authentication credentials
    (either a token or cookies) and applies them to subsequent requests.

    The login is performed lazily on first auth request and cached.
    """

    config: LoginEndpoint
    base_url: str

    __slots__ = ("config", "base_url")

    def get(self, case: Case, context: AuthContext) -> dict | str:
        """Execute login and return authentication data.

        This will be called by CachingAuthProvider wrapper, which handles
        caching the result for the configured refresh interval.

        Args:
            case: Generated test case
            context: Authentication context

        Returns:
            Authentication data (token string or cookies dict)

        Raises:
            WFCLoginError: If login endpoint call fails
            WFCTokenExtractionError: If token extraction fails

        """
        from .errors import WFCLoginError
        from .payloads import format_login_payload
        from .tokens import extract_token_from_body, extract_token_from_header, format_token

        # Build login request URL
        if self.config.endpoint is not None:
            # Relative endpoint on same server
            login_url = self.base_url.rstrip("/") + "/" + self.config.endpoint.lstrip("/")
        else:
            # External absolute URL
            assert self.config.external_endpoint_url is not None
            login_url = self.config.external_endpoint_url

        # Format payload
        try:
            body, extra_headers = format_login_payload(self.config)
        except ValueError as exc:
            raise WFCLoginError(f"Failed to format login payload: {exc}") from exc

        # Build login request headers
        from requests.structures import CaseInsensitiveDict

        login_headers: CaseInsensitiveDict = CaseInsensitiveDict()

        # Add content type if specified
        if self.config.content_type is not None:
            login_headers["Content-Type"] = self.config.content_type

        # Add extra headers from payload formatting
        login_headers.update(extra_headers)

        # Add configured headers
        if self.config.headers:
            for header in self.config.headers:
                login_headers[header.name] = header.value

        # Create a minimal case for the login request
        # We reuse the Case structure to leverage existing transport
        from schemathesis.core import NOT_SET
        from schemathesis.generation.case import Case as CaseClass

        login_case = CaseClass(
            operation=case.operation,
            method=self.config.verb,
            path=login_url,
            path_parameters={},
            headers=login_headers,
            cookies={},
            query={},
            body=body if body is not None else NOT_SET,
        )

        # Execute login request using case.call()
        try:
            response = login_case.call()
        except Exception as exc:
            raise WFCLoginError(f"Login endpoint call failed: {exc}") from exc

        # Check response status
        if response.status_code not in (200, 201):
            raise WFCLoginError(
                f"Login endpoint returned status {response.status_code}. "
                f"Expected 200 or 201. Response: {response.text[:200] if hasattr(response, 'text') else ''}"
            )

        # Handle cookie-based auth
        if self.config.expect_cookies:
            # Extract cookies from response
            if hasattr(response, "cookies"):
                # requests.Response
                cookies_dict = dict(response.cookies)
            elif hasattr(response, "cookies") and hasattr(response.cookies, "items"):
                # httpx.Response
                cookies_dict = dict(response.cookies.items())
            else:
                raise WFCLoginError("Cannot extract cookies from response")

            if not cookies_dict:
                raise WFCLoginError("No cookies returned from login endpoint")

            return cookies_dict

        # Handle token-based auth
        if self.config.token is not None:
            token_config = self.config.token

            # Extract token from response
            if token_config.extract_from == "body":
                # Get response body
                if hasattr(response, "json"):
                    # Try to get JSON directly
                    try:
                        response_body = response.json()
                    except Exception:
                        response_body = response.text if hasattr(response, "text") else str(response.content)
                else:
                    response_body = response.text if hasattr(response, "text") else str(response.content)

                raw_token = extract_token_from_body(response_body, token_config.extract_selector)
            else:
                # Extract from header - handle both requests and httpx formats
                if hasattr(response, "headers"):
                    # Convert to simple dict[str, str]
                    # httpx returns dict[str, list[str]], requests returns dict-like with str values
                    headers_dict = {}
                    for key, value in response.headers.items():
                        # Handle both formats: httpx (list) and requests (str)
                        if isinstance(value, list):
                            headers_dict[key] = value[0] if value else ""
                        else:
                            headers_dict[key] = str(value)  # type: ignore[unreachable]
                else:
                    headers_dict = {}
                raw_token = extract_token_from_header(headers_dict, token_config.extract_selector)

            # Format token
            formatted_token = format_token(raw_token, token_config.send_format)

            return formatted_token

        # No token or cookies configured - shouldn't reach here due to validation
        raise WFCLoginError("Login endpoint auth configured but no token or cookies expected")

    def set(self, case: Case, data: dict | str, context: AuthContext) -> None:
        """Apply authentication data to the test case.

        Args:
            case: Test case to modify
            data: Authentication data from get() (token string or cookies dict)
            context: Authentication context

        """
        if isinstance(data, dict):
            # Cookies
            for name, value in data.items():
                case.cookies[name] = value
        else:
            # Token
            assert self.config.token is not None
            token_config = self.config.token

            if token_config.send_in == "header":
                case.headers[token_config.send_name] = data
            elif token_config.send_in == "query":
                case.query[token_config.send_name] = data
