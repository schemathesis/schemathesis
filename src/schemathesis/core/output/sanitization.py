from __future__ import annotations

from collections.abc import MutableMapping, MutableSequence
from dataclasses import dataclass, replace
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit

from schemathesis.core import NOT_SET, NotSet

# Exact keys to sanitize
DEFAULT_KEYS_TO_SANITIZE = frozenset(
    (
        "phpsessid",
        "xsrf-token",
        "_csrf",
        "_csrf_token",
        "_session",
        "_xsrf",
        "aiohttp_session",
        "api_key",
        "api-key",
        "apikey",
        "auth",
        "authorization",
        "connect.sid",
        "cookie",
        "credentials",
        "csrf",
        "csrf_token",
        "csrf-token",
        "csrftoken",
        "ip_address",
        "mysql_pwd",
        "passwd",
        "password",
        "private_key",
        "private-key",
        "privatekey",
        "remote_addr",
        "remote-addr",
        "secret",
        "session",
        "sessionid",
        "set_cookie",
        "set-cookie",
        "token",
        "x_api_key",
        "x-api-key",
        "x_csrftoken",
        "x-csrftoken",
        "x_forwarded_for",
        "x-forwarded-for",
        "x_real_ip",
        "x-real-ip",
    )
)

# Markers indicating potentially sensitive keys
DEFAULT_SENSITIVE_MARKERS = frozenset(
    (
        "token",
        "key",
        "secret",
        "password",
        "auth",
        "session",
        "passwd",
        "credential",
    )
)

DEFAULT_REPLACEMENT = "[Filtered]"


@dataclass
class SanitizationConfig:
    """Configuration class for sanitizing sensitive data."""

    keys_to_sanitize: frozenset[str] = DEFAULT_KEYS_TO_SANITIZE
    sensitive_markers: frozenset[str] = DEFAULT_SENSITIVE_MARKERS
    replacement: str = DEFAULT_REPLACEMENT

    @classmethod
    def from_config(
        cls,
        base_config: SanitizationConfig,
        *,
        replacement: str | NotSet = NOT_SET,
        keys_to_sanitize: list[str] | NotSet = NOT_SET,
        sensitive_markers: list[str] | NotSet = NOT_SET,
    ) -> SanitizationConfig:
        """Create a new config by replacing specified values."""
        kwargs: dict[str, Any] = {}
        if not isinstance(replacement, NotSet):
            kwargs["replacement"] = replacement
        if not isinstance(keys_to_sanitize, NotSet):
            kwargs["keys_to_sanitize"] = frozenset(key.lower() for key in keys_to_sanitize)
        if not isinstance(sensitive_markers, NotSet):
            kwargs["sensitive_markers"] = frozenset(marker.lower() for marker in sensitive_markers)
        return replace(base_config, **kwargs)

    def extend(
        self,
        *,
        keys_to_sanitize: list[str] | NotSet = NOT_SET,
        sensitive_markers: list[str] | NotSet = NOT_SET,
    ) -> SanitizationConfig:
        """Create a new config by extending current sets."""
        config = self
        if not isinstance(keys_to_sanitize, NotSet):
            new_keys = config.keys_to_sanitize.union(key.lower() for key in keys_to_sanitize)
            config = replace(config, keys_to_sanitize=new_keys)

        if not isinstance(sensitive_markers, NotSet):
            new_markers = config.sensitive_markers.union(marker.lower() for marker in sensitive_markers)
            config = replace(config, sensitive_markers=new_markers)

        return config


_DEFAULT_SANITIZATION_CONFIG = SanitizationConfig()


def configure(
    replacement: str | NotSet = NOT_SET,
    keys_to_sanitize: list[str] | NotSet = NOT_SET,
    sensitive_markers: list[str] | NotSet = NOT_SET,
) -> None:
    """Replace current sanitization configuration."""
    global _DEFAULT_SANITIZATION_CONFIG
    _DEFAULT_SANITIZATION_CONFIG = SanitizationConfig.from_config(
        _DEFAULT_SANITIZATION_CONFIG,
        replacement=replacement,
        keys_to_sanitize=keys_to_sanitize,
        sensitive_markers=sensitive_markers,
    )


def extend(
    keys_to_sanitize: list[str] | NotSet = NOT_SET,
    sensitive_markers: list[str] | NotSet = NOT_SET,
) -> None:
    """Extend current sanitization configuration."""
    global _DEFAULT_SANITIZATION_CONFIG
    _DEFAULT_SANITIZATION_CONFIG = _DEFAULT_SANITIZATION_CONFIG.extend(
        keys_to_sanitize=keys_to_sanitize,
        sensitive_markers=sensitive_markers,
    )


def sanitize_value(item: Any, *, config: SanitizationConfig | None = None) -> None:
    """Sanitize sensitive values within a given item.

    This function is recursive and will sanitize sensitive data within nested
    dictionaries and lists as well.
    """
    config = config or _DEFAULT_SANITIZATION_CONFIG
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


def sanitize_url(url: str, *, config: SanitizationConfig | None = None) -> str:
    """Sanitize sensitive parts of a given URL.

    This function will sanitize the authority and query parameters in the URL.
    """
    config = config or _DEFAULT_SANITIZATION_CONFIG
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
