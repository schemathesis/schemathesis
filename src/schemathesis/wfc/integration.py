"""Wire ``[auth.wfc]`` configuration into a loaded schema's auth storage."""

from __future__ import annotations

from typing import TYPE_CHECKING

from schemathesis.auths import AuthProvider, CachingAuthProvider

from .converter import select_user, wfc_to_auth_provider
from .errors import WFCValidationError
from .escalation import EscalatingAuthProvider
from .loader import load_from_file
from .providers import LoginEndpointAuthProvider

if TYPE_CHECKING:
    from schemathesis.config._auth import WFCAuthConfig
    from schemathesis.schemas import BaseSchema

    from .auth import AuthenticationInfo


def _build(auth_info: AuthenticationInfo, config: WFCAuthConfig) -> AuthProvider:
    provider = wfc_to_auth_provider(auth_info)
    if isinstance(provider, LoginEndpointAuthProvider):
        return CachingAuthProvider(provider, refresh_interval=config.refresh_interval)
    return provider


def register_wfc_auth(schema: BaseSchema, config: WFCAuthConfig, user: str | None = None) -> None:
    """Load the configured WFC file and register its auth provider on the schema."""
    entries = load_from_file(config.path)
    selected = user or config.user
    names = [entry.name for entry in entries]
    peers = tuple(config.peers or ())
    for peer in peers:
        if peer not in names:
            available = ", ".join(f"'{name}'" for name in names)
            raise WFCValidationError(f"Peer '{peer}' not found in WFC auth document. Available users: {available}")
    if peers or (selected is None and len(entries) > 1):
        if selected is not None:
            select_user(entries, selected)
        # No user named: work through the document rather than spending the run on the first entry.
        # Peers need every identity loaded, even when one is pinned.
        schema.auth.providers.append(
            EscalatingAuthProvider([_build(entry, config) for entry in entries], names, peers=peers, pinned=selected)
        )
        return
    schema.auth.providers.append(_build(select_user(entries, selected), config))
