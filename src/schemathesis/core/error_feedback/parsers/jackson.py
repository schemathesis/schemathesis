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

# Jackson `MismatchedInputException` / `InvalidFormatException` text. The verb
# spelling (`Can not` -> `Cannot`) and type wrapping (bare -> backticks) changed
# in jackson-databind 2.10 (Sep 2019). The type capture allows any non-backtick
# character on the modern path so generic types like `java.util.List<E>` come
# through verbatim — the consumer's type-to-format map ignores unknown shapes.
# 2.10+ — String source: `Cannot deserialize value of type `X` from String "Y"`.
# This is the only variant that captures the rejected value — used by the field-inference
# fallback when a reference chain is absent.
_JACKSON_STRING_SOURCE = re.compile(
    r'Cannot deserialize value of type `(?P<type>[^`]+)` from String "(?P<value>[^"]*)"'
)
_JACKSON_TYPE_PATTERNS: tuple[re.Pattern[str], ...] = (
    _JACKSON_STRING_SOURCE,
    # 2.10+ — non-String source (object / array / boolean): `Cannot deserialize
    # instance of \`X\` out of <token>`. Different verb form ("instance of" vs
    # "value of type") because Jackson distinguishes the input shape internally.
    re.compile(r"Cannot deserialize instance of `(?P<type>[^`]+)` out of \w+\s+token"),
    # 2.10+ — Object/Array source on the `value of type` verb form. No quoted
    # source string, so attribution requires the reference chain.
    re.compile(r"Cannot deserialize value of type `(?P<type>[^`]+)` from (?:Object|Array) value"),
    # pre-2.10 (Spring Boot 2.1 and older Jakarta EE deployments) — bare type,
    # different word order. No String value in this form.
    re.compile(r"Can not deserialize instance of (?P<type>[\w.$<>]+)"),
)

# Jackson's reference chain: `through reference chain: Owner["address"]->Address["street"]`.
# Each segment is either `["fieldName"]` (object property) or `[N]` (collection
# index — e.g. `java.util.ArrayList[0]` for a failure inside a list element).
_REFERENCE_CHAIN = re.compile(r"through reference chain: (?P<chain>[^\n)]+)")
_CHAIN_STEP = re.compile(r'\["(?P<name>[^"]+)"\]|\[(?P<index>\d+)\]')

# Jackson lists the enum's accepted values inline when a deserialization fails
# on a non-matching string: "not one of the values accepted for Enum class: [USER, ADMIN]".
_JACKSON_ENUM = re.compile(r"not one of the values accepted for Enum class:\s*\[(?P<values>[^\]]+)\]")

# Jackson `Numeric value (X) out of range of <java-type>` — JsonParser emits
# this when an incoming JSON number doesn't fit the destination Java integer
# width. The width tells us the bounds to inject as `minimum`/`maximum`.
_JACKSON_NUMERIC_OVERFLOW = re.compile(
    r"Numeric value \([^)]+\) out of range of (?P<size>int|long|short|byte)\b",
)

# Jackson `LocalDateTime`/`LocalDate` parse failure: `Text 'X' could not be parsed`.
# When a `LocalDateTime` field receives an `Instant`-shaped value (e.g. trailing `Z`),
# Spring appends `T00:00:00` before parsing and the appended chars leak into the
# error message — strip that suffix before walking the request to find the field.
_JACKSON_DATE_PARSE = re.compile(r"Text '(?P<value>[^']+)' could not be parsed")
_DATE_PARSE_APPENDED_SUFFIX = re.compile(r"T\d{2}:\d{2}:\d{2}$")

# Inclusive bounds per Java primitive integer type.
_JAVA_INT_BOUNDS: dict[str, tuple[int, int]] = {
    "byte": (-128, 127),
    "short": (-32_768, 32_767),
    "int": (-2_147_483_648, 2_147_483_647),
    "long": (-9_223_372_036_854_775_808, 9_223_372_036_854_775_807),
}

# Top-level keys we look for the Jackson message under. Spring wraps Jackson
# errors in its own envelope (`{"detail": "..."}` / `{"message": "..."}`);
# Dropwizard, Micronaut, Quarkus all surface them through similar shapes.
_CARRIER_KEYS = ("msg", "message", "error", "detail", "defaultMessage")
# Custom `@ControllerAdvice` handlers sometimes funnel Jackson parse errors
# through the same array envelope used for Bean-validation results, e.g.
# `{"errors": [{"message": "JSON parse error: Cannot deserialize..."}]}`.
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
    # Spring's `LocalDateTime` coercion appends `T00:00:00` to the user value
    # before parsing; that suffix leaks into the error message, so the wire-form
    # value won't be present in the request as-is. Strip and retry.
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


@PARSERS.register
class JacksonParser:
    """Parser for Jackson `InvalidFormatException` messages naming the offending Java type.

    Library-level (not Spring-specific) — Jackson messages surface in any framework using
    Jackson as the JSON binder. Field attribution prefers the `through reference chain: ...`
    segment when present; when absent, falls back to matching the rejected value against
    the request's parameter slots.
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
            date_parse_match = _JACKSON_DATE_PARSE.search(message)
            if date_parse_match is not None:
                slot = _resolve_date_parse_slot(case, date_parse_match.group("value"))
                if slot is not None:
                    location, slot_path = slot
                    observations.append(
                        Observation(
                            operation_label=operation_label,
                            location=location,
                            parameter_path=slot_path,
                            kind=ObservationKind.FORMAT,
                            raw_message=message,
                            payload=FormatPayload(name="date-time"),
                        )
                    )
                continue
            path = _extract_path(message)
            type_match = _match_type(message)
            enum_values = _match_enum_values(message)
            overflow_match = _JACKSON_NUMERIC_OVERFLOW.search(message)

            constraints: list[tuple[ObservationKind, ObservationPayload]] = []
            if type_match is not None:
                constraints.append((ObservationKind.TYPE_MISMATCH, TypeMismatchPayload(type_name=type_match)))
            if enum_values is not None:
                constraints.append((ObservationKind.ENUM, EnumPayload(values=enum_values)))
            if overflow_match is not None:
                minimum, maximum = _JAVA_INT_BOUNDS[overflow_match.group("size")]
                for bound, direction in (
                    (float(minimum), BoundDirection.MIN),
                    (float(maximum), BoundDirection.MAX),
                ):
                    constraints.append(
                        (
                            ObservationKind.NUMERIC_BOUND,
                            NumericBoundPayload(bound=bound, direction=direction, exclusive=False),
                        ),
                    )

            if not constraints:
                continue

            if path is not None:
                location, slot_path = ParameterLocation.BODY, path
            else:
                string_match = _JACKSON_STRING_SOURCE.search(message)
                if string_match is None or not string_match.group("value"):
                    # Message variant has no captured string source; nothing to walk against.
                    continue
                slot = infer_path_from_request(case=case, rejected_value=string_match.group("value"))
                if slot is None:
                    continue
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
        return tuple(observations)
