from __future__ import annotations

import threading
from collections.abc import MutableMapping, MutableSequence
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any, cast
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit

from .constants import NOT_SET

if TYPE_CHECKING:
    from requests import PreparedRequest

    from .models import Case, CaseSource, Request
    from .runner.serialization import SerializedCase, SerializedCheck, SerializedInteraction
    from .transports.responses import GenericResponse

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
class Config:
    """Configuration class for sanitizing sensitive data.

    :param FrozenSet[str] keys_to_sanitize: The exact keys to sanitize (case-insensitive).
    :param FrozenSet[str] sensitive_markers: Markers indicating potentially sensitive keys (case-insensitive).
    :param str replacement: The replacement string for sanitized values.
    """

    keys_to_sanitize: frozenset[str] = DEFAULT_KEYS_TO_SANITIZE
    sensitive_markers: frozenset[str] = DEFAULT_SENSITIVE_MARKERS
    replacement: str = DEFAULT_REPLACEMENT

    def with_keys_to_sanitize(self, *keys: str) -> Config:
        """Create a new configuration with additional keys to sanitize."""
        new_keys_to_sanitize = self.keys_to_sanitize.union([key.lower() for key in keys])
        return replace(self, keys_to_sanitize=frozenset(new_keys_to_sanitize))

    def without_keys_to_sanitize(self, *keys: str) -> Config:
        """Create a new configuration without certain keys to sanitize."""
        new_keys_to_sanitize = self.keys_to_sanitize.difference([key.lower() for key in keys])
        return replace(self, keys_to_sanitize=frozenset(new_keys_to_sanitize))

    def with_sensitive_markers(self, *markers: str) -> Config:
        """Create a new configuration with additional sensitive markers."""
        new_sensitive_markers = self.sensitive_markers.union([key.lower() for key in markers])
        return replace(self, sensitive_markers=frozenset(new_sensitive_markers))

    def without_sensitive_markers(self, *markers: str) -> Config:
        """Create a new configuration without certain sensitive markers."""
        new_sensitive_markers = self.sensitive_markers.difference([key.lower() for key in markers])
        return replace(self, sensitive_markers=frozenset(new_sensitive_markers))


_thread_local = threading.local()


def _get_default_sanitization_config() -> Config:
    # Initialize the thread-local default sanitization config if not already set
    if not hasattr(_thread_local, "default_sanitization_config"):
        _thread_local.default_sanitization_config = Config()
    return _thread_local.default_sanitization_config


def configure(config: Config) -> None:
    _thread_local.default_sanitization_config = config


def sanitize_value(item: Any, *, config: Config | None = None) -> None:
    """Sanitize sensitive values within a given item.

    This function is recursive and will sanitize sensitive data within nested
    dictionaries and lists as well.
    """
    config = config or _get_default_sanitization_config()
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


def sanitize_case(case: Case, *, config: Config | None = None) -> None:
    """Sanitize sensitive values within a given case."""
    if case.path_parameters is not None:
        sanitize_value(case.path_parameters, config=config)
    if case.headers is not None:
        sanitize_value(case.headers, config=config)
    if case.cookies is not None:
        sanitize_value(case.cookies, config=config)
    if case.query is not None:
        sanitize_value(case.query, config=config)
    if case.body not in (None, NOT_SET):
        sanitize_value(case.body, config=config)
    if case.source is not None:
        sanitize_history(case.source, config=config)


def sanitize_history(source: CaseSource, *, config: Config | None = None) -> None:
    """Recursively sanitize history of case/response pairs."""
    current: CaseSource | None = source
    while current is not None:
        sanitize_case(current.case, config=config)
        sanitize_response(current.response, config=config)
        current = current.case.source


def sanitize_response(response: GenericResponse, *, config: Config | None = None) -> None:
    # Sanitize headers
    sanitize_value(response.headers, config=config)


def sanitize_request(request: PreparedRequest | Request, *, config: Config | None = None) -> None:
    from requests import PreparedRequest

    if isinstance(request, PreparedRequest) and request.url:
        request.url = sanitize_url(request.url, config=config)
    else:
        request = cast("Request", request)
        request.uri = sanitize_url(request.uri, config=config)
    # Sanitize headers
    sanitize_value(request.headers, config=config)


def sanitize_output(case: Case, response: GenericResponse | None = None, *, config: Config | None = None) -> None:
    sanitize_case(case, config=config)
    if response is not None:
        sanitize_response(response, config=config)
        sanitize_request(response.request, config=config)


def sanitize_url(url: str, *, config: Config | None = None) -> str:
    """Sanitize sensitive parts of a given URL.

    This function will sanitize the authority and query parameters in the URL.
    """
    config = config or _get_default_sanitization_config()
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


def sanitize_serialized_check(check: SerializedCheck, *, config: Config | None = None) -> None:
    sanitize_request(check.request, config=config)
    response = check.response
    if response:
        sanitize_value(response.headers, config=config)
    sanitize_serialized_case(check.example, config=config)
    for entry in check.history:
        sanitize_serialized_case(entry.case, config=config)
        sanitize_value(entry.response.headers, config=config)


def sanitize_serialized_case(case: SerializedCase, *, config: Config | None = None) -> None:
    case.url = sanitize_url(case.url, config=config)
    for value in (case.path_parameters, case.headers, case.cookies, case.query, case.extra_headers):
        if value is not None:
            sanitize_value(value, config=config)


def sanitize_serialized_interaction(interaction: SerializedInteraction, *, config: Config | None = None) -> None:
    sanitize_request(interaction.request, config=config)
    sanitize_value(interaction.response.headers, config=config)
    for check in interaction.checks:
        sanitize_serialized_check(check, config=config)
