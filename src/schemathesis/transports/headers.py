from __future__ import annotations

import re
from typing import Any

from ..constants import USER_AGENT


def setup_default_headers(kwargs: dict[str, Any]) -> None:
    headers = kwargs.setdefault("headers", {})
    if "user-agent" not in {header.lower() for header in headers}:
        kwargs["headers"]["User-Agent"] = USER_AGENT


def is_latin_1_encodable(value: str) -> bool:
    """Header values are encoded to latin-1 before sending."""
    try:
        value.encode("latin-1")
        return True
    except UnicodeEncodeError:
        return False


# Adapted from http.client._is_illegal_header_value
INVALID_HEADER_RE = re.compile(r"\n(?![ \t])|\r(?![ \t\n])")


def has_invalid_characters(name: str, value: str) -> bool:
    from requests.exceptions import InvalidHeader
    from requests.utils import check_header_validity

    try:
        check_header_validity((name, value))
        return bool(INVALID_HEADER_RE.search(value))
    except InvalidHeader:
        return True
