from __future__ import annotations

import re
from collections.abc import Iterable
from typing import TYPE_CHECKING

from schemathesis.core.error_feedback.field_inference import infer_path_from_request
from schemathesis.core.error_feedback.parsers import PARSERS
from schemathesis.core.error_feedback.parsers.extractors import parse_number, split_field_path
from schemathesis.core.error_feedback.store import (
    BoundDirection,
    FormatPayload,
    NumericBoundPayload,
    Observation,
    ObservationKind,
    ObservationPayload,
    PatternPayload,
    SizeBoundPayload,
    TypeMismatchPayload,
)
from schemathesis.core.parameters import ParameterLocation

if TYPE_CHECKING:
    from schemathesis.generation.case import Case
    from schemathesis.schemas import APIOperation

# "Required and non-blank": Bean Validation's `must not be blank/null/empty` and custom forms like "can't be blank".
_NON_BLANK = re.compile(
    r"\b(?:(?:must|shall) not be (?:blank|null|empty)"
    r"|can(?:not|'?t) be (?:blank|empty|null)"
    r"|must be filled"
    r"|is required)\b",
    re.IGNORECASE,
)

# `@Size` / `@Length` text does not say whether the field is a string or a collection; the consumer checks the schema.
_SIZE_BOUND = re.compile(
    r"\b(?:size|length) must be between (\d+) and (\d+)\b",
    re.IGNORECASE,
)

# Hibernate `@Range` uses the same wording without the `size` / `length` prefix, so try it after the size pattern.
_RANGE_BOUND = re.compile(
    r"must be between (-?\d+(?:\.\d+)?) and (-?\d+(?:\.\d+)?)",
    re.IGNORECASE,
)

# Match `(well-formed|valid) <token>` in any phrasing; UUID before URI, and email last as its short token misfires most.
_FORMAT_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\b(?:well-formed|valid)\s+UUID\b", re.IGNORECASE), "uuid"),
    (re.compile(r"\b(?:well-formed|valid)\s+(?:URL|URI)\b", re.IGNORECASE), "uri"),
    (re.compile(r"\b(?:well-formed|valid)\s+e-?mail(?:\s+address)?\b", re.IGNORECASE), "email"),
)

# One Hibernate shape covers `@Min`, `@Max`, `@DecimalMin`, `@DecimalMax`, and `@Positive`-style bounds at 0.
_NUMERIC_BOUND = re.compile(
    r"\bmust be (?P<dir>greater|less) than(?P<inclusive>\s+or\s+equal\s+to)?\s+(?P<value>-?\d+(?:\.\d+)?)\b",
    re.IGNORECASE,
)

# "Value shall be a positive number" and similar `@Positive` / `@Negative` (OrZero) wordings, bound 0.
_NUMERIC_KEYWORD = re.compile(
    r"\b(?:must|shall)\s+be\s+(?:a\s+)?"
    r"(?P<kind>positive|non[-\s]?negative|negative|non[-\s]?positive)"
    r"(?:\s+number|\s+value)?\b",
    re.IGNORECASE,
)
_NUMERIC_KEYWORD_PAYLOADS: dict[str, NumericBoundPayload] = {
    "positive": NumericBoundPayload(bound=0.0, direction=BoundDirection.MIN, exclusive=True),
    "nonnegative": NumericBoundPayload(bound=0.0, direction=BoundDirection.MIN, exclusive=False),
    "negative": NumericBoundPayload(bound=0.0, direction=BoundDirection.MAX, exclusive=True),
    "nonpositive": NumericBoundPayload(bound=0.0, direction=BoundDirection.MAX, exclusive=False),
}

# `@Pattern` quotes the Java regex verbatim; the consumer converts Java-only syntax before writing it to the schema.
_PATTERN = re.compile(r'\bmust match "(?P<regex>[^"]+)"')


