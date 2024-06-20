from __future__ import annotations

from typing import TYPE_CHECKING

from ..types import RawAuth

if TYPE_CHECKING:
    from requests.auth import HTTPDigestAuth


def get_requests_auth(auth: RawAuth | None, auth_type: str | None) -> HTTPDigestAuth | RawAuth | None:
    from requests.auth import HTTPDigestAuth

    if auth and auth_type == "digest":
        return HTTPDigestAuth(*auth)
    return auth
