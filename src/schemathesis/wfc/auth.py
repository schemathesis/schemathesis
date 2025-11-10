"""WFC authentication data structures (https://github.com/WebFuzzing/Commons)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

HttpVerb = Literal["POST", "GET", "PATCH", "DELETE", "PUT"]


@dataclass(slots=True)
class Header:
    name: str
    value: str


@dataclass(slots=True)
class PayloadUsernamePassword:
    username: str
    password: str
    username_field: str
    password_field: str


@dataclass(slots=True)
class TokenHandling:
    """How to extract a token from a login response and inject it into later requests."""

    extract_from: Literal["body", "header"]
    # JSON Pointer (RFC 6901) for body extraction, or header name for header extraction.
    extract_selector: str
    send_in: Literal["header", "query"]
    send_name: str
    # Template with a `{token}` placeholder, e.g. "Bearer {token}".
    send_template: str = "{token}"


@dataclass(slots=True)
class LoginEndpoint:
    """A login endpoint that returns credentials (a token or cookies)."""

    verb: HttpVerb
    # Relative path on the API server; mutually exclusive with `external_endpoint_url`.
    endpoint: str | None = None
    external_endpoint_url: str | None = None
    # Raw request body; mutually exclusive with `credentials`.
    payload_raw: str | None = None
    credentials: PayloadUsernamePassword | None = None
    headers: list[Header] = field(default_factory=list)
    content_type: str | None = None
    # Token handling; mutually exclusive with `expect_cookies`.
    token: TokenHandling | None = None
    expect_cookies: bool | None = None


@dataclass(slots=True)
class AuthenticationInfo:
    """Authentication for a single user: static `fixed_headers` or a `login_endpoint_auth` flow."""

    name: str
    fixed_headers: list[Header] = field(default_factory=list)
    login_endpoint_auth: LoginEndpoint | None = None
