from __future__ import annotations

import re
from typing import TYPE_CHECKING

from schemathesis.core.error_feedback.field_inference import infer_path_from_request
from schemathesis.core.error_feedback.parsers import PARSERS
from schemathesis.core.error_feedback.store import (
    BoundDirection,
    EnumPayload,
    FormatPayload,
    NumericBoundPayload,
    Observation,
    ObservationKind,
    ObservationPayload,
    ParameterPath,
    TypeMismatchPayload,
)
from schemathesis.core.parameters import ParameterLocation

if TYPE_CHECKING:
    from schemathesis.generation.case import Case
    from schemathesis.schemas import APIOperation

# jackson-databind 2.10+ writes `Cannot` and backtick-quoted types, generics like `java.util.List<E>` included.
# String source, the only form with the rejected value, which locates the field when no reference chain exists.
_JACKSON_STRING_SOURCE = re.compile(
    r'Cannot deserialize value of type `(?P<type>[^`]+)` from String "(?P<value>[^"]*)"'
)
_JACKSON_TYPE_PATTERNS: tuple[re.Pattern[str], ...] = (
    _JACKSON_STRING_SOURCE,
    # 2.10+ non-String source (object, array, boolean) says "instance of" instead of "value of type".
    re.compile(r"Cannot deserialize instance of `(?P<type>[^`]+)` out of \w+\s+token"),
    # 2.10+ Object/Array source; without a source string only the reference chain locates the field.
    re.compile(r"Cannot deserialize value of type `(?P<type>[^`]+)` from (?:Object|Array) value"),
    # Before 2.10 (Spring Boot 2.1 and older): `Can not`, a bare type and no String value.
    re.compile(r"Can not deserialize instance of (?P<type>[\w.$<>]+)"),
)

# `through reference chain: Owner["address"]->Address["street"]`; a step is `["field"]` or an index like `List[0]`.
_REFERENCE_CHAIN = re.compile(r"through reference chain: (?P<chain>[^\n)]+)")
_CHAIN_STEP = re.compile(r'\["(?P<name>[^"]+)"\]|\[(?P<index>\d+)\]')

# Enum failures list the accepted values: "not one of the values accepted for Enum class: [USER, ADMIN]".
_JACKSON_ENUM = re.compile(r"not one of the values accepted for Enum class:\s*\[(?P<values>[^\]]+)\]")

# A JSON number too wide for the Java integer type; the type gives the `minimum` / `maximum`.
_JACKSON_NUMERIC_OVERFLOW = re.compile(
    r"Numeric value \([^)]+\) out of range of (?P<size>int|long|short|byte)\b",
)

# `LocalDateTime` / `LocalDate` parse failure; Spring may append `T00:00:00` to an `Instant`-shaped value first.
_JACKSON_DATE_PARSE = re.compile(r"Text '(?P<value>[^']+)' could not be parsed")
_DATE_PARSE_APPENDED_SUFFIX = re.compile(r"T\d{2}:\d{2}:\d{2}$")

# Inclusive bounds per Java primitive integer type.
_JAVA_INT_BOUNDS: dict[str, tuple[int, int]] = {
    "byte": (-128, 127),
    "short": (-32_768, 32_767),
    "int": (-2_147_483_648, 2_147_483_647),
    "long": (-9_223_372_036_854_775_808, 9_223_372_036_854_775_807),
}

# Keys holding the Jackson message in Spring, Dropwizard, Micronaut and Quarkus envelopes.
_CARRIER_KEYS = ("msg", "message", "error", "detail", "defaultMessage")
# Custom `@ControllerAdvice` handlers may put Jackson errors in the Bean Validation array, `{"errors": [...]}`.
_ARRAY_KEYS = ("errors", "subErrors", "fieldErrors")


def _carrier_strings(body: dict) -> list[str]:
    strings = [value for key in _CARRIER_KEYS if isinstance(value := body.get(key), str)]
    for array_key in _ARRAY_KEYS:
        items = body.get(array_key)
        if isinstance(items, list):
            for item in items:
                if isinstance(item, dict):
                    strings.extend(value for key in _CARRIER_KEYS if isinstance(value := item.get(key), str))
    return strings


def _extract_path(message: str) -> ParameterPath | None:
    chain_match = _REFERENCE_CHAIN.search(message)
    if chain_match is None:
        return None
    steps: list[str | int] = []
    for match in _CHAIN_STEP.finditer(chain_match.group("chain")):
        name = match.group("name")
        if name is not None:
            steps.append(name)
        else:
            steps.append(int(match.group("index")))
    return tuple(steps) if steps else None


def _match_type(message: str) -> str | None:
    for pattern in _JACKSON_TYPE_PATTERNS:
        match = pattern.search(message)
        if match is not None:
            return match.group("type")
    return None


