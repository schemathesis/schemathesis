from __future__ import annotations

import re

from schemathesis.core.error_feedback.parsers import PARSERS
from schemathesis.core.error_feedback.store import (
    EnumPayload,
    Observation,
    ObservationKind,
    TypeMismatchPayload,
)
from schemathesis.core.parameters import ParameterLocation

# Jackson `MismatchedInputException` / `InvalidFormatException` text. The verb
# spelling (`Can not` -> `Cannot`) and type wrapping (bare -> backticks) changed
# in jackson-databind 2.10 (Sep 2019). The type capture allows any non-backtick
# character on the modern path so generic types like `java.util.List<E>` come
# through verbatim — the consumer's type-to-format map ignores unknown shapes.
_JACKSON_TYPE_PATTERNS: tuple[re.Pattern[str], ...] = (
    # 2.10+ — String source: `Cannot deserialize value of type \`X\` from String "Y"`.
    re.compile(r'Cannot deserialize value of type `(?P<type>[^`]+)` from String "(?P<value>[^"]*)"'),
    # 2.10+ — non-String source (object / array / boolean): `Cannot deserialize
    # instance of \`X\` out of <token>`. Different verb form ("instance of" vs
    # "value of type") because Jackson distinguishes the input shape internally.
    re.compile(r"Cannot deserialize instance of `(?P<type>[^`]+)` out of \w+\s+token"),
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


def _extract_path(message: str) -> tuple[str | int, ...] | None:
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


def _match_enum_values(message: str) -> tuple[str, ...] | None:
    match = _JACKSON_ENUM.search(message)
    if match is None:
        return None
    values = tuple(value.strip() for value in match.group("values").split(",") if value.strip())
    return values or None


@PARSERS.register
class JacksonParser:
    """Parser for Jackson `InvalidFormatException` messages naming the offending Java type.

    Library-level (not Spring-specific) — Jackson messages surface in any
    framework that uses it as the JSON binder. Field attribution requires a
    `through reference chain: ...` segment; messages without one are skipped.
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
            for s in _carrier_strings(body)
        )

    def parse(
        self,
        *,
        operation_label: str,
        body: object,
    ) -> tuple[Observation, ...]:
        if not isinstance(body, dict):
            return ()
        observations: list[Observation] = []
        for message in _carrier_strings(body):
            path = _extract_path(message)
            if path is None:
                continue
            # A single Jackson enum-deserialization error carries both the
            # offending Java type AND the accepted values; emit both kinds so
            # the consumers (TypeMismatchAdjustment + EnumAdjustment) each get
            # what they need.
            type_match = _match_type(message)
            if type_match is not None:
                observations.append(
                    Observation(
                        operation_label=operation_label,
                        location=ParameterLocation.BODY,
                        parameter_path=path,
                        kind=ObservationKind.TYPE_MISMATCH,
                        raw_message=message,
                        payload=TypeMismatchPayload(java_type=type_match),
                    )
                )
            enum_values = _match_enum_values(message)
            if enum_values is not None:
                observations.append(
                    Observation(
                        operation_label=operation_label,
                        location=ParameterLocation.BODY,
                        parameter_path=path,
                        kind=ObservationKind.ENUM,
                        raw_message=message,
                        payload=EnumPayload(values=enum_values),
                    )
                )
        return tuple(observations)