# `MissingServletRequestParameterException`: a missing `@RequestParam`, always a query parameter.
_MISSING_PARAMETER = re.compile(
    r"Required\s+\w+\s+parameter\s+'(?P<field>[\w.]+)'\s+is\s+not\s+present",
    re.IGNORECASE,
)

# `HttpMessageNotReadableException` for an empty `@RequestBody`; refers to the whole body.
_MISSING_BODY = re.compile(r"\bRequired\s+request\s+body\s+is\s+missing\b", re.IGNORECASE)

# `MethodArgumentTypeMismatchException`; only Spring 6+ ProblemDetail adds the `Method parameter '<X>':` field name.
_TYPE_COERCION = re.compile(
    r"Method parameter '(?P<field>[\w.]+)':\s+"
    r"Failed to convert value of type '[\w.$<>]+' to required type '(?P<to>[\w.$<>]+)'",
)

# Jackson's `UnrecognizedPropertyException` (body) and Spring's strict-binding
# rejection (query). Custom envelopes occasionally use single quotes.
_UNRECOGNIZED_FIELD = re.compile(r"Unrecognized field:?\s*['\"](?P<name>[^'\"]+)['\"]")
_UNEXPECTED_PARAMETER = re.compile(r"parameter name [\"'](?P<name>[^\"']+)[\"'] is not allowed")

# Java's `NumberFormatException` names the radix only when it is not 10.
_NUMBER_FORMAT = re.compile(r'For input string: "(?P<value>[^"]*)"(?: under radix (?P<radix>\d+))?')
# MongoDB driver rejecting a string that is not a 24-digit hex ObjectId.
_OBJECT_ID = re.compile(r"invalid hexadecimal representation of an ObjectId: \[(?P<value>[^\]]*)\]")
_HEX_PATTERN = PatternPayload(regex="^[0-9a-fA-F]+$")
_OBJECT_ID_PATTERN = PatternPayload(regex="^[0-9a-f]{24}$")


# Custom `@ControllerAdvice` shape: `messages: ["<field> - <message>", ...]`.
_MESSAGE_LINE = re.compile(r"^\s*([\w.]+)\s*-\s*(.+?)\s*$")

# `Parameter 'page' must be 'Integer'` (or 'Long') after a failed `@RequestParam` coercion gives the type's maximum.
_PARAMETER_TYPE_HINT = re.compile(r"Parameter '(?P<param>\w+)' must be '(?P<type>Integer|Long)'")
_JAVA_TYPE_MAX = {"Integer": 2_147_483_647, "Long": 9_223_372_036_854_775_807}

# Top-level keys with a standalone Spring exception message; custom `@ControllerAdvice` envelopes use `msg`.
_TOP_LEVEL_KEYS = ("message", "detail", "error", "msg")

# ProblemDetail embeds field errors in `detail`: `... on field 'Y': ...; default message [must not be null]]`.
_PROBLEM_DETAIL = re.compile(
    r"on field '([^']+)':.*?default message \[([^\]]+)\]",
    re.DOTALL,
)


# Error arrays, and the keys of their entries, that carry a user-facing message.
_CARRIER_ARRAY_KEYS = ("fieldErrors", "errors", "subErrors")
_CARRIER_ITEM_KEYS = ("message", "defaultMessage")


def _iter_carrier_strings(body: dict) -> Iterable[str]:
    for key in _TOP_LEVEL_KEYS:
        value = body.get(key)
        if isinstance(value, str):
            yield value
    for array_key in _CARRIER_ARRAY_KEYS:
        items = body.get(array_key)
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            for key in _CARRIER_ITEM_KEYS:
                value = item.get(key)
                if isinstance(value, str):
                    yield value


