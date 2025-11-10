"""Wire ``[auth.wfc]`` configuration into a loaded schema's auth storage."""

from __future__ import annotations

from typing import TYPE_CHECKING

from schemathesis.auths import CachingAuthProvider

from .converter import select_user, wfc_to_auth_provider
from .loader import load_from_file
from .providers import LoginEndpointAuthProvider

if TYPE_CHECKING:
    from schemathesis.config._auth import WFCAuthConfig
    from schemathesis.schemas import BaseSchema


def register_wfc_auth(schema: BaseSchema, config: WFCAuthConfig) -> None:
    """Load the configured WFC file and register its auth provider on the schema."""
    auth_info = select_user(load_from_file(config.path), config.user)
    provider = wfc_to_auth_provider(auth_info)
    if isinstance(provider, LoginEndpointAuthProvider):
        schema.auth.providers.append(CachingAuthProvider(provider, refresh_interval=config.refresh_interval))
    else:
        schema.auth.providers.append(provider)
