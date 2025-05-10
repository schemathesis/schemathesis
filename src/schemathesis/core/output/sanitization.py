from __future__ import annotations

from collections.abc import MutableMapping, MutableSequence
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit

from schemathesis.config import SanitizationConfig


def sanitize_value(item: Any, *, config: SanitizationConfig) -> None:
    """Sanitize sensitive values within a given item.

    This function is recursive and will sanitize sensitive data within nested
    dictionaries and lists as well.
    """
    if isinstance(item, MutableMapping):
        for key in list(item.keys()):
            lower_key = key.lower()
            if lower_key in config.keys_to_sanitize or any(marker in lower_key for marker in config.sensitive_markers):
                if isinstance(item[key], list):
                    item[key] = [config.replacement]
                else:
                    item[key] = config.replacement
        for value in item.values():
            if isinstance(value, (MutableMapping, MutableSequence)):
                sanitize_value(value, config=config)
    elif isinstance(item, MutableSequence):
        for value in item:
            if isinstance(value, (MutableMapping, MutableSequence)):
                sanitize_value(value, config=config)


def sanitize_url(url: str, *, config: SanitizationConfig) -> str:
    """Sanitize sensitive parts of a given URL.

    This function will sanitize the authority and query parameters in the URL.
    """
    parsed = urlsplit(url)

    # Sanitize authority
    netloc_parts = parsed.netloc.split("@")
    if len(netloc_parts) > 1:
        netloc = f"{config.replacement}@{netloc_parts[-1]}"
    else:
        netloc = parsed.netloc

    # Sanitize query parameters
    query = parse_qs(parsed.query, keep_blank_values=True)
    sanitize_value(query, config=config)
    sanitized_query = urlencode(query, doseq=True)

    # Reconstruct the URL
    sanitized_url_parts = parsed._replace(netloc=netloc, query=sanitized_query)
    return urlunsplit(sanitized_url_parts)
