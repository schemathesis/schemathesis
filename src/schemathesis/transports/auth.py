from __future__ import annotations
from typing import Optional, Union, TYPE_CHECKING

from ..types import RawAuth

if TYPE_CHECKING:
    from requests.auth import HTTPDigestAuth


def get_requests_auth(auth: Optional[RawAuth], auth_type: Optional[str]) -> Optional[Union[HTTPDigestAuth, RawAuth]]:
    from requests.auth import HTTPDigestAuth

    if auth and auth_type == "digest":
        return HTTPDigestAuth(*auth)
    return auth
