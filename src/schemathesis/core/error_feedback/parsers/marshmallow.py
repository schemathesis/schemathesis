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
    size_bound_exact,
)
from schemathesis.core.error_feedback.store import (
    BoundDirection,
    EnumPayload,
    FormatPayload,
    NumericBoundPayload,
    Observation,
    ObservationKind,
    ParameterPath,
    SizeBoundPayload,
    TypeMismatchPayload,
)
from schemathesis.core.parameters import ParameterLocation

if TYPE_CHECKING:
    from schemathesis.generation.case import Case
    from schemathesis.schemas import APIOperation

# Wrapper keys used by webargs / flask-smorest / APIFlask. Treated as wrappers
# only when the value is a dict, so a field literally named `errors` carrying
# a list of messages still attributes correctly.
_ENVELOPE_WRAPPERS: frozenset[str] = frozenset({"errors", "detail", "messages"})

_LOCATION_KEYS: dict[str, ParameterLocation] = {
    "json": ParameterLocation.BODY,
    "form": ParameterLocation.BODY,
    "files": ParameterLocation.BODY,
    "query": ParameterLocation.QUERY,
    "querystring": ParameterLocation.QUERY,
    "headers": ParameterLocation.HEADER,
    "cookies": ParameterLocation.COOKIE,
    "view_args": ParameterLocation.PATH,
    "path": ParameterLocation.PATH,
}

_LITERAL_MESSAGES: dict[str, ClassificationResult] = {
    "Missing data for required field.": (ObservationKind.MUST_NOT_BE_BLANK, None),
    "Field may not be null.": (ObservationKind.MUST_NOT_BE_BLANK, None),
    "Not a valid email address.": (ObservationKind.FORMAT, FormatPayload(name="email")),
    "Not a valid URL.": (ObservationKind.FORMAT, FormatPayload(name="uri")),
    "Not a valid UUID.": (ObservationKind.FORMAT, FormatPayload(name="uuid")),
    "Not a valid date.": (ObservationKind.FORMAT, FormatPayload(name="date")),
    "Not a valid datetime.": (ObservationKind.FORMAT, FormatPayload(name="date-time")),
    "Not a valid time.": (ObservationKind.FORMAT, FormatPayload(name="time")),
    "Not a valid integer.": (ObservationKind.TYPE_MISMATCH, TypeMismatchPayload(type_name="integer")),
    "Not a valid number.": (ObservationKind.TYPE_MISMATCH, TypeMismatchPayload(type_name="number")),
    "Not a valid boolean.": (ObservationKind.TYPE_MISMATCH, TypeMismatchPayload(type_name="boolean")),
    "Not a valid string.": (ObservationKind.TYPE_MISMATCH, TypeMismatchPayload(type_name="string")),
    "Not a valid list.": (ObservationKind.TYPE_MISMATCH, TypeMismatchPayload(type_name="array")),
    "Not a valid tuple.": (ObservationKind.TYPE_MISMATCH, TypeMismatchPayload(type_name="array")),
    "Not a valid mapping type.": (ObservationKind.TYPE_MISMATCH, TypeMismatchPayload(type_name="object")),
}

_LENGTH_RANGE = re.compile(r"^Length must be between (\d+) and (\d+)\.$")
_RANGE_BETWEEN = re.compile(
    r"^Must be greater than(?P<min_inc>\s+or\s+equal\s+to)?\s+(?P<min>-?\d+(?:\.\d+)?) "
    r"and less than(?P<max_inc>\s+or\s+equal\s+to)?\s+(?P<max>-?\d+(?:\.\d+)?)\.$"
)