def _classify(message: str) -> tuple[tuple[ObservationKind, ObservationPayload], ...]:
    if _NON_BLANK.search(message):
        return ((ObservationKind.MUST_NOT_BE_BLANK, None),)
    size_match = _SIZE_BOUND.search(message)
    if size_match:
        return (
            (
                ObservationKind.SIZE_BOUND,
                SizeBoundPayload(min=int(size_match.group(1)), max=int(size_match.group(2))),
            ),
        )
    range_match = _RANGE_BOUND.search(message)
    if range_match:
        return (
            (
                ObservationKind.NUMERIC_BOUND,
                NumericBoundPayload(
                    bound=parse_number(range_match.group(1)), direction=BoundDirection.MIN, exclusive=False
                ),
            ),
            (
                ObservationKind.NUMERIC_BOUND,
                NumericBoundPayload(
                    bound=parse_number(range_match.group(2)), direction=BoundDirection.MAX, exclusive=False
                ),
            ),
        )
    numeric_match = _NUMERIC_BOUND.search(message)
    if numeric_match:
        direction = BoundDirection.MIN if numeric_match.group("dir").lower() == "greater" else BoundDirection.MAX
        return (
            (
                ObservationKind.NUMERIC_BOUND,
                NumericBoundPayload(
                    bound=parse_number(numeric_match.group("value")),
                    direction=direction,
                    exclusive=numeric_match.group("inclusive") is None,
                ),
            ),
        )
    keyword_match = _NUMERIC_KEYWORD.search(message)
    if keyword_match:
        # Normalise `non-negative` / `non negative` to a single key.
        kind = re.sub(r"[-\s]+", "", keyword_match.group("kind").lower())
        return ((ObservationKind.NUMERIC_BOUND, _NUMERIC_KEYWORD_PAYLOADS[kind]),)
    pattern_match = _PATTERN.search(message)
    if pattern_match:
        return ((ObservationKind.PATTERN, PatternPayload(regex=pattern_match.group("regex"))),)
    for pattern, name in _FORMAT_PATTERNS:
        if pattern.search(message):
            return ((ObservationKind.FORMAT, FormatPayload(name=name)),)
    return ()


def _emit(operation: APIOperation, field: str, message: str) -> list[Observation]:
    if not field:
        return []
    # Spring envelopes carry no location: use the declared parameter with this name, else the body.
    if field in operation.path_parameters:
        location = ParameterLocation.PATH
    elif field in operation.query:
        location = ParameterLocation.QUERY
    else:
        location = ParameterLocation.BODY
    return [
        Observation(
            operation_label=operation.label,
            location=location,
            parameter_path=split_field_path(field),
            kind=kind,
            raw_message=message,
            payload=payload,
        )
        for kind, payload in _classify(message)
    ]


