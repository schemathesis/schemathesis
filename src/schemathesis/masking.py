from collections.abc import MutableMapping, MutableSequence
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any, FrozenSet, Optional, Union, cast
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit

from requests import PreparedRequest

from .utils import NOT_SET

if TYPE_CHECKING:
    from .models import Case, CaseSource, Request
    from .runner.serialization import SerializedCase, SerializedCheck, SerializedInteraction
    from .utils import GenericResponse

# Exact keys to mask
DEFAULT_KEYS_TO_MASK = frozenset(
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

DEFAULT_REPLACEMENT = "[Masked]"


@dataclass
class MaskingConfig:
    """Configuration class for masking sensitive data.

    :param FrozenSet[str] keys_to_mask: The exact keys to mask.
    :param FrozenSet[str] sensitive_markers: Markers indicating potentially sensitive keys.
    :param str replacement: The replacement string for masked values.
    """

    keys_to_mask: FrozenSet[str] = DEFAULT_KEYS_TO_MASK
    sensitive_markers: FrozenSet[str] = DEFAULT_SENSITIVE_MARKERS
    replacement: str = DEFAULT_REPLACEMENT

    def with_keys_to_mask(self, *keys: str) -> "MaskingConfig":
        """Create a new configuration with additional keys to mask."""
        new_keys_to_mask = self.keys_to_mask.union(keys)
        return replace(self, keys_to_mask=frozenset(new_keys_to_mask))

    def without_keys_to_mask(self, *keys: str) -> "MaskingConfig":
        """Create a new configuration without certain keys to mask."""
        new_keys_to_mask = self.keys_to_mask.difference(keys)
        return replace(self, keys_to_mask=frozenset(new_keys_to_mask))

    def with_sensitive_markers(self, *markers: str) -> "MaskingConfig":
        """Create a new configuration with additional sensitive markers."""
        new_sensitive_markers = self.sensitive_markers.union(markers)
        return replace(self, sensitive_markers=frozenset(new_sensitive_markers))

    def without_sensitive_markers(self, *markers: str) -> "MaskingConfig":
        """Create a new configuration without certain sensitive markers."""
        new_sensitive_markers = self.sensitive_markers.difference(markers)
        return replace(self, sensitive_markers=frozenset(new_sensitive_markers))


DEFAULT_MASKING_CONFIG = MaskingConfig()


def mask_value(item: Any, *, config: MaskingConfig = DEFAULT_MASKING_CONFIG) -> None:
    """Mask sensitive values within a given item.

    This function is recursive and will mask sensitive data within nested
    dictionaries and lists as well.
    """
    if isinstance(item, MutableMapping):
        for key in list(item.keys()):
            lower_key = key.lower()
            if lower_key in config.keys_to_mask or any(marker in lower_key for marker in config.sensitive_markers):
                if isinstance(item[key], list):
                    item[key] = [config.replacement]
                else:
                    item[key] = config.replacement
        for value in item.values():
            if isinstance(value, (MutableMapping, MutableSequence)):
                mask_value(value, config=config)
    elif isinstance(item, MutableSequence):
        for value in item:
            if isinstance(value, (MutableMapping, MutableSequence)):
                mask_value(value, config=config)


def mask_case(case: "Case", *, config: MaskingConfig = DEFAULT_MASKING_CONFIG) -> None:
    """Mask sensitive values within a given case."""
    if case.path_parameters is not None:
        mask_value(case.path_parameters, config=config)
    if case.headers is not None:
        mask_value(case.headers, config=config)
    if case.cookies is not None:
        mask_value(case.cookies, config=config)
    if case.query is not None:
        mask_value(case.query, config=config)
    if case.body not in (None, NOT_SET):
        mask_value(case.body, config=config)
    if case.source is not None:
        mask_history(case.source, config=config)


def mask_history(source: "CaseSource", *, config: MaskingConfig = DEFAULT_MASKING_CONFIG) -> None:
    """Recursively mask history of case/response pairs."""
    current: Optional["CaseSource"] = source
    while current is not None:
        mask_case(current.case, config=config)
        mask_response(current.response, config=config)
        current = current.case.source


def mask_response(response: "GenericResponse", *, config: MaskingConfig = DEFAULT_MASKING_CONFIG) -> None:
    # Mask headers
    mask_value(response.headers, config=config)


def mask_request(request: Union[PreparedRequest, "Request"], *, config: MaskingConfig = DEFAULT_MASKING_CONFIG) -> None:
    if isinstance(request, PreparedRequest) and request.url:
        request.url = mask_url(request.url, config=config)
    else:
        request = cast("Request", request)
        request.uri = mask_url(request.uri, config=config)
    # Mask headers
    mask_value(request.headers, config=config)


def mask_sensitive_output(
    case: "Case", response: Optional["GenericResponse"] = None, *, config: MaskingConfig = DEFAULT_MASKING_CONFIG
) -> None:
    mask_case(case, config=config)
    if response is not None:
        mask_response(response, config=config)
        mask_request(response.request, config=config)


def mask_url(url: str, *, config: MaskingConfig = DEFAULT_MASKING_CONFIG) -> str:
    """Mask sensitive parts of a given URL.

    This function will mask the authority and query parameters in the URL.
    """
    parsed = urlsplit(url)

    # Mask authority
    netloc_parts = parsed.netloc.split("@")
    if len(netloc_parts) > 1:
        netloc = f"{config.replacement}@{netloc_parts[-1]}"
    else:
        netloc = parsed.netloc

    # Mask query parameters
    query = parse_qs(parsed.query, keep_blank_values=True)
    mask_value(query, config=config)
    masked_query = urlencode(query, doseq=True)

    # Reconstruct the URL
    masked_url_parts = parsed._replace(netloc=netloc, query=masked_query)
    return urlunsplit(masked_url_parts)


def mask_serialized_check(check: "SerializedCheck", *, config: MaskingConfig = DEFAULT_MASKING_CONFIG) -> None:
    mask_request(check.request, config=config)
    response = check.response
    if response:
        mask_value(response.headers, config=config)
    mask_serialized_case(check.example, config=config)
    for entry in check.history:
        mask_serialized_case(entry.case, config=config)
        mask_value(entry.response.headers, config=config)


def mask_serialized_case(case: "SerializedCase", *, config: MaskingConfig = DEFAULT_MASKING_CONFIG) -> None:
    for value in (case.path_parameters, case.headers, case.cookies, case.query, case.extra_headers):
        if value is not None:
            mask_value(value, config=config)


def mask_serialized_interaction(
    interaction: "SerializedInteraction", *, config: MaskingConfig = DEFAULT_MASKING_CONFIG
) -> None:
    mask_request(interaction.request, config=config)
    mask_value(interaction.response.headers, config=config)
    for check in interaction.checks:
        mask_serialized_check(check, config=config)
