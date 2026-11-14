from collections.abc import Collection, Mapping
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
    """Reject path parameters that `urllib3` drops (`""`) or that make routing ambiguous (`/`, `%2F`).

    Either empties a template variable, mostly a 404 and a false positive; `allow_encoded_slash_for` permits `%2F`.
    """
    allow = allow_encoded_slash_for or ()
    return not any(
        is_invalid_path_parameter(value, allow_encoded_slash=name in allow) for name, value in parameters.items()
    )


def is_invalid_path_parameter(value: object, *, allow_encoded_slash: bool = False) -> bool:
    # `.` and `..` collapse during URL normalization like `/`, routing to a different operation.
    if value in ("/", "", ".", ".."):
        return True
    # An empty comma-joined array drops its path segment, so the request lands on a different operation.
    if isinstance(value, list) and (not value or value == [""]):
        return True
    if contains_unicode_surrogate_pair(value):
        return True

    # Non-strings such as dicts and lists appear in the URL as their `str()`
    str_value = value if isinstance(value, str) else str(value)

    if "/" in str_value or "}" in str_value or "{" in str_value:
        return True

    if isinstance(value, str):
        decoded_value = unquote(str_value)
        # Many HTTP stacks decode `%2F` to `/` before routing; allow it only on opt-in for this parameter.
        if not allow_encoded_slash and "/" in decoded_value:
            return True
        # Curly braces are structural in path templates; reject quoted forms like `%7B` too.
        if "}" in decoded_value or "{" in decoded_value:
            return True
        # `%2E` / `%2E%2E` decode to `.` / `..` and collapse during URL normalization.
        if decoded_value in (".", ".."):
            return True

        # Many servers strip or reject NULL bytes, which can redirect the test to another operation; check `%00` too.
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
