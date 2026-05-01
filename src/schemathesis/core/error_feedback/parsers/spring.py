from __future__ import annotations

import re
from collections.abc import Iterable

from schemathesis.core.error_feedback.parsers import PARSERS
from schemathesis.core.error_feedback.store import (
    BoundDirection,
    FormatPayload,
    NumericBoundPayload,
    Observation,
    ObservationKind,
    ObservationPayload,
    SizeBoundPayload,
)
from schemathesis.core.parameters import ParameterLocation

# Bean Validation messages we recognize as "this field is required and non-blank":
# Spring's stdlib `must not be blank/null/empty`, plus common custom variants.
_NON_BLANK = re.compile(
    r"\b(?:must not be (?:blank|null|empty)|cannot be empty|is required)\b",
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

# Bean-validation format constraints. Hibernate's `@Email` emits
# "must be a well-formed email address"; the `@URL`/`@UUID` extensions emit
# "must be a valid URL"/"must be a valid UUID". The phrasing varies between
# stdlib and custom annotations, hence the optional `well-formed`/`valid`.
# Order matters: UUID before URI so the more specific phrase wins on edge
# strings, and email last because its token is short and easiest to misclassify.
_FORMAT_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bmust be (?:a |an )?(?:well-formed |valid )?UUID\b", re.IGNORECASE), "uuid"),
    (re.compile(r"\bmust be (?:a |an )?(?:well-formed |valid )?(?:URL|URI)\b", re.IGNORECASE), "uri"),
    (
        re.compile(r"\bmust be (?:a |an )?(?:well-formed |valid )?email(?:\s+address)?\b", re.IGNORECASE),
        "email",
    ),
)

# Bean-validation numeric bounds. Hibernate's stdlib emits this single shape
# for `@Min`/`@Max`/`@DecimalMin`/`@DecimalMax` and `@Positive`/`@Negative`/
# `@PositiveOrZero`/`@NegativeOrZero` (the last four expand to "greater/less
# than 0" with the appropriate `or equal to` suffix).
_NUMERIC_BOUND = re.compile(
    r"\bmust be (?P<dir>greater|less) than(?P<inclusive>\s+or\s+equal\s+to)?\s+(?P<value>-?\d+(?:\.\d+)?)\b",
    re.IGNORECASE,
)


# Custom `@ControllerAdvice` shape: each entry of `messages: [...]` is a string
# of the form `<field> - <message>` (e.g. blog API).
_MESSAGE_LINE = re.compile(r"^\s*([\w.]+)\s*-\s*(.+?)\s*$")

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
        return False

    def parse(
        self,
        *,
        operation_label: str,
        body: object,
    ) -> tuple[Observation, ...]:
        if not isinstance(body, dict):
            return ()
        observations: list[Observation] = []
        observations.extend(self._extract_messages(operation_label, body))
        observations.extend(self._extract_sub_errors(operation_label, body))
        observations.extend(self._extract_problem_detail(operation_label, body))
        observations.extend(self._extract_errors(operation_label, body))
        observations.extend(self._extract_field_errors(operation_label, body))
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
