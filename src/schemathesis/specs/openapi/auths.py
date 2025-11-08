"""OpenAPI-specific authentication providers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

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
        return (self.username, self.password)

    def set(self, case: Case, data: tuple[str, str], __: AuthContext) -> None:
        import requests.auth

        case._auth = requests.auth.HTTPBasicAuth(*data)


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
