"""Overriding the authority of `externalEndpointURL` values in a WFC document."""

from __future__ import annotations

from typing import TYPE_CHECKING
from urllib.parse import urlsplit, urlunsplit

if TYPE_CHECKING:
    from .auth import AuthenticationInfo

_INVALID = (
    "Invalid external URL override: {value!r}. Expected 'HOST:PORT' (for example, '127.0.0.1:8083') "
    "or a URL without a path (for example, 'http://127.0.0.1:8083')"
)


def parse_override(value: str) -> tuple[str | None, str]:
    """Split an override into the scheme it forces, if any, and the authority replacing the original one."""
    raw = value.strip()
    has_scheme = "://" in raw
    parts = urlsplit(raw if has_scheme else f"//{raw}")
    if parts.path not in ("", "/") or parts.query or parts.fragment:
        raise ValueError(_INVALID.format(value=value))
    try:
        port = parts.port
    except ValueError:
        raise ValueError(_INVALID.format(value=value)) from None
    # Without a scheme there is nothing to imply the port, so it has to be spelled out.
    if not parts.hostname or (port is None and not has_scheme):
        raise ValueError(_INVALID.format(value=value))
    return (parts.scheme if has_scheme else None), parts.netloc


def override_external_urls(entries: list[AuthenticationInfo], value: str) -> None:
    """Point every `externalEndpointURL` at another host, keeping its path and query."""
    scheme, authority = parse_override(value)
    for entry in entries:
        login = entry.login_endpoint_auth
        if login is None or login.external_endpoint_url is None:
            continue
        parts = urlsplit(login.external_endpoint_url)
        login.external_endpoint_url = urlunsplit(
            (scheme or parts.scheme, authority, parts.path, parts.query, parts.fragment)
        )
