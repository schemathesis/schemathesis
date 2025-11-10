"""Payload formatting for WFC login requests."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING
from urllib.parse import urlencode

from schemathesis.core import media_types

if TYPE_CHECKING:
    from .auth import LoginEndpoint, PayloadUsernamePassword


def format_login_payload(config: LoginEndpoint) -> tuple[str | bytes | None, dict[str, str]]:
    """Build the login request body and any derived headers from the login config."""
    if config.payload_raw is not None:
        return config.payload_raw, {}
    if config.credentials is not None:
        return _format_username_password_payload(config.credentials, config.content_type)
    return None, {}


def _format_username_password_payload(
    credentials: PayloadUsernamePassword, content_type: str | None
) -> tuple[str, dict[str, str]]:
    content_type = content_type or "application/json"
    fields = {credentials.username_field: credentials.username, credentials.password_field: credentials.password}
    if media_types.is_json(content_type):
        return json.dumps(fields), {}
    if media_types.is_form_urlencoded(content_type):
        return urlencode(fields), {}
    raise ValueError(
        f"Unsupported content type for username/password payload: '{content_type}'. "
        f"Supported types: 'application/json', 'application/x-www-form-urlencoded'"
    )
