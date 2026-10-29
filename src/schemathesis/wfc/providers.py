"""WFC authentication providers for Schemathesis."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING
from urllib.parse import urljoin, urlsplit

import requests

from schemathesis.auths import InProcessAuthRequestError, send_auth_request

from .errors import WFCLoginError
from .payloads import format_login_payload
from .tokens import extract_token_from_body, extract_token_from_header, format_token

if TYPE_CHECKING:
    from schemathesis.auths import AuthContext, AuthEndpointResponse
    from schemathesis.generation.case import Case

    from .auth import Header, LoginEndpoint


@dataclass(slots=True)
class FixedHeaderAuthProvider:
    """Apply static headers (API keys, pre-set tokens) to every request."""

    headers: list[Header]

    def get(self, case: Case, context: AuthContext) -> list[tuple[str, str]]:
        return [(h.name, h.value) for h in self.headers]

    def set(self, case: Case, data: list[tuple[str, str]], context: AuthContext) -> None:
        for name, value in data:
            case.headers[name] = value


@dataclass(slots=True)
class LoginEndpointAuthProvider:
    """Call a login endpoint (over the run's transport) to obtain a token or cookies for later requests."""

    config: LoginEndpoint

    def get(self, case: Case, context: AuthContext) -> dict[str, str] | str:
        # An external login endpoint is reached over the network even when the API runs in-process.
        app = context.app if self.config.external_endpoint_url is None else None
        url = self._url(context)
        body, headers = self._body_and_headers()
        try:
            response = send_auth_request(
                app,
                method=self.config.verb,
                url=url,
                # Only an in-process app is called by path, and then `endpoint` is always set.
                path=self.config.endpoint or "",
                body=body,
                headers=headers,
                settings=context.operation.schema.config.request_settings_for(operation=context.operation),
                # A session cookie rides on the redirect itself, so following it would discard the sign-in.
                allow_redirects=False,
            )
        except InProcessAuthRequestError as exc:
            raise WFCLoginError(f"{exc.interface} login request failed: {exc.cause}") from exc.cause
        except requests.exceptions.RequestException as exc:
            raise WFCLoginError(f"Login endpoint call failed: {exc}") from exc
        # Header values outside latin-1 and unparsable URLs fail before anything is sent.
        except ValueError as exc:
            raise WFCLoginError(f"Login endpoint call failed for {url}: {exc}") from exc
        return self._auth_data(response)

    def set(self, case: Case, data: dict[str, str] | str, context: AuthContext) -> None:
        if isinstance(data, dict):
            for name, value in data.items():
                case.cookies[name] = value
            return
        token_config = self.config.token
        assert token_config is not None
        if token_config.send_in == "header":
            case.headers[token_config.send_name] = data
        else:
            case.query[token_config.send_name] = data

    def _url(self, context: AuthContext) -> str:
        if self.config.external_endpoint_url is not None:
            return self.config.external_endpoint_url
        base = context.operation.schema.get_base_url()
        return urljoin(base, self._path())

    def _path(self) -> str:
        """Login path, relative to the server root rather than to the schema's base path."""
        if self.config.external_endpoint_url is not None:
            return urlsplit(self.config.external_endpoint_url).path
        assert self.config.endpoint is not None
        return "/" + self.config.endpoint.lstrip("/")

    def _login_bounce(self, response: AuthEndpointResponse) -> str | None:
        """Redirect target when a sign-in attempt is sent straight back to the login endpoint."""
        location = response.headers.get("Location")
        if location is None:
            return None
        path = self._path()
        # Relative targets resolve against the login path, so both sides end up comparable.
        try:
            target = urlsplit(urljoin(path, location)).path
        except ValueError:
            # A target that does not parse cannot lead back to the login endpoint.
            return None
        return location if target == path else None

    def _body_and_headers(self) -> tuple[str | bytes | None, dict[str, str]]:
        try:
            body, extra = format_login_payload(self.config)
        except ValueError as exc:
            raise WFCLoginError(f"Failed to format login payload: {exc}") from exc
        headers = dict(extra)
        if self.config.content_type is not None:
            headers["Content-Type"] = self.config.content_type
        for header in self.config.headers:
            headers[header.name] = header.value
        return body, headers

    def _auth_data(self, response: AuthEndpointResponse) -> dict[str, str] | str:
        # No token to extract means the login can only hand back a session.
        is_cookie_login = self.config.expect_cookies or self.config.token is None
        # Form-login servers commonly answer a successful sign-in with a redirect to a landing page.
        accepts_redirect = is_cookie_login and 300 <= response.status_code < 400
        bounce = self._login_bounce(response) if accepts_redirect else None
        if bounce is not None:
            raise WFCLoginError(
                f"Login endpoint redirected back to the login endpoint ({bounce}). "
                "The credentials were likely rejected."
            )
        if response.status_code not in (200, 201) and not accepts_redirect:
            expected = "200, 201 or a redirect" if is_cookie_login else "200 or 201"
            raise WFCLoginError(
                f"Login endpoint returned status {response.status_code}. Expected {expected}. "
                f"Response: {response.text[:200]}"
            )
        if is_cookie_login:
            if not response.cookies:
                raise WFCLoginError("No cookies returned from login endpoint")
            return response.cookies

        token_config = self.config.token
        assert token_config is not None
        if token_config.extract_from == "body":
            try:
                body = response.get_json()
            except ValueError as exc:
                raise WFCLoginError(f"Login response body is not valid JSON: {response.text[:200]!r}") from exc
            raw = extract_token_from_body(body, token_config.extract_selector)
        else:
            raw = extract_token_from_header(response.headers, token_config.extract_selector)
        return format_token(raw, token_config.send_template)
