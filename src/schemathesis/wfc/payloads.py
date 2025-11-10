"""Payload formatting for WFC login requests."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING
from urllib.parse import urlencode

if TYPE_CHECKING:
    from .auth import LoginEndpoint, PayloadUsernamePassword


def format_login_payload(config: LoginEndpoint) -> tuple[str | bytes | None, dict[str, str]]:
    """Format login request payload based on configuration.

    Args:
        config: Login endpoint configuration

    Returns:
        Tuple of (body, additional_headers)
        - body: Request body as string/bytes, or None if no payload
        - additional_headers: Additional headers to add to the request

    Raises:
        ValueError: If payload configuration is invalid or unsupported

    """
    # If no payload specified, return None
    if config.payload_raw is None and config.payload_user_pwd is None:
        return None, {}

    # Handle raw payload
    if config.payload_raw is not None:
        # Return raw payload as-is
        # Content-Type will be set separately if specified
        return config.payload_raw, {}

    # Handle username/password payload
    if config.payload_user_pwd is not None:
        return _format_username_password_payload(config.payload_user_pwd, config.content_type)

    return None, {}


def _format_username_password_payload(
    payload: PayloadUsernamePassword, content_type: str | None
) -> tuple[str | bytes, dict[str, str]]:
    """Format username/password payload based on content type.

    Args:
        payload: Username/password payload configuration
        content_type: Content type for the request

    Returns:
        Tuple of (body, additional_headers)

    Raises:
        ValueError: If content type is unsupported

    """
    # Default to JSON if no content type specified
    if content_type is None:
        content_type = "application/json"

    # Normalize content type (remove parameters like charset)
    content_type_main = content_type.split(";")[0].strip().lower()

    if content_type_main == "application/json":
        # Format as JSON
        body_dict = {
            payload.username_field: payload.username,
            payload.password_field: payload.password,
        }
        body = json.dumps(body_dict)
        return body, {}

    elif content_type_main == "application/x-www-form-urlencoded":
        # Format as form-encoded
        form_data = {
            payload.username_field: payload.username,
            payload.password_field: payload.password,
        }
        body = urlencode(form_data)
        return body, {}

    else:
        raise ValueError(
            f"Unsupported content type for username/password payload: '{content_type}'. "
            f"Supported types: 'application/json', 'application/x-www-form-urlencoded'"
        )
