from __future__ import annotations

import re
from collections.abc import Iterator
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
    from schemathesis.schemas import APIOperation

WalkPair = tuple[tuple[str | int, ...], str]


def _walk(body: object, path: tuple[str | int, ...] = ()) -> Iterator[WalkPair]:
    if isinstance(body, dict):
        for key, value in body.items():
            if not isinstance(key, str):
                continue
            # `non_field_errors` carries object-level (cross-field) errors with no
            # property to attribute — adjustments cannot act on them, skip.
            if key == "non_field_errors":
                continue
            # DRF emits index-keyed errors as ASCII-digit strings ("0", "1", ...).
            # `str.isdigit()` would also accept non-ASCII digits, which we don't want.
            child_key: str | int = int(key) if key.isascii() and key.isdigit() else key
            yield from _walk(value, path + (child_key,))
        return
    if isinstance(body, list):
        had_string = False
        for item in body:
            if isinstance(item, str):
                had_string = True
                if item:
                    yield (path, item)
        if had_string:
            return
        for index, item in enumerate(body):
            if item is None:
                continue
            if isinstance(item, dict) and not item:
                continue
            yield from _walk(item, path + (index,))


_LITERAL_MESSAGES: dict[str, ClassificationResult] = {
    "This field is required.": (ObservationKind.MUST_NOT_BE_BLANK, None),
    "This field may not be blank.": (ObservationKind.MUST_NOT_BE_BLANK, None),
    "This field may not be null.": (ObservationKind.MUST_NOT_BE_BLANK, None),
    "Enter a valid email address.": (ObservationKind.FORMAT, FormatPayload(name="email")),
    "Enter a valid URL.": (ObservationKind.FORMAT, FormatPayload(name="uri")),
    "Must be a valid UUID.": (ObservationKind.FORMAT, FormatPayload(name="uuid")),
    "A valid integer is required.": (
        ObservationKind.TYPE_MISMATCH,
        TypeMismatchPayload(type_name="integer"),
    ),
    "A valid number is required.": (
        ObservationKind.TYPE_MISMATCH,
        TypeMismatchPayload(type_name="number"),
    ),
    "Must be a valid boolean.": (
        ObservationKind.TYPE_MISMATCH,
        TypeMismatchPayload(type_name="boolean"),
    ),
}


# Date/datetime/time and list/dict-type messages have variable suffixes (the
# accepted format strings, the offending Python type), so prefix-match the
# stable lead and ignore the rest.
_PREFIX_MESSAGES: tuple[tuple[str, ClassificationResult], ...] = (
    ("Date has wrong format.", (ObservationKind.FORMAT, FormatPayload(name="date"))),
    ("Datetime has wrong format.", (ObservationKind.FORMAT, FormatPayload(name="date-time"))),
    ("Time has wrong format.", (ObservationKind.FORMAT, FormatPayload(name="time"))),
    (
        "Expected a list of items but got type",
        (ObservationKind.TYPE_MISMATCH, TypeMismatchPayload(type_name="array")),
    ),
    (
        "Expected a dictionary of items",
        (ObservationKind.TYPE_MISMATCH, TypeMismatchPayload(type_name="object")),
    ),
)


# Both DRF native ("field") and Django bridge ("value") wordings; Django's
# `Min/MaxLengthValidator` appends `(it has N)` on either side, so make the
# suffix optional on both min and max patterns.
_REGEX_PATTERNS: tuple[tuple[re.Pattern[str], RegexHandler], ...] = (
    (
        re.compile(r"^Ensure this (?:field|value) has at least (\d+) characters?(?: \(it has \d+\))?\.$"),
        size_bound(direction=BoundDirection.MIN),
    ),
    (
        re.compile(
            r"^Ensure this (?:field|value) has (?:no more than|at most) (\d+) characters?(?: \(it has \d+\))?\.$"
        ),
        size_bound(direction=BoundDirection.MAX),
    ),
    (re.compile(r"^Ensure this field has at least (\d+) elements?\.$"), size_bound(direction=BoundDirection.MIN)),
    (re.compile(r"^Ensure this field has no more than (\d+) elements?\.$"), size_bound(direction=BoundDirection.MAX)),
    (
        re.compile(r"^Ensure this value is greater than or equal to (-?\d+(?:\.\d+)?)\.$"),
        numeric_bound(direction=BoundDirection.MIN, exclusive=False),
    ),
    (
        re.compile(r"^Ensure this value is greater than (-?\d+(?:\.\d+)?)\.$"),
        numeric_bound(direction=BoundDirection.MIN, exclusive=True),
    ),
    (
        re.compile(r"^Ensure this value is less than or equal to (-?\d+(?:\.\d+)?)\.$"),
        numeric_bound(direction=BoundDirection.MAX, exclusive=False),
    ),
    (
        re.compile(r"^Ensure this value is less than (-?\d+(?:\.\d+)?)\.$"),
        numeric_bound(direction=BoundDirection.MAX, exclusive=True),
    ),
)


def _classify(message: str) -> ClassificationResult | None:
    if message in _LITERAL_MESSAGES:
        return _LITERAL_MESSAGES[message]
    for prefix, result in _PREFIX_MESSAGES:
        if message.startswith(prefix):
            return result
    for pattern, handler in _REGEX_PATTERNS:
        match = pattern.match(message)
        if match is not None:
            return handler(match)
    return None


def _has_string_list_leaf(value: object, depth: int = 0) -> bool:
    """Bounded scan for a `list[str]` leaf — the canonical DRF leaf shape."""
    if depth > 16:
        return False
    if isinstance(value, list):
        if any(isinstance(item, str) for item in value):
            return True
        return any(_has_string_list_leaf(item, depth + 1) for item in value)
    if isinstance(value, dict):
        return any(_has_string_list_leaf(v, depth + 1) for v in value.values())
    return False


@PARSERS.register
class DRFParser:
    """Parser for Django REST Framework `ValidationError` envelopes — `{<field>: ["...message..."], ...}`."""

    priority = 3

    def can_parse(self, *, body: object) -> bool:
        if not isinstance(body, dict) or not body:
            return False
        # `{"detail": "..."}` is too generic to claim — let other parsers try.
        if list(body.keys()) == ["detail"] and isinstance(body["detail"], str):
            return False
        if "non_field_errors" in body:
            return True
        return _has_string_list_leaf(body)

    def parse(self, *, operation: APIOperation, body: object) -> tuple[Observation, ...]:
        location = location_for_method(operation.method)
        observations: list[Observation] = []
        for path, message in _walk(body):
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
