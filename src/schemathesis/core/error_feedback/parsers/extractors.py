"""Shared helpers for parsers."""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import TYPE_CHECKING

from schemathesis.core.error_feedback.store import (
    BoundDirection,
    FormatPayload,
    NumericBoundPayload,
    ObservationKind,
    ObservationPayload,
    ParameterPath,
    SizeBoundPayload,
)
from schemathesis.core.parameters import ParameterLocation
from schemathesis.core.transforms import as_index_or_key

if TYPE_CHECKING:
    from schemathesis.generation.case import Case
    from schemathesis.schemas import APIOperation

ClassificationResult = tuple[ObservationKind, ObservationPayload | None]
RegexHandler = Callable[[re.Match[str]], ClassificationResult]
DictHandler = Callable[[dict], tuple[ClassificationResult, ...]]

# Operations using these methods bind from query, not body; undeclared fields attribute under QUERY for them.
QUERY_METHODS = frozenset({"GET", "DELETE", "HEAD"})


def location_for_method(method: str) -> ParameterLocation:
    return ParameterLocation.QUERY if method.upper() in QUERY_METHODS else ParameterLocation.BODY


def location_for_parameter(operation: APIOperation, parameter_path: ParameterPath, case: Case) -> ParameterLocation:
    if parameter_path and isinstance(parameter_path[0], str):
        name = parameter_path[0]
        # A field the sent body carries belongs to the body; otherwise one named like a declared parameter belongs there.
        if (
            location_for_method(operation.method) is ParameterLocation.BODY
            and isinstance(case.body, dict)
            and name in case.body
        ):
            return ParameterLocation.BODY
        for location in (ParameterLocation.PATH, ParameterLocation.QUERY, ParameterLocation.HEADER):
            if name in operation.get_parameter_set(location):
                return location
    return location_for_method(operation.method)


_FIELD_PATH_SEGMENT = re.compile(r"([^.\[\]]+)|\[(\d+)\]")


def split_field_path(raw: str) -> ParameterPath:
    """`address.street` -> ('address', 'street'); `items[0].name` and `items.0.name` -> ('items', 0, 'name')."""
    segments: list[str | int] = []
    for name, index in _FIELD_PATH_SEGMENT.findall(raw):
        segment = name or index
        segments.append(as_index_or_key(segment))
    return tuple(segments)


def lowercase_first_letter(name: str) -> str:
    """Map a PascalCase identifier (C# property, Go struct field) to its camelCase JSON property name."""
    if not name or not name[0].isupper():
        return name
    return name[0].lower() + name[1:]


def size_bound(*, direction: BoundDirection) -> RegexHandler:
    """Build a regex handler that captures one side of a size bound from match group 1."""

    def handler(match: re.Match[str]) -> ClassificationResult:
        value = int(match.group(1))
        if direction is BoundDirection.MIN:
            payload = SizeBoundPayload(min=value, max=None)
        else:
            payload = SizeBoundPayload(min=None, max=value)
        return ObservationKind.SIZE_BOUND, payload

    return handler


def size_bound_exact() -> RegexHandler:
    """Build a regex handler for "exactly N" size phrasings (Rails wrong_length)."""

    def handler(match: re.Match[str]) -> ClassificationResult:
        value = int(match.group(1))
        return ObservationKind.SIZE_BOUND, SizeBoundPayload(min=value, max=value)

    return handler


def numeric_bound(*, direction: BoundDirection, exclusive: bool) -> RegexHandler:
    """Build a regex handler that emits a NumericBoundPayload from match group 1."""

    def handler(match: re.Match[str]) -> ClassificationResult:
        return ObservationKind.NUMERIC_BOUND, NumericBoundPayload(
            bound=parse_number(match.group(1)),
            direction=direction,
            exclusive=exclusive,
        )

    return handler


def required_handler(_data: dict) -> tuple[ClassificationResult, ...]:
    """A `DictHandler` that emits a single MUST_NOT_BE_BLANK observation regardless of input."""
    return ((ObservationKind.MUST_NOT_BE_BLANK, None),)


def format_handler(format_name: str) -> DictHandler:
    """Build a `DictHandler` that emits a constant FORMAT(name=format_name) observation."""

    def handler(_data: dict) -> tuple[ClassificationResult, ...]:
        return ((ObservationKind.FORMAT, FormatPayload(name=format_name)),)

    return handler


def int_or_none(value: object) -> int | None:
    """Parse a string into an int; return None if the value is not a string or not parseable."""
    if not isinstance(value, str):
        return None
    try:
        return int(value)
    except ValueError:
        return None


def parse_number(text: str) -> int | float:
    """Parse a numeric string, keeping integers exact: a float loses precision above 2**53."""
    try:
        return int(text)
    except ValueError:
        return float(text)


def number_or_none(value: object) -> int | float | None:
    if not isinstance(value, str):
        return None
    try:
        return parse_number(value)
    except ValueError:
        return None
