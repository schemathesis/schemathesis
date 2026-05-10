from __future__ import annotations

import re
from collections.abc import Iterator, Mapping, Sequence
from typing import TYPE_CHECKING

from schemathesis.core.error_feedback.parsers import PARSERS
from schemathesis.core.error_feedback.parsers.extractors import (
    ClassificationResult,
    RegexHandler,
    location_for_method,
    numeric_bound,
    size_bound,
)
from schemathesis.core.error_feedback.store import (
    BoundDirection,
    FormatPayload,
    Observation,
    ObservationKind,
    TypeMismatchPayload,
)

if TYPE_CHECKING:
    from schemathesis.generation.case import Case
    from schemathesis.schemas import APIOperation

WalkPair = tuple[tuple[str | int, ...], str]
LaravelShape = Mapping[str, Sequence[str]]

# Vocabulary discriminator — substrings that, if found in any message, lock detection to Laravel.
_LARAVEL_VOCABULARY: frozenset[str] = frozenset(
    {
        " field is required.",
        " field must have",
        " field must not have",
        " field format is invalid.",
        " field must be true or false.",
        " field must be a valid email address.",
        " field must be a valid URL.",
        " field must be a valid UUID.",
        " field must be a valid date.",
        " field must be an integer.",
        " field must be a number.",
        " field must be at least ",
        " field must not be greater than ",
        " field must be greater than ",
        " field must be less than ",
        "The selected ",
    }
)


def _walk(body: LaravelShape) -> Iterator[WalkPair]:
    """Yield `(path, message)` pairs; Laravel emits dotted keys for nested attributes."""
    for raw_key, messages in body.items():
        key_path: tuple[str | int, ...] = tuple(raw_key.split(".")) if "." in raw_key else (raw_key,)
        for message in messages:
            if message:
                yield (key_path, message)


def _format(name: str) -> ClassificationResult:
    return ObservationKind.FORMAT, FormatPayload(name=name)


def _type_mismatch(type_name: str) -> ClassificationResult:
    return ObservationKind.TYPE_MISMATCH, TypeMismatchPayload(type_name=type_name)


_STATIC_PATTERNS: tuple[tuple[re.Pattern[str], ClassificationResult], ...] = (
    (re.compile(r"^The .+? field is required\.$"), (ObservationKind.MUST_NOT_BE_BLANK, None)),
    (re.compile(r"^The .+? field must be a valid email address\.$"), _format("email")),
    (re.compile(r"^The .+? field must be a valid URL\.$"), _format("uri")),
    (re.compile(r"^The .+? field must be a valid UUID\.$"), _format("uuid")),
    (re.compile(r"^The .+? field must be a valid date\.$"), _format("date")),
    (re.compile(r"^The .+? field must be an integer\.$"), _type_mismatch("integer")),
    (re.compile(r"^The .+? field must be a number\.$"), _type_mismatch("number")),
    (re.compile(r"^The .+? field must be true or false\.$"), _type_mismatch("boolean")),
)


_DYNAMIC_PATTERNS: tuple[tuple[re.Pattern[str], RegexHandler], ...] = (
    (
        re.compile(r"^The .+? field must be at least (\d+) characters?\.$"),
        size_bound(direction=BoundDirection.MIN),
    ),
    (
        re.compile(r"^The .+? field must not be greater than (\d+) characters?\.$"),
        size_bound(direction=BoundDirection.MAX),
    ),
    (
        re.compile(r"^The .+? field must have at least (\d+) items?\.$"),
        size_bound(direction=BoundDirection.MIN),
    ),
    (
        re.compile(r"^The .+? field must not have more than (\d+) items?\.$"),
        size_bound(direction=BoundDirection.MAX),
    ),
    (
        re.compile(r"^The .+? field must be at least (-?\d+(?:\.\d+)?)\.$"),
        numeric_bound(direction=BoundDirection.MIN, exclusive=False),
    ),
    (
        re.compile(r"^The .+? field must not be greater than (-?\d+(?:\.\d+)?)\.$"),
        numeric_bound(direction=BoundDirection.MAX, exclusive=False),
    ),
    (
        re.compile(r"^The .+? field must be greater than (-?\d+(?:\.\d+)?)\.$"),
        numeric_bound(direction=BoundDirection.MIN, exclusive=True),
    ),
    (
        re.compile(r"^The .+? field must be less than (-?\d+(?:\.\d+)?)\.$"),
        numeric_bound(direction=BoundDirection.MAX, exclusive=True),
    ),
)


def _classify(message: str) -> ClassificationResult | None:
    for pattern, result in _STATIC_PATTERNS:
        if pattern.match(message):
            return result
    for pattern, handler in _DYNAMIC_PATTERNS:
        match = pattern.match(message)
        if match is not None:
            return handler(match)
    return None


def _extract_errors(body: object) -> LaravelShape | None:
    """Return the validated `errors` dict if `body` matches a Laravel envelope; otherwise None."""
    if not isinstance(body, dict):
        return None
    if not isinstance(body.get("message"), str):
        return None
    # ProblemDetails markers (ASP.NET ModelValidation) — disambiguate by absence.
    if "type" in body or "title" in body or "status" in body:
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


def _has_laravel_vocabulary(errors: LaravelShape) -> bool:
    return any(
        phrase in message for messages in errors.values() for message in messages for phrase in _LARAVEL_VOCABULARY
    )


@PARSERS.register
class LaravelParser:
    """Parser for Laravel validation envelopes — `{"message": "...", "errors": {<field>: ["...", ...]}}`."""

    priority = 5

    def can_parse(self, *, body: object) -> bool:
        errors = _extract_errors(body)
        return errors is not None and _has_laravel_vocabulary(errors)

    def parse(self, *, operation: APIOperation, body: object, case: Case) -> tuple[Observation, ...]:
        errors = _extract_errors(body)
        if errors is None:
            return ()
        location = location_for_method(operation.method)
        observations: list[Observation] = []
        for path, message in _walk(errors):
            classification = _classify(message)
            if classification is None:
                continue
            kind, payload = classification
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


__all__ = ["LaravelParser"]
