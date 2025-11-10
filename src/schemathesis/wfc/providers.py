"""WFC authentication providers for Schemathesis."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from http.cookies import SimpleCookie
from typing import TYPE_CHECKING, Any

import requests

from schemathesis.python import asgi, wsgi
from schemathesis.schemas import get_full_path
from schemathesis.transport import is_asgi_app

from .errors import WFCLoginError
from .payloads import format_login_payload
from .tokens import extract_token_from_body, extract_token_from_header, format_token

if TYPE_CHECKING:
    from schemathesis.auths import AuthContext
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
class _LoginResponse:
    status_code: int
    text: str
    get_json: Callable[[], Any]
    headers: Mapping[str, str]
    cookies: dict[str, str]


@dataclass(slots=True)
class LoginEndpointAuthProvider:
    """Call a login endpoint (over the run's transport) to obtain a token or cookies for later requests."""

    config: LoginEndpoint

    def get(self, case: Case, context: AuthContext) -> dict[str, str] | str:
        app = context.app
        if app is None or self.config.external_endpoint_url is not None:
            response = self._fetch_http(context)
        elif is_asgi_app(app):
            response = self._fetch_asgi(app, context)
        else:
            response = self._fetch_wsgi(app, context)
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
        assert self.config.endpoint is not None
        return get_full_path(base + "/", self.config.endpoint)

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

    def _fetch_http(self, context: AuthContext) -> _LoginResponse:
        url = self._url(context)
        config = context.operation.schema.config
        body, headers = self._body_and_headers()
        try:
            response = requests.request(
                self.config.verb,
                url,
                data=body,
                headers=headers,
                timeout=config.request_timeout_for(operation=context.operation),
                verify=config.tls_verify_for(operation=context.operation),
                cert=config.request_cert_for(operation=context.operation),
            )
        except requests.exceptions.RequestException as exc:
            raise WFCLoginError(f"Login endpoint call failed: {exc}") from exc
        return _LoginResponse(
            status_code=response.status_code,
            text=response.text,
            get_json=response.json,
            headers=response.headers,
            cookies=response.cookies.get_dict(),
        )

    def _fetch_wsgi(self, app: Any, context: AuthContext) -> _LoginResponse:
        body, headers = self._body_and_headers()
        try:
            client = wsgi.get_client(app)
            response = client.open(self.config.endpoint, method=self.config.verb, data=body, headers=headers)
        except Exception as exc:
            raise WFCLoginError(f"WSGI login request failed: {exc}") from exc
        return _LoginResponse(
            status_code=response.status_code,
            text=response.get_data(as_text=True),
            get_json=lambda: response.get_json(force=True, silent=False),
            headers=response.headers,
            cookies=_parse_set_cookie(response.headers.get_all("Set-Cookie")),
        )

    def _fetch_asgi(self, app: Any, context: AuthContext) -> _LoginResponse:
        timeout = context.operation.schema.config.request_timeout_for(operation=context.operation)
        body, headers = self._body_and_headers()
        try:
            with asgi.get_client(app) as client:
                response = client.request(
                    self.config.verb, self.config.endpoint, data=body, headers=headers, timeout=timeout
                )
        except Exception as exc:
            raise WFCLoginError(f"ASGI login request failed: {exc}") from exc
        return _LoginResponse(
            status_code=response.status_code,
            text=response.text,
            get_json=response.json,
            headers=response.headers,
            cookies=dict(response.cookies),
        )

    def _auth_data(self, response: _LoginResponse) -> dict[str, str] | str:
        if response.status_code not in (200, 201):
            raise WFCLoginError(
                f"Login endpoint returned status {response.status_code}. Expected 200 or 201. "
                f"Response: {response.text[:200]}"
            )
        if self.config.expect_cookies:
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


def _parse_set_cookie(headers: list[str]) -> dict[str, str]:
    jar: dict[str, str] = {}
    for header in headers:
        cookie: SimpleCookie = SimpleCookie()
        cookie.load(header)
        for key, morsel in cookie.items():
            jar[key] = morsel.value
    return jar
