"""Shared regex helpers for prose-classifying parsers."""

from __future__ import annotations

import re
from collections.abc import Callable

from schemathesis.core.error_feedback.store import (
    BoundDirection,
    NumericBoundPayload,
    ObservationKind,
    ObservationPayload,
    SizeBoundPayload,
)
from schemathesis.core.parameters import ParameterLocation

ClassificationResult = tuple[ObservationKind, ObservationPayload | None]
RegexHandler = Callable[[re.Match[str]], ClassificationResult]

# Operations using these methods bind from query, not body — observations
# attribute under QUERY for them.
QUERY_METHODS = frozenset({"GET", "DELETE", "HEAD"})


def location_for_method(method: str) -> ParameterLocation:
    return ParameterLocation.QUERY if method.upper() in QUERY_METHODS else ParameterLocation.BODY


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
