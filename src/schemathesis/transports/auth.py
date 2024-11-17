from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from requests.auth import HTTPDigestAuth


def get_requests_auth(auth: tuple[str, str] | None, auth_type: str | None) -> HTTPDigestAuth | tuple[str, str] | None:
    from requests.auth import HTTPDigestAuth

    if auth and auth_type == "digest":
        return HTTPDigestAuth(*auth)
    return auth
