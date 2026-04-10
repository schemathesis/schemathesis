from collections.abc import Collection, Mapping
from typing import Any
from urllib.parse import unquote

from schemathesis.core import NOT_SET
from schemathesis.core.validation import contains_unicode_surrogate_pair, has_invalid_characters, is_latin_1_encodable

__all__ = [
    "is_valid_path",
    "is_valid_header",
    "is_valid_urlencoded",
    "is_valid_query",
]


def is_valid_path(parameters: dict[str, object], allow_encoded_slash_for: Collection[str] | None = None) -> bool:
    """Empty strings ("") are excluded from path by urllib3.

    A path containing to "/" or "%2F" will lead to ambiguous path resolution in
    many frameworks and libraries, such behaviour have been observed in both
    WSGI and ASGI applications.

    In this case one variable in the path template will be empty, which will lead to 404 in most cases.
    Because of it this case doesn't bring much value and might lead to false positive Schemathesis results.

    `allow_encoded_slash_for` lets callers allow `%2F` for selected parameter names while
    preserving strict defaults for all other path parameters.
    """
    allow = allow_encoded_slash_for or ()
    return not any(
        is_invalid_path_parameter(value, allow_encoded_slash=name in allow) for name, value in parameters.items()
    )


def is_invalid_path_parameter(value: Any, *, allow_encoded_slash: bool = False) -> bool:
    if value in ("/", ""):
        return True
    if contains_unicode_surrogate_pair(value):
        return True

    # Get string representation for checking problematic characters.
    # For non-strings (dicts, lists), their str() is what appears in the URL
    str_value = value if isinstance(value, str) else str(value)

    if "/" in str_value or "}" in str_value or "{" in str_value:
        return True

    if isinstance(value, str):
        decoded_value = unquote(str_value)
        # `%2F` is decoded by many HTTP stacks before routing, effectively turning it into `/`.
        # Allow it only when the caller explicitly opts in for this parameter.
        if not allow_encoded_slash and "/" in decoded_value:
            return True
        # Curly braces are structural characters in path templates.
        # Reject their quoted forms (e.g. `%7B` / `%7D`) as well.
        if "}" in decoded_value or "{" in decoded_value:
            return True

        # Avoid NULL bytes in path parameters — many webservers strip or reject them,
        # which can silently redirect the test to a different API operation.
        # Check decoded values as well to catch quoted forms like `%00`.
        if "\x00" in decoded_value:
            return True

    return False


def is_valid_header(headers: dict[str, object]) -> bool:
    for name, value in headers.items():
        if not is_latin_1_encodable(value):
            return False
        if has_invalid_characters(name, value):
            return False
    return True


def is_valid_query(query: dict[str, object]) -> bool:
    for name, value in query.items():
        if contains_unicode_surrogate_pair(name) or contains_unicode_surrogate_pair(value):
            return False
    return True


def is_valid_urlencoded(data: object) -> bool:
    if data is NOT_SET or isinstance(data, Mapping):
        return True

    if hasattr(data, "__iter__"):
        try:
            for _, _ in data:
                pass
            return True
        except (TypeError, ValueError):
            return False
    return False
