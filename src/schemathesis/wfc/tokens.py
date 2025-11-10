"""Token extraction and formatting utilities for WFC authentication."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from schemathesis.core.transforms import UNRESOLVABLE, resolve_pointer

from .errors import WFCTokenExtractionError


def extract_token_from_body(body: Any, selector: str) -> str:
    """Extract a token from a parsed JSON response body using a JSON Pointer (RFC 6901)."""
    result = resolve_pointer(body, selector)
    if result is UNRESOLVABLE:
        raise WFCTokenExtractionError(
            f"Token not found at JSON Pointer '{selector}'. Check that the path exists in the response body."
        )
    if isinstance(result, str):
        return result
    if isinstance(result, (int, float, bool)):
        return str(result)
    if result is None:
        raise WFCTokenExtractionError(f"Token at '{selector}' is null. Expected a non-null value.")
    raise WFCTokenExtractionError(
        f"Token at '{selector}' is not a string or primitive value. Got: {type(result).__name__}"
    )


def extract_token_from_header(headers: Mapping[str, str], name: str) -> str:
    """Extract a token from response headers (case-insensitive)."""
    name_lower = name.lower()
    for header_name, header_value in headers.items():
        if header_name.lower() == name_lower:
            return header_value
    available = ", ".join(f"'{h}'" for h in headers.keys())
    raise WFCTokenExtractionError(f"Header '{name}' not found in response. Available headers: {available or 'none'}")


def format_token(token: str, template: str = "{token}") -> str:
    """Interpolate a token into a `{token}` template, e.g. 'Bearer {token}'."""
    return template.replace("{token}", token)