def _resolve_date_parse_slot(case: Case, rejected_value: str) -> tuple[ParameterLocation, ParameterPath] | None:
    slot = infer_path_from_request(case=case, rejected_value=rejected_value)
    if slot is not None:
        return slot
    # Spring appends `T00:00:00` to `LocalDateTime` input and the error echoes it; retry without the suffix.
    stripped = _DATE_PARSE_APPENDED_SUFFIX.sub("", rejected_value)
    if stripped == rejected_value:
        return None
    return infer_path_from_request(case=case, rejected_value=stripped)


def _match_enum_values(message: str) -> tuple[str, ...] | None:
    match = _JACKSON_ENUM.search(message)
    if match is None:
        return None
    values = tuple(value.strip() for value in match.group("values").split(",") if value.strip())
    return values or None


def _date_parse_observation(
    operation_label: str,
    message: str,
    case: Case,
    rejected_value: str,
) -> Observation | None:
    slot = _resolve_date_parse_slot(case, rejected_value)
    if slot is None:
        return None
    location, slot_path = slot
    return Observation(
        operation_label=operation_label,
        location=location,
        parameter_path=slot_path,
        kind=ObservationKind.FORMAT,
        raw_message=message,
        payload=FormatPayload(name="date-time"),
    )


def _append_numeric_constraints(
    message: str,
    constraints: list[tuple[ObservationKind, ObservationPayload]],
) -> None:
    overflow_match = _JACKSON_NUMERIC_OVERFLOW.search(message)
    if overflow_match is not None:
        minimum, maximum = _JAVA_INT_BOUNDS[overflow_match.group("size")]
        for bound, direction in (
            (minimum, BoundDirection.MIN),
            (maximum, BoundDirection.MAX),
        ):
            constraints.append(
                (
                    ObservationKind.NUMERIC_BOUND,
                    NumericBoundPayload(bound=bound, direction=direction, exclusive=False),
                ),
            )


def _constraints(message: str) -> list[tuple[ObservationKind, ObservationPayload]]:
    constraints: list[tuple[ObservationKind, ObservationPayload]] = []
    type_match = _match_type(message)
    if type_match is not None:
        constraints.append((ObservationKind.TYPE_MISMATCH, TypeMismatchPayload(type_name=type_match)))
    enum_values = _match_enum_values(message)
    if enum_values is not None:
        constraints.append((ObservationKind.ENUM, EnumPayload(values=enum_values)))
    _append_numeric_constraints(message, constraints)
    return constraints


def _locate(message: str, case: Case) -> tuple[ParameterLocation, ParameterPath] | None:
    path = _extract_path(message)
    if path is not None:
        return ParameterLocation.BODY, path
    string_match = _JACKSON_STRING_SOURCE.search(message)
    if not string_match or not string_match.group("value"):
        return None
    return infer_path_from_request(case=case, rejected_value=string_match.group("value"))


def _append_constraint_observations(
    operation_label: str,
    message: str,
    case: Case,
    observations: list[Observation],
) -> None:
    constraints = _constraints(message)
    if not constraints:
        return
    slot = _locate(message, case)
    if slot is None:
        return
    location, slot_path = slot
    for kind, payload in constraints:
        observations.append(
            Observation(
                operation_label=operation_label,
                location=location,
                parameter_path=slot_path,
                kind=kind,
                raw_message=message,
                payload=payload,
            )
        )


def _append_observations_for_message(
    operation_label: str,
    message: str,
    case: Case,
    observations: list[Observation],
) -> None:
    date_parse_match = _JACKSON_DATE_PARSE.search(message)
    if date_parse_match is not None:
        observation = _date_parse_observation(operation_label, message, case, date_parse_match.group("value"))
        if observation is not None:
            observations.append(observation)
        return
    _append_constraint_observations(operation_label, message, case, observations)


@PARSERS.register
class JacksonParser:
    """Parser for Jackson messages naming the offending Java type, in any framework that binds JSON with Jackson.

    Locates the field by the `through reference chain: ...` part, else by the rejected value in the request.
    """

    priority = 5

    def can_parse(self, *, body: object) -> bool:
        if not isinstance(body, dict):
            return False
        return any(
            "Cannot deserialize value of type" in s
            or "Cannot deserialize instance of" in s
            or "Can not deserialize instance of" in s
            or "Enum class:" in s
            or "out of range of" in s
            or "could not be parsed" in s
            for s in _carrier_strings(body)
        )

    def parse(
        self,
        *,
        operation: APIOperation,
        body: object,
        case: Case,
    ) -> tuple[Observation, ...]:
        if not isinstance(body, dict):
            return ()
        operation_label = operation.label
        observations: list[Observation] = []
        for message in _carrier_strings(body):
            _append_observations_for_message(operation_label, message, case, observations)
        return tuple(observations)
