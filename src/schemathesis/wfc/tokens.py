"""Token extraction and formatting utilities for WFC authentication."""

from __future__ import annotations

import json
from typing import Any, Mapping

from schemathesis.core.transforms import UNRESOLVABLE, resolve_pointer

from .errors import WFCTokenExtractionError


def extract_token_from_body(body: str | bytes | dict[str, Any], selector: str) -> str:
    """Extract token from response body using JSON Pointer (RFC 6901).

    Args:
        body: Response body (JSON string, bytes, or already parsed dict)
        selector: JSON Pointer selector (e.g., "/access_token", "/data/token")

    Returns:
        Extracted token as string

    Raises:
        WFCTokenExtractionError: If token cannot be extracted

    Examples:
        >>> extract_token_from_body({"token": "abc"}, "/token")
        'abc'
        >>> extract_token_from_body('{"data": {"token": "xyz"}}', "/data/token")
        'xyz'

    """
    # Parse body if it's a string or bytes
    if isinstance(body, (str, bytes)):
        try:
            parsed = json.loads(body)
        except (json.JSONDecodeError, ValueError) as exc:
            raise WFCTokenExtractionError(f"Failed to parse response body as JSON: {exc}") from exc
    else:
        parsed = body

    # Extract using JSON Pointer
    result = resolve_pointer(parsed, selector)

    if result is UNRESOLVABLE:
        raise WFCTokenExtractionError(
            f"Token not found at JSON Pointer '{selector}'. Check that the path exists in the response body."
        )

    # Convert result to string
    if isinstance(result, str):
        return result
    elif isinstance(result, (int, float, bool)):
        # Convert primitives to string
        return str(result)
    elif result is None:
        raise WFCTokenExtractionError(f"Token at '{selector}' is null. Expected a non-null value.")
    else:
        # Complex types (dict, list) are not valid tokens
        raise WFCTokenExtractionError(
            f"Token at '{selector}' is not a string or primitive value. Got: {type(result).__name__}"
        )


def extract_token_from_header(headers: Mapping[str, str], name: str) -> str:
    """Extract token from response headers (case-insensitive).

    Args:
        headers: Response headers dictionary
        name: Header name to extract (case-insensitive)

    Returns:
        Extracted token as string

    Raises:
        WFCTokenExtractionError: If header not found

    Examples:
        >>> extract_token_from_header({"X-Auth-Token": "secret"}, "x-auth-token")
        'secret'
        >>> extract_token_from_header({"Authorization": "Bearer xyz"}, "Authorization")
        'Bearer xyz'

    """
    # Case-insensitive header lookup
    name_lower = name.lower()
    for header_name, header_value in headers.items():
        if header_name.lower() == name_lower:
            return header_value

    # Header not found
    available = ", ".join(f"'{h}'" for h in headers.keys())
    raise WFCTokenExtractionError(f"Header '{name}' not found in response. Available headers: {available or 'none'}")


def format_token(token: str, template: str = "{token}") -> str:
    """Format token using template with {token} placeholder.

    Args:
        token: The raw token value
        template: Template string with {token} placeholder

    Returns:
        Formatted token string

    Examples:
        >>> format_token("abc123", "Bearer {token}")
        'Bearer abc123'
        >>> format_token("xyz", "JWT {token}")
        'JWT xyz'
        >>> format_token("token", "{token}")
        'token'

    """
    return template.replace("{token}", token)
