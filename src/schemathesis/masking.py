from collections.abc import MutableMapping, MutableSequence
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


def mask_value(
    item: Any,
    *,
    keys_to_mask: FrozenSet[str] = DEFAULT_KEYS_TO_MASK,
    sensitive_markers: FrozenSet[str] = DEFAULT_SENSITIVE_MARKERS,
    default_replacement: str = DEFAULT_REPLACEMENT,
) -> None:
    if isinstance(item, MutableMapping):
        for key in list(item.keys()):
            lower_key = key.lower()
            if lower_key in keys_to_mask or any(marker in lower_key for marker in sensitive_markers):
                if isinstance(item[key], list):
                    item[key] = [default_replacement]
                else:
                    item[key] = default_replacement
        for value in item.values():
            if isinstance(value, (MutableMapping, MutableSequence)):
                mask_value(
                    value,
                    keys_to_mask=keys_to_mask,
                    sensitive_markers=sensitive_markers,
                    default_replacement=default_replacement,
                )
    elif isinstance(item, MutableSequence):
        for value in item:
            if isinstance(value, (MutableMapping, MutableSequence)):
                mask_value(
                    value,
                    keys_to_mask=keys_to_mask,
                    sensitive_markers=sensitive_markers,
                    default_replacement=default_replacement,
                )


def mask_case(
    case: "Case",
    *,
    keys_to_mask: FrozenSet[str] = DEFAULT_KEYS_TO_MASK,
    sensitive_markers: FrozenSet[str] = DEFAULT_SENSITIVE_MARKERS,
    default_replacement: str = DEFAULT_REPLACEMENT,
) -> None:
    if case.path_parameters is not None:
        mask_value(
            case.path_parameters,
            keys_to_mask=keys_to_mask,
            sensitive_markers=sensitive_markers,
            default_replacement=default_replacement,
        )
    if case.headers is not None:
        mask_value(
            case.headers,
            keys_to_mask=keys_to_mask,
            sensitive_markers=sensitive_markers,
            default_replacement=default_replacement,
        )
    if case.cookies is not None:
        mask_value(
            case.cookies,
            keys_to_mask=keys_to_mask,
            sensitive_markers=sensitive_markers,
            default_replacement=default_replacement,
        )
    if case.query is not None:
        mask_value(
            case.query,
            keys_to_mask=keys_to_mask,
            sensitive_markers=sensitive_markers,
            default_replacement=default_replacement,
        )
    if case.body not in (None, NOT_SET):
        mask_value(
            case.body,
            keys_to_mask=keys_to_mask,
            sensitive_markers=sensitive_markers,
            default_replacement=default_replacement,
        )
    if case.source is not None:
        mask_history(case.source)


def mask_history(
    source: "CaseSource",
    *,
    keys_to_mask: FrozenSet[str] = DEFAULT_KEYS_TO_MASK,
    sensitive_markers: FrozenSet[str] = DEFAULT_SENSITIVE_MARKERS,
    default_replacement: str = DEFAULT_REPLACEMENT,
) -> None:
    """Recursively mask history of case/response pairs."""
    current: Optional["CaseSource"] = source
    while current is not None:
        mask_case(
            current.case,
            keys_to_mask=keys_to_mask,
            sensitive_markers=sensitive_markers,
            default_replacement=default_replacement,
        )
        mask_response(
            current.response,
            keys_to_mask=keys_to_mask,
            sensitive_markers=sensitive_markers,
            default_replacement=default_replacement,
        )
        current = current.case.source


def mask_response(
    response: "GenericResponse",
    *,
    keys_to_mask: FrozenSet[str] = DEFAULT_KEYS_TO_MASK,
    sensitive_markers: FrozenSet[str] = DEFAULT_SENSITIVE_MARKERS,
    default_replacement: str = DEFAULT_REPLACEMENT,
) -> None:
    # Mask headers
    mask_value(
        response.headers,
        keys_to_mask=keys_to_mask,
        sensitive_markers=sensitive_markers,
        default_replacement=default_replacement,
    )


