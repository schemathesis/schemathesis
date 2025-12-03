from collections.abc import Mapping
from typing import Any

from schemathesis.core import NOT_SET
from schemathesis.core.validation import contains_unicode_surrogate_pair, has_invalid_characters, is_latin_1_encodable

__all__ = [
    "is_valid_path",
    "is_valid_header",
    "is_valid_urlencoded",
    "is_valid_query",
]


def is_valid_path(parameters: dict[str, object]) -> bool:
    """Empty strings ("") are excluded from path by urllib3.

    A path containing to "/" or "%2F" will lead to ambiguous path resolution in
    many frameworks and libraries, such behaviour have been observed in both
    WSGI and ASGI applications.

    In this case one variable in the path template will be empty, which will lead to 404 in most of the cases.
    Because of it this case doesn't bring much value and might lead to false positives results of Schemathesis runs.
    """
    return not any(is_invalid_path_parameter(value) for value in parameters.values())


def is_invalid_path_parameter(value: Any) -> bool:
    if value in ("/", ""):
        return True
    if contains_unicode_surrogate_pair(value):
        return True

    # Get string representation for checking problematic characters.
    # For non-strings (dicts, lists), their str() is what appears in the URL
    str_value = value if isinstance(value, str) else str(value)

    if "/" in str_value or "}" in str_value or "{" in str_value:
        return True

    # Avoid situations when the path parameter contains only NULL bytes.
    # Many webservers remove such bytes and as the result, the test can target a different API operation
    if isinstance(value, str) and len(value) == value.count("\x00"):
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
    # TODO: write a test that will check if `requests` can send it
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