_REGEX_HANDLERS: tuple[tuple[re.Pattern[str], RegexHandler], ...] = (
    (re.compile(r"^Shorter than minimum length (\d+)\.$"), size_bound(direction=BoundDirection.MIN)),
    (re.compile(r"^Longer than maximum length (\d+)\.$"), size_bound(direction=BoundDirection.MAX)),
    (re.compile(r"^Length must be (\d+)\.$"), size_bound_exact()),
    (
        re.compile(r"^Must be greater than or equal to (-?\d+(?:\.\d+)?)\.$"),
        numeric_bound(direction=BoundDirection.MIN, exclusive=False),
    ),
    (
        re.compile(r"^Must be less than or equal to (-?\d+(?:\.\d+)?)\.$"),
        numeric_bound(direction=BoundDirection.MAX, exclusive=False),
    ),
    (
        re.compile(r"^Must be greater than (-?\d+(?:\.\d+)?)\.$"),
        numeric_bound(direction=BoundDirection.MIN, exclusive=True),
    ),
    (
        re.compile(r"^Must be less than (-?\d+(?:\.\d+)?)\.$"),
        numeric_bound(direction=BoundDirection.MAX, exclusive=True),
    ),
)

_ONEOF = re.compile(r"^Must be one of: (?P<choices>.+?)\.$")


def _classify(message: str) -> tuple[ClassificationResult, ...]:
    """Return classifications for `message` — Range emits two, others zero or one."""
    literal = _LITERAL_MESSAGES.get(message)
    if literal is not None:
        return (literal,)
    range_match = _LENGTH_RANGE.match(message)
    if range_match is not None:
        return (
            (
                ObservationKind.SIZE_BOUND,
                SizeBoundPayload(min=int(range_match.group(1)), max=int(range_match.group(2))),
            ),
        )
    range_pair = _RANGE_BETWEEN.match(message)
    if range_pair is not None:
        return (
            (
                ObservationKind.NUMERIC_BOUND,
                NumericBoundPayload(
                    bound=float(range_pair.group("min")),
                    direction=BoundDirection.MIN,
                    exclusive=range_pair.group("min_inc") is None,
                ),
            ),
            (
                ObservationKind.NUMERIC_BOUND,
                NumericBoundPayload(
                    bound=float(range_pair.group("max")),
                    direction=BoundDirection.MAX,
                    exclusive=range_pair.group("max_inc") is None,
                ),
            ),
        )
    for pattern, handler in _REGEX_HANDLERS:
        match = pattern.match(message)
        if match is not None:
            return (handler(match),)
    oneof_match = _ONEOF.match(message)
    if oneof_match is not None:
        values = tuple(part.strip() for part in oneof_match.group("choices").split(","))
        return ((ObservationKind.ENUM, EnumPayload(values=values)),)
    return ()


def _walk(
    value: object,
    *,
    path: ParameterPath,
    location: ParameterLocation,
    at_root: bool,
) -> Iterator[tuple[ParameterPath, ParameterLocation, str]]:
    if isinstance(value, dict):
        for key, child in value.items():
            if at_root and key in _ENVELOPE_WRAPPERS and isinstance(child, dict):
                yield from _walk(child, path=path, location=location, at_root=True)
                continue
            new_location = _LOCATION_KEYS.get(key)
            if new_location is not None and isinstance(child, dict):
                yield from _walk(child, path=path, location=new_location, at_root=False)
                continue
            child_key: str | int = int(key) if key.isascii() and key.isdigit() else key
            yield from _walk(child, path=(*path, child_key), location=location, at_root=False)
    elif isinstance(value, list):
        if value and all(isinstance(item, str) for item in value):
            for message in value:
                yield path, location, message
            return
        for index, item in enumerate(value):
            yield from _walk(item, path=(*path, index), location=location, at_root=False)


def _has_recognized_message(body: object, *, default_location: ParameterLocation) -> bool:
    for _, _, message in _walk(body, path=(), location=default_location, at_root=True):
        if _classify(message):
            return True
    return False


@PARSERS.register
class MarshmallowParser:
    """Parser for marshmallow validation envelopes — plain, webargs, flask-smorest, APIFlask."""

    priority = 4

    def can_parse(self, *, body: object) -> bool:
        if not isinstance(body, dict) or not body:
            return False
        return _has_recognized_message(body, default_location=ParameterLocation.BODY)

    def parse(self, *, operation: APIOperation, body: object, case: Case) -> tuple[Observation, ...]:
        assert isinstance(body, dict)
        default_location = location_for_method(operation.method)
        observations: list[Observation] = []
        for path, location, message in _walk(body, path=(), location=default_location, at_root=True):
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


__all__ = ["MarshmallowParser"]
