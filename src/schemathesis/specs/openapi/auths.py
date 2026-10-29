"""OpenAPI-specific authentication providers."""

from __future__ import annotations

import json
import textwrap
from dataclasses import dataclass
from typing import TYPE_CHECKING
from urllib.parse import urlencode

import requests

from schemathesis.auths import InProcessAuthRequestError, send_auth_request
from schemathesis.core.errors import AuthenticationError, MalformedMediaType
from schemathesis.core.failures import reason_phrase
from schemathesis.core.media_types import is_form_urlencoded, is_json
from schemathesis.core.output import prepare_response_payload
from schemathesis.core.transforms import UNRESOLVABLE, resolve_pointer
from schemathesis.core.transport import AUTH_REJECTION_STATUSES
from schemathesis.schemas import get_full_path

if TYPE_CHECKING:
    from schemathesis.auths import AuthContext, AuthEndpointResponse
    from schemathesis.config import OutputConfig
    from schemathesis.generation.case import Case


def _response_block(status_code: int, text: str, config: OutputConfig) -> str:
    reason = reason_phrase(status_code)
    payload = prepare_response_payload(text, config=config)
    return f"\n[{status_code}] {reason}:\n" + textwrap.indent(f"\n`{payload}`", "    ")


@dataclass(slots=True)
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


@dataclass(slots=True)
class HttpBasicAuthProvider:
    """Auth provider for HTTP Basic authentication."""

    username: str
    """The username for basic auth."""
    password: str
    """The password for basic auth."""

    def get(self, _: Case, __: AuthContext) -> tuple[str, str]:
        return self.username, self.password

    def set(self, case: Case, data: tuple[str, str], __: AuthContext) -> None:
        from requests.auth import _basic_auth_str

        case.headers["Authorization"] = _basic_auth_str(*data)


@dataclass(slots=True)
class HttpBearerAuthProvider:
    """Auth provider for HTTP Bearer token authentication.

    Sets the Authorization header with Bearer scheme.
    """

    bearer: str
    """The bearer token value."""

    def get(self, _: Case, __: AuthContext) -> str:
        """Return the configured bearer token."""
        return self.bearer

    def set(self, case: Case, data: str, __: AuthContext) -> None:
        """Apply bearer token to Authorization header."""
        case.headers["Authorization"] = f"Bearer {data}"


@dataclass(slots=True)
class DynamicTokenAuthProvider:
    """Auth provider that fetches a token from an endpoint at test time."""

    path: str
    method: str
    payload: dict[str, str] | None
    extract_from: str
    extract_selector: str
    _applier: HttpBearerAuthProvider | ApiKeyAuthProvider
    payload_content_type: str = "application/json"

    def get(self, case: Case, ctx: AuthContext) -> str:
        config = ctx.operation.schema.config
        body, headers = self._build_body()
        try:
            response = send_auth_request(
                ctx.app,
                method=self.method.upper(),
                url=get_full_path(ctx.operation.schema.get_base_url() + "/", self.path),
                path=self.path,
                body=body,
                headers=headers,
                settings=config.request_settings_for(operation=ctx.operation),
                allow_redirects=True,
            )
        except InProcessAuthRequestError as exc:
            raise AuthenticationError(
                "DynamicTokenAuthProvider",
                "get",
                f"{exc.interface} auth request failed: {exc.cause}",
            ) from exc.cause
        # OSError: requests surfaces TLS setup failures (e.g. a missing client certificate file) as raw OSError.
        except (requests.exceptions.RequestException, OSError) as exc:
            raise AuthenticationError(
                "DynamicTokenAuthProvider",
                "get",
                f"Connection to auth endpoint failed: {exc}",
            ) from exc
        return self._extract_token(response, config.output)

    def _build_body(self) -> tuple[bytes | None, dict[str, str]]:
        if self.payload is None:
            return None, {}
        try:
            form = is_form_urlencoded(self.payload_content_type)
            json_like = is_json(self.payload_content_type)
        except MalformedMediaType as exc:
            raise AuthenticationError(
                "DynamicTokenAuthProvider",
                "get",
                f"Malformed payload_content_type {self.payload_content_type!r}: {exc}",
            ) from exc
        if form:
            body = urlencode(self.payload).encode("utf-8")
        elif json_like:
            body = json.dumps(self.payload).encode("utf-8")
        else:
            raise AuthenticationError(
                "DynamicTokenAuthProvider",
                "get",
                f"Unsupported payload_content_type {self.payload_content_type!r}. "
                "Supported: 'application/json' (and any '+json' variant) "
                "and 'application/x-www-form-urlencoded'.",
            )
        return body, {"Content-Type": self.payload_content_type}

    def _extract_token(self, response: AuthEndpointResponse, output_config: OutputConfig) -> str:
        status_code = response.status_code
        text = response.text
        if status_code in AUTH_REJECTION_STATUSES:
            raise AuthenticationError(
                "DynamicTokenAuthProvider",
                "get",
                "Auth endpoint rejected the credentials. Check the configured auth credentials.\n"
                + _response_block(status_code, text, output_config),
                include_common_causes=False,
            )
        if status_code >= 400:
            raise AuthenticationError(
                "DynamicTokenAuthProvider",
                "get",
                "Auth endpoint returned an error response.\n" + _response_block(status_code, text, output_config),
            )
        if self.extract_from == "body":
            try:
                body = response.get_json()
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
        if self.extract_from == "cookie":
            value = response.cookies.get(self.extract_selector)
            if value is None:
                raise AuthenticationError(
                    "DynamicTokenAuthProvider",
                    "get",
                    f"Cookie {self.extract_selector!r} not found in auth response. Present cookies: {list(response.cookies)!r}",
                )
            return value
        result = response.headers.get(self.extract_selector)
        if result is None:
            present = list(response.headers.keys())
            raise AuthenticationError(
                "DynamicTokenAuthProvider",
                "get",
                f"Header {self.extract_selector!r} not found in auth response. Present headers: {present!r}",
            )
        return result

    def set(self, case: Case, data: str, ctx: AuthContext) -> None:
        self._applier.set(case, data, ctx)
