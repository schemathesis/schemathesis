from __future__ import annotations

import math
import re
from collections.abc import Callable
from dataclasses import dataclass

from packaging import version

from schemathesis.core import string_to_boolean
from schemathesis.core.jsonschema.types import JsonSchema, get_type, to_json_type_name

_NUMERIC_PREFIX = re.compile(r"\d+(?:\.\d+)*")
_PATH_PARAMETER = re.compile(r"\{([^{}]+)\}")
# Sorts below every known spec version, so unrecognized values fall back to the oldest handling.
_UNKNOWN_VERSION = version.parse("0")


def parse_spec_version(value: str) -> version.Version:
    """Parse the numeric part of a spec version, ignoring any suffix the spec allows."""
    match = _NUMERIC_PREFIX.match(value)
    if match is None:
        return _UNKNOWN_VERSION
    return version.parse(match.group())


def parameter_types(schema: JsonSchema) -> list[str]:
    """Types a parameter schema admits, reading `nullable` and `allowEmptyValue` wrappers through."""
    if isinstance(schema, dict) and "type" not in schema:
        if "enum" in schema:
            return [to_json_type_name(value) for value in schema["enum"]]
        if "const" in schema:
            return [to_json_type_name(schema["const"])]
        branches = [*schema.get("anyOf", []), *schema.get("oneOf", [])]
        if branches:
            return [name for branch in branches for name in parameter_types(branch)]
    return get_type(schema)


def coerce_wire_string(value: str, expected_types: list[str]) -> int | float | bool | None:
    """Try to coerce `value` to one of the numeric or boolean types in `expected_types`.

    Returns the coerced value, or `None` if the string is not parseable as any expected type.
    Used to bridge wire-level string transmission (query/header/cookie/path) and
    the JSON-typed schema constraints those parameters declare.
    """
    if "integer" in expected_types:
        try:
            return int(value)
        except (ValueError, TypeError):
            pass
    if "number" in expected_types:
        try:
            return float(value)
        except (ValueError, TypeError):
            pass
    if "boolean" in expected_types:
        # Spellings such as `0`, `yes`, or `True` are read as booleans by most frameworks.
        coerced = string_to_boolean(value)
        if isinstance(coerced, bool):
            return coerced
    return None


def numeric_wire_value_is_valid(coerced: int | float | list[int | float], is_valid: Callable[[object], bool]) -> bool:
    # Values like `nan` or `1e400` parse as non-finite floats that servers may read as numbers.
    values = coerced if isinstance(coerced, list) else [coerced]
    return any(isinstance(value, float) and not math.isfinite(value) for value in values) or is_valid(coerced)


def reads_as_null(text: str, is_valid: Callable[[object], bool]) -> bool:
    """Frameworks read the text `null` into a nullable parameter as null."""
    return text.lower() == "null" and is_valid(None)


def sent_text_is_valid(text: str, is_valid: Callable[[object], bool], expected_types: list[str]) -> bool:
    """Whether a server reading `text` off the wire gets a value `is_valid` accepts."""
    if is_valid(text) or reads_as_null(text, is_valid):
        return True
    # Python's number parsing also accepts `1_0`, ` 5`, or non-ASCII digits, which servers do not.
    if not text.isascii() or "_" in text or text != text.strip():
        return False
    coerced = coerce_wire_string(text, expected_types)
    return coerced is not None and numeric_wire_value_is_valid(coerced, is_valid)


@dataclass(frozen=True)
class PathTemplate:
    """A templated path and a pattern that matches the concrete paths it routes."""

    path: str
    pattern: re.Pattern[str]
    names: tuple[str, ...]


def compile_path_template(path: str) -> PathTemplate:
    # `re.split` with a capturing group alternates literal text and parameter names.
    parts = _PATH_PARAMETER.split(path)
    pattern = "".join(re.escape(part) if index % 2 == 0 else "([^/]+)" for index, part in enumerate(parts))
    return PathTemplate(path=path, pattern=re.compile(pattern), names=tuple(parts[1::2]))
