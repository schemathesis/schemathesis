from __future__ import annotations

import re
from typing import TYPE_CHECKING

from schemathesis.core.error_feedback.parsers import PARSERS
from schemathesis.core.error_feedback.parsers.extractors import location_for_method
from schemathesis.core.error_feedback.store import (
    Observation,
    ObservationKind,
    ParameterPath,
    PatternPayload,
    SizeBoundPayload,
)

if TYPE_CHECKING:
    from schemathesis.generation.case import Case
    from schemathesis.schemas import APIOperation

# Confluent's `error_code` runs in two ranges: 4xx HTTP mirrors plus a 5-digit
# extended namespace. Skip 5xx in either range — those signal broker / runtime
# faults, not request shape.
_HTTP_STATUS_LOW = 400
_HTTP_STATUS_HIGH = 500
_EXTENDED_LOW = 40000
_EXTENDED_HIGH = 50000

_TOPIC_NAME_REGEX = "^[a-zA-Z0-9._-]+$"

_COERCE_EMPTY_STRING = re.compile(r'Cannot coerce empty String \(""\) to `\w+` value')
# Recognized but not actionable: cannot pin a field path without per-endpoint
# knowledge of the root container.
_PAYLOAD_NULL_OR_EMPTY = re.compile(r"Payload error\. (?:Null|Empty) input provided\.|Request body is empty")
_EMPTY_BATCH = re.compile(r"^Empty batch\.?$")
_BATCH_ENTRY_IDENTIFIER = re.compile(r"^Batch entry identifier is not a valid string\.?$")
# `<field> cannot be <value>` — recognized but not emitted: narrowing an enum
# requires the allowed-value list, which the message doesn't carry.
_FIELD_CANNOT_BE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]* cannot be [A-Z][A-Z0-9_]*$")
# Two wordings observed across releases; second arm is the post-collapse form.
_TOPIC_NAME_INVALID = re.compile(
    r"Topic name is invalid:.*characters other than ASCII alphanumerics|Payload error\. Invalid topic name\."
)

_TOPIC_NAME_FIELD: ParameterPath = ("topic_name",)
_DATA_FIELD: ParameterPath = ("data",)


def _is_confluent_envelope(body: object) -> tuple[int, str] | None:
    if not isinstance(body, dict):
        return None
    error_code = body.get("error_code")
    if not isinstance(error_code, int) or isinstance(error_code, bool):
        return None
    if not (_HTTP_STATUS_LOW <= error_code < _HTTP_STATUS_HIGH or _EXTENDED_LOW <= error_code < _EXTENDED_HIGH):
        return None
    message = body.get("message")
    if not isinstance(message, str):
        return None
    return error_code, message


def _message_recognized(message: str) -> bool:
    return any(
        pattern.search(message) is not None
        for pattern in (
            _COERCE_EMPTY_STRING,
            _PAYLOAD_NULL_OR_EMPTY,
            _EMPTY_BATCH,
            _BATCH_ENTRY_IDENTIFIER,
            _FIELD_CANNOT_BE,
            _TOPIC_NAME_INVALID,
        )
    )


def _find_unique_empty_string_path(body: object) -> ParameterPath | None:
    """Locate the single body slot whose value is `""`; ambiguous matches drop."""
    matches: list[ParameterPath] = []
    stack: list[tuple[ParameterPath, object]] = [((), body)]
    while stack:
        path, current = stack.pop()
        if isinstance(current, dict):
            for key, value in current.items():
                if isinstance(key, str):
                    stack.append(((*path, key), value))
        elif isinstance(current, list):
            for index, value in enumerate(current):
                stack.append(((*path, index), value))
        elif current == "":
            matches.append(path)
            if len(matches) > 1:
                return None
    return matches[0] if len(matches) == 1 else None


@PARSERS.register
class ConfluentParser:
    """Parser for Confluent REST Proxy `{error_code, message}` envelopes."""

    priority = 5

    def can_parse(self, *, body: object) -> bool:
        envelope = _is_confluent_envelope(body)
        if envelope is None:
            return False
        return _message_recognized(envelope[1])

    def parse(self, *, operation: APIOperation, body: object, case: Case) -> tuple[Observation, ...]:
        envelope = _is_confluent_envelope(body)
        if envelope is None:
            return ()
        _, message = envelope
        location = location_for_method(operation.method)
        observations: list[Observation] = []

        if _COERCE_EMPTY_STRING.search(message) is not None:
            path = _find_unique_empty_string_path(case.body)
            if path is not None:
                observations.append(
                    Observation(
                        operation_label=operation.label,
                        location=location,
                        parameter_path=path,
                        kind=ObservationKind.SIZE_BOUND,
                        raw_message=message,
                        payload=SizeBoundPayload(min=1, max=None),
                    )
                )
        elif _EMPTY_BATCH.search(message) is not None:
            observations.append(
                Observation(
                    operation_label=operation.label,
                    location=location,
                    parameter_path=_DATA_FIELD,
                    kind=ObservationKind.SIZE_BOUND,
                    raw_message=message,
                    payload=SizeBoundPayload(min=1, max=None),
                )
            )
        elif _TOPIC_NAME_INVALID.search(message) is not None:
            observations.append(
                Observation(
                    operation_label=operation.label,
                    location=location,
                    parameter_path=_TOPIC_NAME_FIELD,
                    kind=ObservationKind.PATTERN,
                    raw_message=message,
                    payload=PatternPayload(regex=_TOPIC_NAME_REGEX),
                )
            )

        return tuple(observations)


__all__ = ["ConfluentParser"]
