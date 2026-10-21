from __future__ import annotations

from collections.abc import MutableMapping, Sequence
from functools import lru_cache
from typing import TYPE_CHECKING
from urllib.parse import quote_plus, unquote_plus, urlsplit, urlunsplit

if TYPE_CHECKING:
    from schemathesis.config import SanitizationConfig

# `MutableMapping` covers `CaseInsensitiveDict` without importing `requests`, which deadlocks on writer threads.
MAPPING_TYPES = (dict, MutableMapping)


def is_sensitive_key(key: str, keys_to_sanitize: Sequence[str], sensitive_markers: Sequence[str]) -> bool:
    """Whether `key` matches the configured sensitivity rules (exact-in-list or substring marker)."""
    lowered = key.lower()
    if lowered in keys_to_sanitize:
        return True
    return any(marker in lowered for marker in sensitive_markers)


def sanitize_value(item: object, *, config: SanitizationConfig) -> None:
    """Replace sensitive values within `item` with `config.replacement`; recurses into nested dicts/lists."""
    if isinstance(item, MAPPING_TYPES):
        for key in item:
            if is_sensitive_key(
                key,
                keys_to_sanitize=config.keys_to_sanitize,
                sensitive_markers=config.sensitive_markers,
            ):
                if isinstance(item[key], list):
                    item[key] = [config.replacement]
                else:
                    item[key] = config.replacement
        for value in item.values():
            if isinstance(value, (dict, list)):
                sanitize_value(value, config=config)
    elif isinstance(item, list):
        for value in item:
            if isinstance(value, (dict, list)):
                sanitize_value(value, config=config)


def sanitize_url(url: str, *, config: SanitizationConfig) -> str:
    """Sanitize sensitive parts of a given URL.

    This function will sanitize the authority and query parameters in the URL.
    """
    return _sanitize_url_cached(
        url,
        config.replacement,
        config.keys_to_sanitize,
        config.sensitive_markers,
    )


def sanitized(url: str | None, sanitization_config: SanitizationConfig) -> str | None:
    # The schema location may be a file path; only URLs can carry credentials.
    if url is None or not sanitization_config.enabled or not url.startswith(("http://", "https://")):
        return url
    return sanitize_url(url, config=sanitization_config)


@lru_cache(maxsize=512)
def _sanitize_url_cached(
    url: str,
    replacement: str,
    keys_to_sanitize: tuple[str, ...],
    sensitive_markers: tuple[str, ...],
) -> str:
    parsed = urlsplit(url)

    # Sanitize authority
    netloc_parts = parsed.netloc.split("@")
    if len(netloc_parts) > 1:
        netloc = f"{replacement}@{netloc_parts[-1]}"
    else:
        netloc = parsed.netloc

    # Mask values of sensitive query keys; every other pair stays byte-for-byte as sent
    pairs = parsed.query.split("&")
    for index, pair in enumerate(pairs):
        key, separator, _ = pair.partition("=")
        # A bare key has no value to mask
        if separator and is_sensitive_key(
            unquote_plus(key), keys_to_sanitize=keys_to_sanitize, sensitive_markers=sensitive_markers
        ):
            pairs[index] = f"{key}={quote_plus(replacement)}"
    sanitized_query = "&".join(pairs)

    # Reconstruct the URL
    sanitized_url_parts = parsed._replace(netloc=netloc, query=sanitized_query)
    return urlunsplit(sanitized_url_parts)
