from __future__ import annotations

import re
import string
from collections.abc import Generator
from itertools import chain, product

from schemathesis.core import string_to_boolean


def coerce_parameter_value(value: str, schema: dict) -> str | int | float | bool | None:
    """Coerce a string HTTP parameter value to the type declared by its JSON schema.

    Query/path/header/cookie values arrive as strings over HTTP; schemas describe
    the parsed type, so type conversion is attempted before validation.
    """
    schema_type = schema.get("type")
    if schema_type == "integer":
        try:
            return int(value)
        except (ValueError, TypeError):
            return value
    if schema_type == "number":
        try:
            return float(value)
        except (ValueError, TypeError):
            return value
    if schema_type == "boolean":
        return string_to_boolean(value)
    if schema_type == "null" and isinstance(value, str) and value.lower() == "null":
        return None
    return value


def openapi_path_to_werkzeug(path: str) -> str:
    """Convert OpenAPI path template {param} to werkzeug <param> syntax."""
    return re.sub(r"\{([^}]+)\}", r"<\1>", path)


def expand_status_code(status_code: str | int) -> Generator[int, None, None]:
    chars = [list(string.digits) if digit == "X" else [digit] for digit in str(status_code).upper()]
    for expanded in product(*chars):
        yield int("".join(expanded))


def expand_status_codes(status_codes: list[str]) -> set[int]:
    return set(chain.from_iterable(expand_status_code(code) for code in status_codes))
