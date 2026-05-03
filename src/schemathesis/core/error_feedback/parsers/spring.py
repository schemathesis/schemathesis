from __future__ import annotations

import re
from collections.abc import Iterable
from typing import TYPE_CHECKING

from schemathesis.core.error_feedback.parsers import PARSERS
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
    from schemathesis.schemas import APIOperation

# Bean Validation messages we recognize as "this field is required and non-blank":
# Spring's stdlib `must not be blank/null/empty`, plus common custom variants
# (apostrophe-contracted "can't be blank", "must be filled", etc.).
_NON_BLANK = re.compile(
    r"\b(?:(?:must|shall) not be (?:blank|null|empty)"
    r"|can(?:not|'?t) be (?:blank|empty|null)"
    r"|must be filled"
    r"|is required)\b",
    re.IGNORECASE,
)

# Bean Validation `@Size`/`@Length` messages — apply to String, Collection, Map,
# and array; the message text doesn't reveal which, so the consumer branches on
# schema type. Spring stdlib emits "size must be between X and Y"; Hibernate
# Validator's `@Length` emits "length must be between X and Y".
_SIZE_BOUND = re.compile(
    r"\b(?:size|length) must be between (\d+) and (\d+)\b",
    re.IGNORECASE,
)

# Bean-validation format constraints. Hibernate's `@Email` emits "must be a
# well-formed email address"; `@URL`/`@UUID` extensions emit "must be a valid
# URL"/"must be a valid UUID". Custom @ControllerAdvice handlers also use
# softer phrasings like "Please enter a valid e-mail address" — we don't
# require a leading verb, only the `(well-formed|valid) <token>` core.
# Order matters: UUID before URI so the more specific phrase wins on edge
# strings, and email last because its token is short and easiest to misclassify.
_FORMAT_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\b(?:well-formed|valid)\s+UUID\b", re.IGNORECASE), "uuid"),
    (re.compile(r"\b(?:well-formed|valid)\s+(?:URL|URI)\b", re.IGNORECASE), "uri"),
    (re.compile(r"\b(?:well-formed|valid)\s+e-?mail(?:\s+address)?\b", re.IGNORECASE), "email"),
)

# Bean-validation numeric bounds. Hibernate's stdlib emits this single shape
# for `@Min`/`@Max`/`@DecimalMin`/`@DecimalMax` and `@Positive`/`@Negative`/
# `@PositiveOrZero`/`@NegativeOrZero` (the last four expand to "greater/less
# than 0" with the appropriate `or equal to` suffix).
_NUMERIC_BOUND = re.compile(
    r"\bmust be (?P<dir>greater|less) than(?P<inclusive>\s+or\s+equal\s+to)?\s+(?P<value>-?\d+(?:\.\d+)?)\b",
    re.IGNORECASE,
)

# Bean-validation `@Pattern(regexp = "...")` — Hibernate emits the regex
# verbatim between double quotes. Java regex syntax is a superset of ECMA-262;
# the consumer normalizes Java-only constructs before writing it to the schema.
_PATTERN = re.compile(r'\bmust match "(?P<regex>[^"]+)"')


# `MissingServletRequestParameterException` — Spring stdlib emits this when a
# `@RequestParam`-bound field is absent from the request. Always a query param.
_MISSING_PARAMETER = re.compile(
    r"Required\s+\w+\s+parameter\s+'(?P<field>[\w.]+)'\s+is\s+not\s+present",
    re.IGNORECASE,
)

# `MethodArgumentTypeMismatchException` — request-param coercion failure (path
# / query / header). The `Method parameter '<X>':` prefix only appears when
# Spring 6 / Spring Boot 3+ wraps it in RFC 7807 ProblemDetail; the older
# stdlib envelope omits it. We require the prefix because it's the only way
# we get a field name out of the message.
_TYPE_COERCION = re.compile(
    r"Method parameter '(?P<field>[\w.]+)':\s+"
    r"Failed to convert value of type '[\w.$<>]+' to required type '(?P<to>[\w.$<>]+)'",
)


