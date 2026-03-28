"""OpenAPI-specific authentication providers."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import requests

from schemathesis.core.errors import AuthenticationError
from schemathesis.core.transforms import UNRESOLVABLE, resolve_pointer
from schemathesis.schemas import get_full_path

if TYPE_CHECKING:
    from schemathesis.auths import AuthContext
    from schemathesis.generation.case import Case


@dataclass
class ApiKeyAuthProvider:
    """Auth provider for OpenAPI API Key authentication.

    Inserts the configured API key value into headers, query parameters, or cookies
    based on the OpenAPI security scheme definition.
    """

    value: str
    """The API key value to use."""
    name: str
    """The parameter name."""
    location: str
    """Where to place the key: 'header', 'query', or 'cookie'."""

    __slots__ = ("value", "name", "location")

    def get(self, _: Case, __: AuthContext) -> str:
        """Return the configured API key value."""
        return self.value

    def set(self, case: Case, data: str, __: AuthContext) -> None:
        """Apply API key to the appropriate location in the test case."""
        if self.location == "header":
            case.headers[self.name] = data
        elif self.location == "query":
            case.query[self.name] = data
        elif self.location == "cookie":
            case.cookies[self.name] = data


@dataclass
class HttpBasicAuthProvider:
    """Auth provider for HTTP Basic authentication."""

    username: str
    """The username for basic auth."""
    password: str
    """The password for basic auth."""

    __slots__ = ("username", "password")

    def get(self, _: Case, __: AuthContext) -> tuple[str, str]:
        return self.username, self.password

    def set(self, case: Case, data: tuple[str, str], __: AuthContext) -> None:
        from requests.auth import _basic_auth_str

        case.headers["Authorization"] = _basic_auth_str(*data)


@dataclass
class HttpBearerAuthProvider:
    """Auth provider for HTTP Bearer token authentication.

    Sets the Authorization header with Bearer scheme.
    """

    bearer: str
    """The bearer token value."""

    __slots__ = ("bearer",)

    def get(self, _: Case, __: AuthContext) -> str:
        """Return the configured bearer token."""
        return self.bearer

    def set(self, case: Case, data: str, __: AuthContext) -> None:
        """Apply bearer token to Authorization header."""
        case.headers["Authorization"] = f"Bearer {data}"


@dataclass
class DynamicTokenAuthProvider:
    """Auth provider that fetches a token from an endpoint at test time."""

    path: str
    method: str
    payload: dict[str, str] | None
    extract_from: str
    extract_selector: str
    _applier: HttpBearerAuthProvider | ApiKeyAuthProvider

    __slots__ = ("path", "method", "payload", "extract_from", "extract_selector", "_applier")

    def get(self, case: Case, ctx: AuthContext) -> str:
        from schemathesis.transport import is_asgi_app

        app = ctx.app
        if app is None:
            return self._fetch_http(ctx)
        if is_asgi_app(app):
            return self._fetch_asgi(app, ctx)
        return self._fetch_wsgi(app, ctx)

    def _fetch_http(self, ctx: AuthContext) -> str:
        url = get_full_path(ctx.operation.schema.get_base_url() + "/", self.path)
        timeout = ctx.operation.schema.config.request_timeout_for(operation=ctx.operation)
        try:
            response = requests.request(self.method, url, json=self.payload, timeout=timeout)
        except requests.exceptions.RequestException as exc:
            raise AuthenticationError(
                "DynamicTokenAuthProvider",
                "get",
                f"Connection to auth endpoint failed: {exc}",
            ) from exc
        return self._extract_token(
            status_code=response.status_code,
            text=response.text,
            get_json=response.json,
            headers=response.headers,
        )

    def _fetch_wsgi(self, app: Any, ctx: AuthContext) -> str:
        from schemathesis.python.wsgi import get_client

        try:
            client = get_client(app)
            response = client.open(self.path, method=self.method.upper(), json=self.payload)
        except Exception as exc:
            raise AuthenticationError(
                "DynamicTokenAuthProvider",
                "get",
                f"WSGI auth request failed: {exc}",
            ) from exc
        return self._extract_token(
            status_code=response.status_code,
            text=response.get_data(as_text=True),
            get_json=lambda: response.get_json(force=True, silent=False),
            headers=response.headers,
        )

    def _fetch_asgi(self, app: Any, ctx: AuthContext) -> str:
        from schemathesis.python.asgi import get_client

        timeout = ctx.operation.schema.config.request_timeout_for(operation=ctx.operation)
        try:
            with get_client(app) as client:
                response = client.request(self.method.upper(), self.path, json=self.payload, timeout=timeout)
        except Exception as exc:
            raise AuthenticationError(
                "DynamicTokenAuthProvider",
                "get",
                f"ASGI auth request failed: {exc}",
            ) from exc
        return self._extract_token(
            status_code=response.status_code,
            text=response.text,
            get_json=response.json,
            headers=response.headers,
        )

    def _extract_token(
        self,
        status_code: int,
        text: str,
        get_json: Callable[[], Any],
        headers: Mapping[str, str],
    ) -> str:
        if status_code >= 400:
            raise AuthenticationError(
                "DynamicTokenAuthProvider",
                "get",
                f"Auth endpoint returned {status_code}: {text!r}",
            )
        if self.extract_from == "body":
            try:
                body = get_json()
            except ValueError as exc:
                raise AuthenticationError(
                    "DynamicTokenAuthProvider",
                    "get",
                    f"Auth endpoint returned non-JSON body: {text!r}",
                ) from exc
            raw = resolve_pointer(body, self.extract_selector)
            if raw is UNRESOLVABLE:
                raise AuthenticationError(
                    "DynamicTokenAuthProvider",
                    "get",
                    f"JSON Pointer {self.extract_selector!r} not found in auth response body: {body!r}",
                )
            if not isinstance(raw, str):
                raise AuthenticationError(
                    "DynamicTokenAuthProvider",
                    "get",
                    f"Expected a string at {self.extract_selector!r}, got {type(raw).__name__}: {raw!r}",
                )
            return raw
        result = headers.get(self.extract_selector)
        if result is None:
            present = list(headers.keys())
            raise AuthenticationError(
                "DynamicTokenAuthProvider",
                "get",
                f"Header {self.extract_selector!r} not found in auth response. Present headers: {present!r}",
            )
        return result

    def set(self, case: Case, data: str, ctx: AuthContext) -> None:
        self._applier.set(case, data, ctx)