@PARSERS.register
class SpringParser:
    """Parser for the five Spring 4xx error shapes seen in practice."""

    priority = 10

    def can_parse(self, *, body: object) -> bool:
        if not isinstance(body, dict):
            return False
        messages = body.get("messages")
        if isinstance(messages, list) and all(isinstance(m, str) for m in messages):
            return True
        if isinstance(body.get("subErrors"), list):
            return True
        if isinstance(body.get("detail"), str) and "Field error in object" in body["detail"]:
            return True
        errors = body.get("errors")
        if isinstance(errors, list) and errors and isinstance(errors[0], dict):
            return True
        if isinstance(body.get("fieldErrors"), list):
            return True
        # Exception messages in `{message, error, ...}` or ProblemDetail `{detail, ...}`, without a field list.
        for key in _TOP_LEVEL_KEYS:
            text = body.get(key)
            if isinstance(text, str) and (
                _MISSING_PARAMETER.search(text)
                or _MISSING_BODY.search(text)
                or _TYPE_COERCION.search(text)
                or _UNRECOGNIZED_FIELD.search(text)
                or _UNEXPECTED_PARAMETER.search(text)
                or _NUMBER_FORMAT.search(text)
                or _OBJECT_ID.search(text)
            ):
                return True
        return False

    def parse(
        self,
        *,
        operation: APIOperation,
        body: object,
        case: Case,
    ) -> tuple[Observation, ...]:
        if not isinstance(body, dict):
            return ()
        observations: list[Observation] = []
        observations.extend(self._extract_messages(operation, body))
        observations.extend(self._extract_sub_errors(operation, body))
        observations.extend(self._extract_problem_detail(operation, body))
        observations.extend(self._extract_errors(operation, body))
        observations.extend(self._extract_field_errors(operation, body))
        observations.extend(self._extract_top_level_message(operation, body))
        observations.extend(self._extract_unexpected_properties(operation, body))
        observations.extend(self._extract_value_parse_errors(operation, body, case))
        return tuple(observations)

    @staticmethod
    def _extract_messages(operation: APIOperation, body: dict) -> Iterable[Observation]:
        # Custom `@ControllerAdvice`: `{"messages": ["<field> - <message>", ...]}`.
        messages = body.get("messages")
        if not isinstance(messages, list):
            return
        for line in messages:
            if not isinstance(line, str):
                continue
            type_hint = _PARAMETER_TYPE_HINT.search(line)
            if type_hint is not None:
                yield Observation(
                    operation_label=operation.label,
                    location=ParameterLocation.QUERY,
                    parameter_path=(type_hint.group("param"),),
                    kind=ObservationKind.NUMERIC_BOUND,
                    raw_message=line,
                    payload=NumericBoundPayload(
                        bound=_JAVA_TYPE_MAX[type_hint.group("type")],
                        direction=BoundDirection.MAX,
                        exclusive=False,
                    ),
                )
                continue
            match = _MESSAGE_LINE.match(line)
            if match is None:
                continue
            yield from _emit(operation, match.group(1), match.group(2))

    @staticmethod
    def _extract_sub_errors(operation: APIOperation, body: dict) -> Iterable[Observation]:
        # Common validation-error wrapper: `{"subErrors": [{"field": "...", "message": "..."}, ...]}`.
        sub_errors = body.get("subErrors")
        if not isinstance(sub_errors, list):
            return
        for item in sub_errors:
            if not isinstance(item, dict):
                continue
            field = item.get("field") or ""
            message = item.get("message") or ""
            if isinstance(field, str) and isinstance(message, str):
                yield from _emit(operation, field, message)

    @staticmethod
    def _extract_problem_detail(operation: APIOperation, body: dict) -> Iterable[Observation]:
        # RFC 7807 ProblemDetail embeds field/message pairs in `detail`.
        detail = body.get("detail")
        if not isinstance(detail, str):
            return
        for match in _PROBLEM_DETAIL.finditer(detail):
            yield from _emit(operation, match.group(1), match.group(2))

    @staticmethod
    def _extract_errors(operation: APIOperation, body: dict) -> Iterable[Observation]:
        # Spring Boot Bean Validation default: `{"errors": [{"field": "...", "defaultMessage": "..."}, ...]}`.
        errors = body.get("errors")
        if not isinstance(errors, list):
            return
        for item in errors:
            if not isinstance(item, dict):
                continue
            field = item.get("field") or ""
            message = item.get("defaultMessage") or item.get("message") or ""
            if isinstance(field, str) and isinstance(message, str):
                yield from _emit(operation, field, message)

    @staticmethod
    def _extract_field_errors(operation: APIOperation, body: dict) -> Iterable[Observation]:
        # error-handling-spring-boot-starter: `{"fieldErrors": [{"property": "...", "message": "..."}, ...]}`.
        field_errors = body.get("fieldErrors")
        if not isinstance(field_errors, list):
            return
        for item in field_errors:
            if not isinstance(item, dict):
                continue
            field = item.get("property") or item.get("field") or item.get("path") or ""
            message = item.get("message") or item.get("defaultMessage") or ""
            if isinstance(field, str) and isinstance(message, str):
                yield from _emit(operation, field, message)

    @staticmethod
    def _extract_top_level_message(operation: APIOperation, body: dict) -> Iterable[Observation]:
        # Top-level exception messages: a missing query parameter or a request parameter coercion failure.
        for key in _TOP_LEVEL_KEYS:
            message = body.get(key)
            if not isinstance(message, str):
                continue
            for missing_match in _MISSING_PARAMETER.finditer(message):
                yield Observation(
                    operation_label=operation.label,
                    location=ParameterLocation.QUERY,
                    parameter_path=(missing_match.group("field"),),
                    kind=ObservationKind.MUST_NOT_BE_BLANK,
                    raw_message=message,
                )
            if _MISSING_BODY.search(message):
                # Empty `parameter_path`: the body itself is required.
                yield Observation(
                    operation_label=operation.label,
                    location=ParameterLocation.BODY,
                    parameter_path=(),
                    kind=ObservationKind.MUST_NOT_BE_BLANK,
                    raw_message=message,
                )
            for coercion_match in _TYPE_COERCION.finditer(message):
                # The message omits path vs query, so emit both; the consumer ignores locations lacking the field.
                payload = TypeMismatchPayload(type_name=coercion_match.group("to"))
                for location in (ParameterLocation.PATH, ParameterLocation.QUERY):
                    yield Observation(
                        operation_label=operation.label,
                        location=location,
                        parameter_path=(coercion_match.group("field"),),
                        kind=ObservationKind.TYPE_MISMATCH,
                        raw_message=message,
                        payload=payload,
                    )

    @staticmethod
    def _extract_unexpected_properties(operation: APIOperation, body: dict) -> Iterable[Observation]:
        # `Unrecognized field` (body) and `parameter name "X" is not allowed` (query) name a property to drop.
        for message in _iter_carrier_strings(body):
            for match in _UNRECOGNIZED_FIELD.finditer(message):
                yield Observation(
                    operation_label=operation.label,
                    location=ParameterLocation.BODY,
                    parameter_path=(match.group("name"),),
                    kind=ObservationKind.UNEXPECTED_PROPERTY,
                    raw_message=message,
                )
                # The server rejects unknown keys, so invented extras would be refused too.
                yield Observation(
                    operation_label=operation.label,
                    location=ParameterLocation.BODY,
                    parameter_path=(),
                    kind=ObservationKind.FORBIDS_ADDITIONAL_PROPERTIES,
                    raw_message=message,
                )
            for match in _UNEXPECTED_PARAMETER.finditer(message):
                yield Observation(
                    operation_label=operation.label,
                    location=ParameterLocation.QUERY,
                    parameter_path=(match.group("name"),),
                    kind=ObservationKind.UNEXPECTED_PROPERTY,
                    raw_message=message,
                )

    @staticmethod
    def _extract_value_parse_errors(operation: APIOperation, body: dict, case: Case) -> Iterable[Observation]:
        # Number and ObjectId parse errors name the rejected value but not the field, so find the slot that sent it.
        for key in _TOP_LEVEL_KEYS:
            message = body.get(key)
            # A coercion message with a field name is already handled.
            if not isinstance(message, str) or _TYPE_COERCION.search(message):
                continue
            for value, kind, payload in _iter_value_parse_errors(message):
                slot = infer_path_from_request(case=case, rejected_value=value)
                if slot is None:
                    continue
                location, path = slot
                yield Observation(
                    operation_label=operation.label,
                    location=location,
                    parameter_path=path,
                    kind=kind,
                    raw_message=message,
                    payload=payload,
                )


def _iter_value_parse_errors(message: str) -> Iterable[tuple[str, ObservationKind, ObservationPayload]]:
    for match in _NUMBER_FORMAT.finditer(message):
        radix = match.group("radix")
        if radix is None:
            yield match.group("value"), ObservationKind.TYPE_MISMATCH, TypeMismatchPayload(type_name="integer")
        elif radix == "16":
            yield match.group("value"), ObservationKind.PATTERN, _HEX_PATTERN
    for match in _OBJECT_ID.finditer(message):
        yield match.group("value"), ObservationKind.PATTERN, _OBJECT_ID_PATTERN
