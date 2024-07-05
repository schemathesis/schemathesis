from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..constants import USER_AGENT
from ..types import RawAuth

if TYPE_CHECKING:
    from requests.auth import HTTPDigestAuth


def get_requests_auth(auth: RawAuth | None, auth_type: str | None) -> HTTPDigestAuth | RawAuth | None:
    from requests.auth import HTTPDigestAuth

    if auth and auth_type == "digest":
        return HTTPDigestAuth(*auth)
    return auth


def prepare_wsgi_headers(headers: dict[str, Any] | None, auth: RawAuth | None, auth_type: str | None) -> dict[str, Any]:
    headers = headers or {}
    if "user-agent" not in {header.lower() for header in headers}:
        headers["User-Agent"] = USER_AGENT
    wsgi_auth = get_wsgi_auth(auth, auth_type)
    if wsgi_auth:
        headers["Authorization"] = wsgi_auth
    return headers


def get_wsgi_auth(auth: RawAuth | None, auth_type: str | None) -> str | None:
    from requests.auth import _basic_auth_str

    if auth:
        if auth_type == "digest":
            raise ValueError("Digest auth is not supported for WSGI apps")
        return _basic_auth_str(*auth)
    return None
