"""Convert WFC authentication configuration to Schemathesis auth providers."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .errors import WFCValidationError
from .providers import FixedHeaderAuthProvider, LoginEndpointAuthProvider

if TYPE_CHECKING:
    from .auth import AuthenticationInfo


def wfc_to_auth_provider(auth_info: AuthenticationInfo) -> FixedHeaderAuthProvider | LoginEndpointAuthProvider:
    """Build an auth provider from a validated WFC auth entry."""
    if auth_info.fixed_headers:
        return FixedHeaderAuthProvider(headers=auth_info.fixed_headers)
    assert auth_info.login_endpoint_auth is not None
    return LoginEndpointAuthProvider(config=auth_info.login_endpoint_auth)


def select_user(auth_list: list[AuthenticationInfo], user: str | None) -> AuthenticationInfo:
    """Pick the auth entry named `user`, or the only entry when there is just one."""
    if len(auth_list) == 1:
        return auth_list[0]

    available = ", ".join(f"'{a.name}'" for a in auth_list)
    if user is None:
        raise WFCValidationError(
            f"WFC auth document has {len(auth_list)} entries. "
            f"Please specify which user to use with the 'user' config option.\n"
            f"Available users: {available}"
        )
    for auth_info in auth_list:
        if auth_info.name == user:
            return auth_info
    raise WFCValidationError(f"User '{user}' not found in WFC auth document. Available users: {available}")
