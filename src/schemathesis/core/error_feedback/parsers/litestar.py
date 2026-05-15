from __future__ import annotations

import re
from typing import TYPE_CHECKING, TypedDict, TypeGuard

from schemathesis.core.error_feedback.parsers import PARSERS
from schemathesis.core.error_feedback.parsers.extractors import ClassificationResult, location_for_method
from schemathesis.core.error_feedback.store import (
    BoundDirection,
    NumericBoundPayload,
    Observation,
    ObservationKind,
    ParameterPath,
    PatternPayload,
    SizeBoundPayload,
    TypeMismatchPayload,
)
from schemathesis.core.parameters import ParameterLocation

if TYPE_CHECKING:
    from schemathesis.generation.case import Case
    from schemathesis.schemas import APIOperation

# Wire envelope: `{status_code, detail, extra: [{message, key, source}]}`.
# `source` carries the request location of the offending value.
_SOURCE_LOCATIONS: dict[str, ParameterLocation] = {
    "body": ParameterLocation.BODY,
    "query": ParameterLocation.QUERY,
    "path": ParameterLocation.PATH,
    "header": ParameterLocation.HEADER,
    "cookie": ParameterLocation.COOKIE,
}

# Path notation: `address.street`, `items[0]`, `tags[2].name`.
_PATH_SEGMENT = re.compile(r"\.?([^.\[]+)|\[(\d+)\]")

# Scalar names mapped to JSON Schema types. Unmapped values pass through verbatim
# so user-defined types reach the consumer's format-vs-type dispatch unchanged.
_TYPE_ALIASES: dict[str, str] = {
    "str": "string",
    "int": "integer",
    "float": "number",
    "bool": "boolean",
    "bytes": "string",
    "array": "array",
    "object": "object",
}

_MISSING_REQUIRED = re.compile(r"^Object missing required field `(?P<name>[^`]+)`$")
_LENGTH_REGEX = re.compile(r"^Expected `(?:str|array)` of length (?P<op>>=|<=) (?P<n>\d+)$")
_NUMERIC_REGEX = re.compile(r"^Expected `\w+(?:\s\|\s\w+)*` (?P<op>>=|<=|>|<) (?P<n>-?\d+(?:\.\d+)?)$")
_REGEX_PATTERN = re.compile(r"^Expected `\w+` matching regex '(?P<regex>.+)'$")
_TYPE_MISMATCH = re.compile(r"^Expected `(?P<expected>[^`]+)`, got `(?P<got>[^`]+)`$")


def _split_path(key: str) -> ParameterPath:
    """Convert path notation `address.items[0].name` into a structured tuple."""
    segments: list[str | int] = []
    for prop, index in _PATH_SEGMENT.findall(key):
        segments.append(prop or int(index))
    return tuple(segments)


def _resolve_type(expected: str) -> str | None:
    """Map an expected type name to a JSON Schema type, or `None` for genuine multi-arm unions.

    `int | null` collapses to `int` (the Optional pattern); `int | str` returns `None` so we
    don't over-narrow a property the server actually accepts under multiple types.
    """
    non_null_arms = [arm.strip() for arm in expected.split("|") if arm.strip() != "null"]
    if len(non_null_arms) != 1:
        return None
    primary = non_null_arms[0]
    return _TYPE_ALIASES.get(primary, primary)


def _length_classification(match: re.Match[str]) -> ClassificationResult:
    value = int(match.group("n"))
    payload = (
        SizeBoundPayload(min=value, max=None) if match.group("op") == ">=" else SizeBoundPayload(min=None, max=value)
    )
    return ObservationKind.SIZE_BOUND, payload


def _numeric_classification(match: re.Match[str]) -> ClassificationResult:
    op = match.group("op")
    direction = BoundDirection.MIN if op in {">=", ">"} else BoundDirection.MAX
    exclusive = op in {">", "<"}
    return ObservationKind.NUMERIC_BOUND, NumericBoundPayload(
        bound=float(match.group("n")),
        direction=direction,
        exclusive=exclusive,
    )


def _classify(message: str) -> ClassificationResult | None:
    if _MISSING_REQUIRED.match(message):
        return ObservationKind.MUST_NOT_BE_BLANK, None
    length_match = _LENGTH_REGEX.match(message)
    if length_match is not None:
        return _length_classification(length_match)
    numeric_match = _NUMERIC_REGEX.match(message)
    if numeric_match is not None:
        return _numeric_classification(numeric_match)
    regex_match = _REGEX_PATTERN.match(message)
    if regex_match is not None:
        return ObservationKind.PATTERN, PatternPayload(regex=regex_match.group("regex"))
    type_match = _TYPE_MISMATCH.match(message)
    if type_match is not None:
        # `got null` means the value can't be null — nullability is reported via
        # MUST_NOT_BE_BLANK regardless of which type the schema expected.
        if type_match.group("got") == "null":
            return ObservationKind.MUST_NOT_BE_BLANK, None
        type_name = _resolve_type(type_match.group("expected"))
        if type_name is None:
            return None
        return ObservationKind.TYPE_MISMATCH, TypeMismatchPayload(type_name=type_name)
    return None


class LitestarIssue(TypedDict):
    message: str
    key: str
    source: str


def _is_litestar_issue(item: object) -> TypeGuard[LitestarIssue]:
    return (
        isinstance(item, dict)
        and isinstance(item.get("message"), str)
        and isinstance(item.get("key"), str)
        and isinstance(item.get("source"), str)
    )


def _extract_issues(body: object) -> list[LitestarIssue] | None:
    if not isinstance(body, dict):
        return None
    if not isinstance(body.get("status_code"), int):
        return None
    extra = body.get("extra")
    if not isinstance(extra, list) or not extra:
        return None
    if not all(_is_litestar_issue(item) for item in extra):
        return None
    return extra


def _resolve_path(key: str, message: str) -> ParameterPath:
    """Compose the parameter path; required-missing appends the captured field name."""
    base = () if key == "data" else _split_path(key)
    missing_required = _MISSING_REQUIRED.match(message)
    if missing_required is not None:
        return (*base, missing_required.group("name"))
    return base


@PARSERS.register
class LitestarParser:
    """Parser for Litestar 400 validation envelopes."""

    priority = 4

    def can_parse(self, *, body: object) -> bool:
        issues = _extract_issues(body)
        if issues is None:
            return False
        return any(_classify(issue["message"]) is not None for issue in issues)

    def parse(self, *, operation: APIOperation, body: object, case: Case) -> tuple[Observation, ...]:
        issues = _extract_issues(body)
        if issues is None:
            return ()
        method_default = location_for_method(operation.method)
        observations: list[Observation] = []
        for issue in issues:
            message = issue["message"]
            classification = _classify(message)
            if classification is None:
                continue
            kind, payload = classification
            location = _SOURCE_LOCATIONS.get(issue["source"], method_default)
            path = _resolve_path(issue["key"], message)
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


__all__ = ["LitestarParser"]