def mask_request(
    request: Union[PreparedRequest, "Request"],
    *,
    keys_to_mask: FrozenSet[str] = DEFAULT_KEYS_TO_MASK,
    sensitive_markers: FrozenSet[str] = DEFAULT_SENSITIVE_MARKERS,
    default_replacement: str = DEFAULT_REPLACEMENT,
) -> None:
    if isinstance(request, PreparedRequest) and request.url:
        request.url = mask_url(
            request.url,
            keys_to_mask=keys_to_mask,
            sensitive_markers=sensitive_markers,
            default_replacement=default_replacement,
        )
    else:
        request = cast("Request", request)
        request.uri = mask_url(
            request.uri,
            keys_to_mask=keys_to_mask,
            sensitive_markers=sensitive_markers,
            default_replacement=default_replacement,
        )
    # Mask headers
    mask_value(
        request.headers,
        keys_to_mask=keys_to_mask,
        sensitive_markers=sensitive_markers,
        default_replacement=default_replacement,
    )


def mask_sensitive_output(
    case: "Case",
    response: Optional["GenericResponse"] = None,
    *,
    keys_to_mask: FrozenSet[str] = DEFAULT_KEYS_TO_MASK,
    sensitive_markers: FrozenSet[str] = DEFAULT_SENSITIVE_MARKERS,
    default_replacement: str = DEFAULT_REPLACEMENT,
) -> None:
    mask_case(
        case,
        keys_to_mask=keys_to_mask,
        sensitive_markers=sensitive_markers,
        default_replacement=default_replacement,
    )
    if response is not None:
        mask_response(
            response,
            keys_to_mask=keys_to_mask,
            sensitive_markers=sensitive_markers,
            default_replacement=default_replacement,
        )
        mask_request(
            response.request,
            keys_to_mask=keys_to_mask,
            sensitive_markers=sensitive_markers,
            default_replacement=default_replacement,
        )


def mask_url(
    url: str,
    *,
    keys_to_mask: FrozenSet[str] = DEFAULT_KEYS_TO_MASK,
    sensitive_markers: FrozenSet[str] = DEFAULT_SENSITIVE_MARKERS,
    default_replacement: str = DEFAULT_REPLACEMENT,
) -> str:
    parsed = urlsplit(url)

    # Mask authority
    netloc_parts = parsed.netloc.split("@")
    if len(netloc_parts) > 1:
        netloc = f"{default_replacement}@{netloc_parts[-1]}"
    else:
        netloc = parsed.netloc

    # Mask query parameters
    query = parse_qs(parsed.query, keep_blank_values=True)
    mask_value(
        query, keys_to_mask=keys_to_mask, sensitive_markers=sensitive_markers, default_replacement=default_replacement
    )
    masked_query = urlencode(query, doseq=True)

    # Reconstruct the URL
    masked_url_parts = parsed._replace(netloc=netloc, query=masked_query)
    return urlunsplit(masked_url_parts)


def mask_serialized_check(check: "SerializedCheck") -> None:
    # TODO: Unit tests
    mask_request(check.request)
    response = check.response
    if response:
        mask_value(response.headers)
    mask_serialized_case(check.example)
    for entry in check.history:
        mask_serialized_case(entry.case)
        mask_value(entry.response.headers)


def mask_serialized_case(case: "SerializedCase") -> None:
    # TODO: Unit tests
    for value in (case.path_parameters, case.headers, case.cookies, case.query, case.extra_headers):
        if value is not None:
            mask_value(value)


def mask_serialized_interaction(interaction: "SerializedInteraction") -> None:
    # TODO: Unit tests
    mask_request(interaction.request)
    if interaction.response:
        mask_value(interaction.response.headers)
    for check in interaction.checks:
        mask_serialized_check(check)
