from __future__ import annotations

import re
from collections.abc import Iterator, Mapping, Sequence
from typing import TYPE_CHECKING

from schemathesis.core.error_feedback.parsers import PARSERS
from schemathesis.core.error_feedback.parsers.extractors import (
    ClassificationResult,
    RegexHandler,
    location_for_method,
    lowercase_first_letter,
    numeric_bound,
    size_bound,
)
from schemathesis.core.error_feedback.store import (
    BoundDirection,
    FormatPayload,
    NumericBoundPayload,
    Observation,
    ObservationKind,
    PatternPayload,
    SizeBoundPayload,
)

if TYPE_CHECKING:
    from schemathesis.schemas import APIOperation

WalkPair = tuple[tuple[str | int, ...], str]
AspNetShape = Mapping[str, Sequence[object]]

# Vocabulary discriminator — substrings that lock detection to ASP.NET / FluentValidation.
_ASPNET_VOCABULARY: frozenset[str] = frozenset(
    {
        " field is required.",
        " field is not a valid",
        "must be a string or array type",
        "must be a string with a minimum length of",
        "must match the regular expression",
        " must not be empty.",
        " is not a valid email address.",
        "The length of '",
        " must be greater than '",
        " must be less than '",
        " must be between ",
    }
)

# `$.<path>` keys and the `input` placeholder come from JSON deserialization, not schema validation.
_PSEUDO_FIELDS: frozenset[str] = frozenset({"input"})


def _is_pseudo_field(name: str) -> bool:
    return name in _PSEUDO_FIELDS or name.startswith("$.")


def _walk(errors: AspNetShape) -> Iterator[WalkPair]:
    for raw_key, messages in errors.items():
        if _is_pseudo_field(raw_key):
            continue
        field = lowercase_first_letter(raw_key)
        for message in messages:
            if isinstance(message, str) and message:
                yield ((field,), message)


def _format(name: str) -> ClassificationResult:
    return ObservationKind.FORMAT, FormatPayload(name=name)


_STATIC_PATTERNS: tuple[tuple[re.Pattern[str], ClassificationResult], ...] = (
    (re.compile(r"^The .+? field is required\.$"), (ObservationKind.MUST_NOT_BE_BLANK, None)),
    (re.compile(r"^The .+? field is not a valid e-mail address\.$"), _format("email")),
    (re.compile(r"^'.+?' must not be empty\.$"), (ObservationKind.MUST_NOT_BE_BLANK, None)),
    (re.compile(r"^'.+?' is not a valid email address\.$"), _format("email")),
)


def _string_length_range(match: re.Match[str]) -> ClassificationResult:
    return ObservationKind.SIZE_BOUND, SizeBoundPayload(min=int(match.group(1)), max=int(match.group(2)))


def _regex_pattern(match: re.Match[str]) -> ClassificationResult:
    return ObservationKind.PATTERN, PatternPayload(regex=match.group(1))


_DYNAMIC_PATTERNS: tuple[tuple[re.Pattern[str], RegexHandler], ...] = (
    (
        re.compile(r"^The field .+? must be a string or array type with a minimum length of '(\d+)'\.$"),
        size_bound(direction=BoundDirection.MIN),
    ),
    (
        re.compile(r"^The field .+? must be a string or array type with a maximum length of '(\d+)'\.$"),
        size_bound(direction=BoundDirection.MAX),
    ),
    (
        re.compile(r"^The field .+? must be a string with a minimum length of (\d+) and a maximum length of (\d+)\.$"),
        _string_length_range,
    ),
    (
        re.compile(r"^The length of '.+?' must be at least (\d+) characters?\..*$"),
        size_bound(direction=BoundDirection.MIN),
    ),
    (
        re.compile(r"^The length of '.+?' must be (\d+) characters? or fewer\..*$"),
        size_bound(direction=BoundDirection.MAX),
    ),
    (
        re.compile(r"^'.+?' must be greater than '?(-?\d+(?:\.\d+)?)'?\.$"),
        numeric_bound(direction=BoundDirection.MIN, exclusive=True),
    ),
    (
        re.compile(r"^'.+?' must be less than '?(-?\d+(?:\.\d+)?)'?\.$"),
        numeric_bound(direction=BoundDirection.MAX, exclusive=True),
    ),
    (
        re.compile(r"^The field .+? must match the regular expression '(.+)'\.$"),
        _regex_pattern,
    ),
)

_INCLUSIVE_RANGE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^The field .+? must be between (-?\d+(?:\.\d+)?) and (-?\d+(?:\.\d+)?)\.$"),
    re.compile(r"^'.+?' must be between (-?\d+(?:\.\d+)?) and (-?\d+(?:\.\d+)?)\..*$"),
)


def _classify(message: str) -> tuple[ClassificationResult, ...]:
    for pattern, result in _STATIC_PATTERNS:
        if pattern.match(message):
            return (result,)
    for pattern, handler in _DYNAMIC_PATTERNS:
        match = pattern.match(message)
        if match is not None:
            return (handler(match),)
    for pattern in _INCLUSIVE_RANGE_PATTERNS:
        match = pattern.match(message)
        if match is not None:
            low = float(match.group(1))
            high = float(match.group(2))
            return (
                (
                    ObservationKind.NUMERIC_BOUND,
                    NumericBoundPayload(bound=low, direction=BoundDirection.MIN, exclusive=False),
                ),
                (
                    ObservationKind.NUMERIC_BOUND,
                    NumericBoundPayload(bound=high, direction=BoundDirection.MAX, exclusive=False),
                ),
            )
    return ()


def _extract_errors(body: object) -> AspNetShape | None:
    """Return the validated `errors` dict if `body` matches an ASP.NET ProblemDetails envelope; otherwise None."""
    if not isinstance(body, dict):
        return None
    # RFC 7807 ProblemDetails markers.
    if "type" not in body and "title" not in body and "status" not in body:
        return None
    errors = body.get("errors")
    if not isinstance(errors, dict) or not errors:
        return None
    for value in errors.values():
        if not isinstance(value, list):
            return None
        if not all(isinstance(item, str) for item in value):
            return None
    return errors


def _has_aspnet_vocabulary(errors: AspNetShape) -> bool:
    return any(
        phrase in message
        for messages in errors.values()
        for message in messages
        if isinstance(message, str)
        for phrase in _ASPNET_VOCABULARY
    )


@PARSERS.register
class AspNetParser:
    """Parser for ASP.NET ModelValidation envelopes — RFC 7807 ProblemDetails wrapping per-field errors."""

    priority = 6

    def can_parse(self, *, body: object) -> bool:
        errors = _extract_errors(body)
        return errors is not None and _has_aspnet_vocabulary(errors)

    def parse(self, *, operation: APIOperation, body: object) -> tuple[Observation, ...]:
        errors = _extract_errors(body)
        if errors is None:
            return ()
        location = location_for_method(operation.method)
        observations: list[Observation] = []
        for path, message in _walk(errors):
            for kind, payload in _classify(message):
                observations.append(
                    Observation(
                        operation_label=operation.label,
                        location=location,
                        parameter_path=path,
                        kind=kind,
                        raw_message=message,
                        payload=payload,
                    )
                )
        return tuple(observations)


__all__ = ["AspNetParser"]
