from __future__ import annotations

import re
from collections.abc import Callable, MutableMapping, Sequence
from functools import lru_cache
from typing import TYPE_CHECKING, Any
from urllib.parse import quote_plus, unquote_plus, urlsplit, urlunsplit

if TYPE_CHECKING:
    from schemathesis.config import SanitizationConfig

# `MutableMapping` covers `CaseInsensitiveDict` without importing `requests`, which deadlocks on writer threads.
MAPPING_TYPES = (dict, MutableMapping)


def is_sensitive_key(key: object, keys_to_sanitize: Sequence[str], sensitive_markers: Sequence[str]) -> bool:
    """Whether `key` matches the configured sensitivity rules (exact-in-list or substring marker)."""
    if not isinstance(key, str):
        return False
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


def sanitize_headers(headers: MutableMapping[str, Any], *, config: SanitizationConfig) -> None:
    """Replace sensitive header values with `config.replacement`; cookie headers keep their non-sensitive cookies."""
    for name, value in headers.items():
        sanitize_cookies = _COOKIE_SANITIZERS.get(name.lower())
        if sanitize_cookies is not None:
            if isinstance(value, list):
                headers[name] = [sanitize_cookies(item, config) for item in value]
            else:
                headers[name] = sanitize_cookies(value, config)
        elif is_sensitive_key(
            name, keys_to_sanitize=config.keys_to_sanitize, sensitive_markers=config.sensitive_markers
        ):
            headers[name] = [config.replacement] if isinstance(value, list) else config.replacement


def sanitize_header(name: str, value: str, *, config: SanitizationConfig) -> str:
    """`value` of the `name` header with sensitive data replaced by `config.replacement`."""
    sanitize_cookies = _COOKIE_SANITIZERS.get(name.lower())
    if sanitize_cookies is not None:
        return sanitize_cookies(value, config)
    if is_sensitive_key(name, keys_to_sanitize=config.keys_to_sanitize, sensitive_markers=config.sensitive_markers):
        return config.replacement
    return value


def _sanitize_cookie_header(value: object, config: SanitizationConfig) -> str:
    """Mask sensitive cookies of a `Cookie` header; a value that is not `name=value` pairs is masked whole."""
    if not isinstance(value, str):
        return config.replacement
    pairs = value.split(";")
    for index, pair in enumerate(pairs):
        name, separator, _ = pair.partition("=")
        if not separator:
            # Empty segments, e.g. after a trailing `;`, carry no value
            if pair.strip():
                return config.replacement
        elif is_sensitive_key(
            name.strip(), keys_to_sanitize=config.keys_to_sanitize, sensitive_markers=config.sensitive_markers
        ):
            pairs[index] = f"{name}={config.replacement}"
    return ";".join(pairs)


# The comma of an `Expires` date, e.g. `Expires=Wed, 21 Oct 2026 07:28:00 GMT`
_EXPIRES_WEEKDAY = re.compile(r"expires\s*=\s*[a-z]+,", re.IGNORECASE)


def _sanitize_set_cookie_header(value: object, config: SanitizationConfig) -> str:
    """Mask a sensitive cookie of a `Set-Cookie` header and keep its attributes."""
    # Any other comma may join several `Set-Cookie` headers into one value
    if not isinstance(value, str) or "," in _EXPIRES_WEEKDAY.sub("", value):
        return config.replacement
    cookie, attributes_separator, attributes = value.partition(";")
    name, separator, _ = cookie.partition("=")
    if not separator:
        return config.replacement
    if is_sensitive_key(
        name.strip(), keys_to_sanitize=config.keys_to_sanitize, sensitive_markers=config.sensitive_markers
    ):
        return f"{name}={config.replacement}{attributes_separator}{attributes}"
    return value


# Headers carrying cookies are masked per cookie name, whatever the configured keys say about the header name
_COOKIE_SANITIZERS: dict[str, Callable[[object, SanitizationConfig], str]] = {
    "cookie": _sanitize_cookie_header,
    "set-cookie": _sanitize_set_cookie_header,
}


def sanitize_url(url: str, *, config: SanitizationConfig) -> str:
    """Mask credentials in the authority and values of sensitive query parameters."""
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

    sanitized_url_parts = parsed._replace(netloc=netloc, query=sanitized_query)
    return urlunsplit(sanitized_url_parts)
