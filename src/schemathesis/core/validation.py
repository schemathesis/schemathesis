import re

# Adapted from http.client._is_illegal_header_value
INVALID_HEADER_RE = re.compile(r"\n(?![ \t])|\r(?![ \t\n])")


def has_invalid_characters(name: str, value: object) -> bool:
    from requests.exceptions import InvalidHeader
    from requests.utils import check_header_validity

    if not isinstance(value, str):
        return False
    try:
        check_header_validity((name, value))
        return bool(INVALID_HEADER_RE.search(value))
    except InvalidHeader:
        return True


def is_latin_1_encodable(value: object) -> bool:
    """Check if a value is a Latin-1 encodable string."""
    if not isinstance(value, str):
        return False
    try:
        value.encode("latin-1")
        return True
    except UnicodeEncodeError:
        return False


SURROGATE_PAIR_RE = re.compile(r"[\ud800-\udfff]")
_contains_surrogate_pair = SURROGATE_PAIR_RE.search


def contains_unicode_surrogate_pair(item: object) -> bool:
    if isinstance(item, list):
        return any(isinstance(item_, str) and bool(_contains_surrogate_pair(item_)) for item_ in item)
    return isinstance(item, str) and bool(_contains_surrogate_pair(item))
