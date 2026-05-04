"""Shared helpers for parsers."""

from __future__ import annotations

import re
from collections.abc import Callable

from schemathesis.core.error_feedback.store import (
    BoundDirection,
    FormatPayload,
    NumericBoundPayload,
    ObservationKind,
    ObservationPayload,
    SizeBoundPayload,
)
from schemathesis.core.parameters import ParameterLocation

ClassificationResult = tuple[ObservationKind, ObservationPayload | None]
RegexHandler = Callable[[re.Match[str]], ClassificationResult]
DictHandler = Callable[[dict], tuple[ClassificationResult, ...]]

# Operations using these methods bind from query, not body — observations
# attribute under QUERY for them.
QUERY_METHODS = frozenset({"GET", "DELETE", "HEAD"})


def location_for_method(method: str) -> ParameterLocation:
    return ParameterLocation.QUERY if method.upper() in QUERY_METHODS else ParameterLocation.BODY


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
            bound=float(match.group(1)),
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


def float_or_none(value: object) -> float | None:
    """Parse a string into a float; return None if the value is not a string or not parseable."""
    if not isinstance(value, str):
        return None
    try:
        return float(value)
    except ValueError:
        return None
