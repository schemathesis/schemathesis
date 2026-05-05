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
    from schemathesis.generation.case import Case
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

# `Value shall be a positive number` and friends — wraps `@Positive` /
# `@PositiveOrZero` (and the negative mirrors) at bound=0.
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

# Jackson's `UnrecognizedPropertyException` (body) and Spring's strict-binding
# rejection (query). Custom envelopes occasionally use single quotes.
_UNRECOGNIZED_FIELD = re.compile(r"Unrecognized field:\s*['\"](?P<name>[^'\"]+)['\"]")
_UNEXPECTED_PARAMETER = re.compile(r"parameter name [\"'](?P<name>[^\"']+)[\"'] is not allowed")


# Custom `@ControllerAdvice` shape: each entry of `messages: [...]` is a string
# of the form `<field> - <message>` (e.g. blog API).
_MESSAGE_LINE = re.compile(r"^\s*([\w.]+)\s*-\s*(.+?)\s*$")

# Pagination-style type hint: `Parameter 'page' must be 'Integer'` (or 'Long').
# Spring emits this when a `@RequestParam` int/long fails coercion. The hint
# alone gives us the language-level upper bound for the field.
_PARAMETER_TYPE_HINT = re.compile(r"Parameter '(?P<param>\w+)' must be '(?P<type>Integer|Long)'")
_JAVA_TYPE_MAX = {"Integer": 2_147_483_647, "Long": 9_223_372_036_854_775_807}

# Top-level keys that carry standalone Spring exception messages: standard
# `message`/`detail`/`error` shapes plus `msg` used by custom @ControllerAdvice
# envelopes (e.g. `{"msg": ..., "throwable": ..., "status": "BAD_REQUEST"}`).
_TOP_LEVEL_KEYS = ("message", "detail", "error", "msg")

# RFC 7807 ProblemDetail shape: field/message pairs are embedded inside
# `detail`, e.g. `... [Field error in object 'X' on field 'Y': ...; default message [must not be null]]`.
_PROBLEM_DETAIL = re.compile(
    r"on field '([^']+)':.*?default message \[([^\]]+)\]",
    re.DOTALL,
)


# Spring carriers a user-facing message in top-level scalars and inside the
# entries of common error arrays.
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
    keyword_match = _NUMERIC_KEYWORD.search(message)
    if keyword_match:
        # Normalise `non-negative` / `non negative` to a single key.
        kind = re.sub(r"[-\s]+", "", keyword_match.group("kind").lower())
        return ObservationKind.NUMERIC_BOUND, _NUMERIC_KEYWORD_PAYLOADS[kind]
    pattern_match = _PATTERN.search(message)
    if pattern_match:
        return ObservationKind.PATTERN, PatternPayload(regex=pattern_match.group("regex"))
    for pattern, name in _FORMAT_PATTERNS:
        if pattern.search(message):
            return ObservationKind.FORMAT, FormatPayload(name=name)
    return None


def _emit(operation: APIOperation, field: str, message: str) -> Observation | None:
    classification = _classify(message)
    if classification is None or not field:
        return None
    kind, payload = classification
    # Spring envelopes don't carry a location tag; treat the field as a path
    # parameter when the operation declares one with that name, otherwise body.
    location = ParameterLocation.PATH if field in operation.path_parameters else ParameterLocation.BODY
    return Observation(
        operation_label=operation.label,
        location=location,
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
            if isinstance(text, str) and (
                _MISSING_PARAMETER.search(text)
                or _TYPE_COERCION.search(text)
                or _UNRECOGNIZED_FIELD.search(text)
                or _UNEXPECTED_PARAMETER.search(text)
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
        return tuple(observations)

    @staticmethod
    def _extract_messages(operation: APIOperation, body: dict) -> Iterable[Observation]:
        # `{"messages": ["<field> - <message>", ...]}` — custom @ControllerAdvice.
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
                        bound=float(_JAVA_TYPE_MAX[type_hint.group("type")]),
                        direction=BoundDirection.MAX,
                        exclusive=False,
                    ),
                )
                continue
            match = _MESSAGE_LINE.match(line)
            if match is None:
                continue
            observation = _emit(operation, match.group(1), match.group(2))
            if observation is not None:
                yield observation

    @staticmethod
    def _extract_sub_errors(operation: APIOperation, body: dict) -> Iterable[Observation]:
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
                observations = _emit(operation, field, message)
                if observations is not None:
                    yield observations

    @staticmethod
    def _extract_problem_detail(operation: APIOperation, body: dict) -> Iterable[Observation]:
        # RFC 7807 ProblemDetail — field/message pairs are embedded in `detail`.
        detail = body.get("detail")
        if not isinstance(detail, str):
            return
        for match in _PROBLEM_DETAIL.finditer(detail):
            observations = _emit(operation, match.group(1), match.group(2))
            if observations is not None:
                yield observations

    @staticmethod
    def _extract_errors(operation: APIOperation, body: dict) -> Iterable[Observation]:
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
                observations = _emit(operation, field, message)
                if observations is not None:
                    yield observations

    @staticmethod
    def _extract_field_errors(operation: APIOperation, body: dict) -> Iterable[Observation]:
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
                observations = _emit(operation, field, message)
                if observations is not None:
                    yield observations

    @staticmethod
    def _extract_top_level_message(operation: APIOperation, body: dict) -> Iterable[Observation]:
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
                    operation_label=operation.label,
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
                        operation_label=operation.label,
                        location=location,
                        parameter_path=(coercion_match.group("field"),),
                        kind=ObservationKind.TYPE_MISMATCH,
                        raw_message=message,
                        payload=payload,
                    )

    @staticmethod
    def _extract_unexpected_properties(operation: APIOperation, body: dict) -> Iterable[Observation]:
        # Body-side `Unrecognized field` and query-side `parameter name "X" is not
        # allowed` — both name a property the schema must drop.
        for message in _iter_carrier_strings(body):
            for match in _UNRECOGNIZED_FIELD.finditer(message):
                yield Observation(
                    operation_label=operation.label,
                    location=ParameterLocation.BODY,
                    parameter_path=(match.group("name"),),
                    kind=ObservationKind.UNEXPECTED_PROPERTY,
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