# Custom `@ControllerAdvice` shape: each entry of `messages: [...]` is a string
# of the form `<field> - <message>` (e.g. blog API).
_MESSAGE_LINE = re.compile(r"^\s*([\w.]+)\s*-\s*(.+?)\s*$")

# Top-level keys that carry standalone Spring exception messages (envelope
# shapes from `DefaultErrorAttributes` / RFC 7807 ProblemDetail).
_TOP_LEVEL_KEYS = ("message", "detail", "error")

# RFC 7807 ProblemDetail shape: field/message pairs are embedded inside
# `detail`, e.g. `... [Field error in object 'X' on field 'Y': ...; default message [must not be null]]`.
_PROBLEM_DETAIL = re.compile(
    r"on field '([^']+)':.*?default message \[([^\]]+)\]",
    re.DOTALL,
)


def _split_path(field: str) -> tuple[str | int, ...]:
    # Spring uses dotted paths for nested fields (e.g. `address.street`).
    return tuple(field.split(".")) if field else ()


def _classify(message: str) -> tuple[ObservationKind, ObservationPayload] | None:
    if _NON_BLANK.search(message):
        return ObservationKind.MUST_NOT_BE_BLANK, None
    size_match = _SIZE_BOUND.search(message)
    if size_match:
        return ObservationKind.SIZE_BOUND, SizeBoundPayload(
            min=int(size_match.group(1)),
            max=int(size_match.group(2)),
        )
    numeric_match = _NUMERIC_BOUND.search(message)
    if numeric_match:
        direction = BoundDirection.MIN if numeric_match.group("dir").lower() == "greater" else BoundDirection.MAX
        return ObservationKind.NUMERIC_BOUND, NumericBoundPayload(
            bound=float(numeric_match.group("value")),
            direction=direction,
            exclusive=numeric_match.group("inclusive") is None,
        )
    pattern_match = _PATTERN.search(message)
    if pattern_match:
        return ObservationKind.PATTERN, PatternPayload(regex=pattern_match.group("regex"))
    for pattern, name in _FORMAT_PATTERNS:
        if pattern.search(message):
            return ObservationKind.FORMAT, FormatPayload(name=name)
    return None


def _emit(operation_label: str, field: str, message: str) -> Observation | None:
    classification = _classify(message)
    if classification is None or not field:
        return None
    kind, payload = classification
    return Observation(
        operation_label=operation_label,
        location=ParameterLocation.BODY,
        parameter_path=_split_path(field),
        kind=kind,
        raw_message=message,
        payload=payload,
    )


