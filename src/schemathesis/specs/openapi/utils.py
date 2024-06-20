from __future__ import annotations

import string
from itertools import product
from typing import Any, Generator


def expand_status_code(status_code: str | int) -> Generator[int, None, None]:
    chars = [list(string.digits) if digit == "X" else [digit] for digit in str(status_code).upper()]
    for expanded in product(*chars):
        yield int("".join(expanded))


def is_header_location(location: str) -> bool:
    """Whether this location affects HTTP headers."""
    return location in ("header", "cookie")


def get_type(schema: dict[str, Any]) -> list[str]:
    type_ = schema.get("type", ["null", "boolean", "integer", "number", "string", "array", "object"])
    if isinstance(type_, str):
        return [type_]
    return type_
