"""Convert WFC authentication configuration to Schemathesis auth providers."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .errors import WFCValidationError
from .providers import FixedHeaderAuthProvider, LoginEndpointAuthProvider

if TYPE_CHECKING:
    from schemathesis.auths import AuthProvider

    from .auth import AuthenticationInfo


def wfc_to_auth_provider(auth_info: AuthenticationInfo, base_url: str) -> AuthProvider:
    """Convert WFC AuthenticationInfo to Schemathesis AuthProvider.

    Args:
        auth_info: WFC auth configuration (after template merging and validation)
        base_url: Base URL for resolving relative login endpoints

    Returns:
        AuthProvider instance (FixedHeaderAuthProvider or LoginEndpointAuthProvider)

    Raises:
        WFCValidationError: If auth configuration is invalid

    """
    has_fixed = auth_info.fixed_headers is not None and len(auth_info.fixed_headers) > 0
    has_login = auth_info.login_endpoint_auth is not None

    # This should be caught by validation, but double-check
    if not has_fixed and not has_login:
        raise WFCValidationError(f"Auth '{auth_info.name}': Must specify either 'fixedHeaders' or 'loginEndpointAuth'")

    if has_fixed and has_login:
        raise WFCValidationError(f"Auth '{auth_info.name}': Cannot specify both 'fixedHeaders' and 'loginEndpointAuth'")

    if has_fixed:
        assert auth_info.fixed_headers is not None
        return FixedHeaderAuthProvider(headers=auth_info.fixed_headers)

    if has_login:
        assert auth_info.login_endpoint_auth is not None
        return LoginEndpointAuthProvider(config=auth_info.login_endpoint_auth, base_url=base_url)

    # Should never reach here
    raise WFCValidationError(f"Auth '{auth_info.name}': Invalid configuration")  # pragma: no cover


def select_user(auth_list: list[AuthenticationInfo], user: str | None) -> AuthenticationInfo:
    """Select authentication entry from list by name.

    Args:
        auth_list: List of authentication configurations
        user: User name to select, or None to auto-select if only one entry

    Returns:
        Selected AuthenticationInfo

    Raises:
        WFCValidationError: If user not found or selection is ambiguous

    """
    if len(auth_list) == 0:
        raise WFCValidationError("WFC auth document has no auth entries")

    # Auto-select if only one entry
    if len(auth_list) == 1:
        return auth_list[0]

    # Multiple entries - user must be specified
    if user is None:
        available = ", ".join(f"'{a.name}'" for a in auth_list)
        raise WFCValidationError(
            f"WFC auth document has {len(auth_list)} entries. "
            f"Please specify which user to use with 'user' config option.\n"
            f"Available users: {available}"
        )

    # Find matching user
    for auth_info in auth_list:
        if auth_info.name == user:
            return auth_info

    # User not found
    available = ", ".join(f"'{a.name}'" for a in auth_list)
    raise WFCValidationError(f"User '{user}' not found in WFC auth document. Available users: {available}")