@PARSERS.register
class SpringParser:
    """Parser for the five empirically-observed Spring 4xx error shapes."""

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
        # `MissingServletRequestParameterException` / `MethodArgumentTypeMismatchException`
        # land in Spring's stdlib envelope (`{message, error, ...}`) or RFC 7807
        # ProblemDetail (`{detail, ...}`) without the field-list shapes above.
        for key in _TOP_LEVEL_KEYS:
            text = body.get(key)
            if isinstance(text, str) and (_MISSING_PARAMETER.search(text) or _TYPE_COERCION.search(text)):
                return True
        return False

    def parse(
        self,
        *,
        operation: APIOperation,
        body: object,
    ) -> tuple[Observation, ...]:
        if not isinstance(body, dict):
            return ()
        operation_label = operation.label
        observations: list[Observation] = []
        observations.extend(self._extract_messages(operation_label, body))
        observations.extend(self._extract_sub_errors(operation_label, body))
        observations.extend(self._extract_problem_detail(operation_label, body))
        observations.extend(self._extract_errors(operation_label, body))
        observations.extend(self._extract_field_errors(operation_label, body))
        observations.extend(self._extract_top_level_message(operation_label, body))
        return tuple(observations)

    @staticmethod
    def _extract_messages(operation_label: str, body: dict) -> Iterable[Observation]:
        # `{"messages": ["<field> - <message>", ...]}` — custom @ControllerAdvice.
        messages = body.get("messages")
        if not isinstance(messages, list):
            return
        for line in messages:
            if not isinstance(line, str):
                continue
            match = _MESSAGE_LINE.match(line)
            if match is None:
                continue
            obsservation = _emit(operation_label, match.group(1), match.group(2))
            if obsservation is not None:
                yield obsservation

    @staticmethod
    def _extract_sub_errors(operation_label: str, body: dict) -> Iterable[Observation]:
        # `{"subErrors": [{"field": "...", "message": "..."}, ...]}` — common
        # validation-error wrapper.
        sub_errors = body.get("subErrors")
        if not isinstance(sub_errors, list):
            return
        for item in sub_errors:
            if not isinstance(item, dict):
                continue
            field = item.get("field") or ""
            message = item.get("message") or ""
            if isinstance(field, str) and isinstance(message, str):
                observations = _emit(operation_label, field, message)
                if observations is not None:
                    yield observations

    @staticmethod
    def _extract_problem_detail(operation_label: str, body: dict) -> Iterable[Observation]:
        # RFC 7807 ProblemDetail — field/message pairs are embedded in `detail`.
        detail = body.get("detail")
        if not isinstance(detail, str):
            return
        for match in _PROBLEM_DETAIL.finditer(detail):
            observations = _emit(operation_label, match.group(1), match.group(2))
            if observations is not None:
                yield observations

    @staticmethod
    def _extract_errors(operation_label: str, body: dict) -> Iterable[Observation]:
        # `{"errors": [{"field": "...", "defaultMessage": "..."}, ...]}` —
        # default Spring Boot Bean Validation shape.
        errors = body.get("errors")
        if not isinstance(errors, list):
            return
        for item in errors:
            if not isinstance(item, dict):
                continue
            field = item.get("field") or ""
            message = item.get("defaultMessage") or item.get("message") or ""
            if isinstance(field, str) and isinstance(message, str):
                observations = _emit(operation_label, field, message)
                if observations is not None:
                    yield observations

    @staticmethod
    def _extract_field_errors(operation_label: str, body: dict) -> Iterable[Observation]:
        # `{"fieldErrors": [{"property": "...", "message": "..."}, ...]}` —
        # wimdeblauwe error-handling-spring-boot-starter shape.
        field_errors = body.get("fieldErrors")
        if not isinstance(field_errors, list):
            return
        for item in field_errors:
            if not isinstance(item, dict):
                continue
            field = item.get("property") or item.get("field") or item.get("path") or ""
            message = item.get("message") or item.get("defaultMessage") or ""
            if isinstance(field, str) and isinstance(message, str):
                observations = _emit(operation_label, field, message)
                if observations is not None:
                    yield observations

    @staticmethod
    def _extract_top_level_message(operation_label: str, body: dict) -> Iterable[Observation]:
        # Spring stdlib / ProblemDetail envelopes carry exception messages directly
        # in `message`/`detail`/`error`. We pull two non-body Spring exceptions
        # out of these strings: a missing query parameter and a request-param
        # type-coercion failure.
        for key in _TOP_LEVEL_KEYS:
            message = body.get(key)
            if not isinstance(message, str):
                continue
            for missing_match in _MISSING_PARAMETER.finditer(message):
                yield Observation(
                    operation_label=operation_label,
                    location=ParameterLocation.QUERY,
                    parameter_path=(missing_match.group("field"),),
                    kind=ObservationKind.MUST_NOT_BE_BLANK,
                    raw_message=message,
                )
            for coercion_match in _TYPE_COERCION.finditer(message):
                # Field name + target Java type. Spring doesn't tell us whether
                # the parameter was bound from the path or query, so we emit on
                # both — the consumer's `_walk_to_property` ignores locations
                # whose schema doesn't declare the field.
                payload = TypeMismatchPayload(type_name=coercion_match.group("to"))
                for location in (ParameterLocation.PATH, ParameterLocation.QUERY):
                    yield Observation(
                        operation_label=operation_label,
                        location=location,
                        parameter_path=(coercion_match.group("field"),),
                        kind=ObservationKind.TYPE_MISMATCH,
                        raw_message=message,
                        payload=payload,
                    )
