"""Wire ``[auth.wfc]`` configuration into a loaded schema's auth storage."""

from __future__ import annotations

from typing import TYPE_CHECKING

from schemathesis.auths import AuthProvider, CachingAuthProvider

from .converter import select_user, wfc_to_auth_provider
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
    if selected is None and len(entries) > 1:
        # No user named: work through the document rather than spending the run on the first entry.
        schema.auth.providers.append(
            EscalatingAuthProvider([_build(entry, config) for entry in entries], [entry.name for entry in entries])
        )
        return
    schema.auth.providers.append(_build(select_user(entries, selected), config))
