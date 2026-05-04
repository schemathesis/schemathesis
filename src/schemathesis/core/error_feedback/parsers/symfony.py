from __future__ import annotations

import re
from typing import TYPE_CHECKING

from schemathesis.core.error_feedback.parsers import PARSERS
from schemathesis.core.error_feedback.parsers.extractors import (
    ClassificationResult,
    DictHandler,
    float_or_none,
    format_handler,
    int_or_none,
    location_for_method,
    required_handler,
)
from schemathesis.core.error_feedback.store import (
    BoundDirection,
    EnumPayload,
    NumericBoundPayload,
    Observation,
    ObservationKind,
    PatternPayload,
    SizeBoundPayload,
    TypeMismatchPayload,
)

if TYPE_CHECKING:
    from schemathesis.generation.case import Case
    from schemathesis.schemas import APIOperation

ParameterPath = tuple[str | int, ...]

# Stable Symfony Validator constraint UUIDs — declared as `public const` on each
# constraint class, covered by Symfony's BC promise. Browse the source at:
# https://github.com/symfony/symfony/tree/7.1/src/Symfony/Component/Validator/Constraints
_NOT_BLANK = "c1051bb4-d103-4f74-8988-acbcafc7fdc3"  # NotBlank::IS_BLANK_ERROR
_NOT_NULL = "ad32d13f-c3d4-423b-909a-857b961eb720"  # NotNull::IS_NULL_ERROR
_EMAIL = "bd79c0ab-ddba-46cc-a703-a7a4b08de310"  # Email::INVALID_FORMAT_ERROR
_URL = "57c2f299-1154-4870-89bb-ef3b1f5ad229"  # Url::INVALID_URL_ERROR
_UUID = "51120b12-a2bc-41bf-aa53-cd73daf330d0"  # Uuid::INVALID_CHARACTERS_ERROR
_DATE = "69819696-02ac-4a99-9ff0-14e127c4d1bc"  # Date::INVALID_FORMAT_ERROR
_DATETIME = "1a9da513-2640-4f84-9b6a-4d99dcddc628"  # DateTime::INVALID_FORMAT_ERROR
_LENGTH_MIN = "9ff3fdc4-b214-49db-8718-39c315e33d45"  # Length::TOO_SHORT_ERROR
_LENGTH_MAX = "d94b19cc-114f-4f44-9cc4-4138e80a87b9"  # Length::TOO_LONG_ERROR
_GTE = "ea4e51d1-3342-48bd-87f1-9e672cd90cad"  # GreaterThanOrEqual::TOO_LOW_ERROR
_LTE = "30fbb013-d015-4232-8b3b-8f3be97a7e14"  # LessThanOrEqual::TOO_HIGH_ERROR
_GT = "778b7ae0-84d3-481a-9dec-35fdb64b1d78"  # GreaterThan::TOO_LOW_ERROR
_LT = "079d7420-2d13-460c-8756-de810eeb37d2"  # LessThan::TOO_HIGH_ERROR
_RANGE = "04b91c99-a946-4221-afc5-e65ebac401eb"  # Range::NOT_IN_RANGE_ERROR
_CHOICE = "8e179f1b-97aa-4560-a02f-2a8b42e49df7"  # Choice::NO_SUCH_CHOICE_ERROR
_REGEX = "de1e3db3-5ed4-4941-aae4-59f3667cc3a3"  # Regex::REGEX_FAILED_ERROR
_TYPE = "ba785a8c-82cb-4283-967c-3cf342181b40"  # Type::INVALID_TYPE_ERROR
_COUNT_MIN = "bef8e338-6ae5-4caf-b8e2-50e7b0579e69"  # Count::TOO_FEW_ERROR
_COUNT_MAX = "756b1212-697c-468d-a9ad-50dd783bb169"  # Count::TOO_MANY_ERROR

_URN_UUID_PREFIX = "urn:uuid:"

_PROPERTY_SEGMENT = re.compile(r"([^.\[]+)|\[(\d+)\]")
_PERL_REGEX = re.compile(r"^/(.+)/[a-zA-Z]*$")


def _split_property_path(path: str) -> ParameterPath:
    """`email` → ('email',); `user.email` → ('user','email'); `tags[0]` → ('tags', 0)."""
    if not path:
        return ()
    segments: list[str | int] = []
    for prop, index in _PROPERTY_SEGMENT.findall(path):
        if prop:
            segments.append(prop)
        else:
            segments.append(int(index))
    return tuple(segments)


def _extract_code(violation: dict) -> str | None:
    code = violation.get("code")
    if isinstance(code, str) and code:
        return code
    type_value = violation.get("type")
    if isinstance(type_value, str) and type_value.startswith(_URN_UUID_PREFIX):
        return type_value[len(_URN_UUID_PREFIX) :]
    return None


def _params(violation: dict) -> dict:
    raw = violation.get("parameters")
    return raw if isinstance(raw, dict) else {}


def _size_handler(direction: BoundDirection) -> DictHandler:
    def handler(violation: dict) -> tuple[ClassificationResult, ...]:
        limit = int_or_none(_params(violation).get("{{ limit }}"))
        if limit is None:
            return ()
        if direction is BoundDirection.MIN:
            payload = SizeBoundPayload(min=limit, max=None)
        else:
            payload = SizeBoundPayload(min=None, max=limit)
        return ((ObservationKind.SIZE_BOUND, payload),)

    return handler


def _bound_handler(direction: BoundDirection, exclusive: bool) -> DictHandler:
    def handler(violation: dict) -> tuple[ClassificationResult, ...]:
        bound = float_or_none(_params(violation).get("{{ compared_value }}"))
        if bound is None:
            return ()
        return (
            (
                ObservationKind.NUMERIC_BOUND,
                NumericBoundPayload(bound=bound, direction=direction, exclusive=exclusive),
            ),
        )

    return handler


def _range_handler(violation: dict) -> tuple[ClassificationResult, ...]:
    params = _params(violation)
    low = float_or_none(params.get("{{ min }}"))
    high = float_or_none(params.get("{{ max }}"))
    if low is None or high is None:
        return ()
    return (
        (
            ObservationKind.NUMERIC_BOUND,
            NumericBoundPayload(bound=low, direction=BoundDirection.MIN, exclusive=False),
        ),
        (
            ObservationKind.NUMERIC_BOUND,
            NumericBoundPayload(bound=high, direction=BoundDirection.MAX, exclusive=False),
        ),
    )


def _regex_handler(violation: dict) -> tuple[ClassificationResult, ...]:
    pattern = _params(violation).get("{{ pattern }}")
    if not isinstance(pattern, str) or not pattern:
        return ()
    # Symfony emits Perl-style `/regex/flags` — strip delimiters to expose the bare regex.
    match = _PERL_REGEX.match(pattern)
    cleaned = match.group(1) if match is not None else pattern
    return ((ObservationKind.PATTERN, PatternPayload(regex=cleaned)),)


def _choice_handler(violation: dict) -> tuple[ClassificationResult, ...]:
    raw = _params(violation).get("{{ choices }}")
    if not isinstance(raw, str) or not raw:
        return ()
    # `"\"admin\", \"user\", \"guest\""` → ("admin", "user", "guest").
    values = tuple(_strip_quotes(token.strip()) for token in raw.split(","))
    return ((ObservationKind.ENUM, EnumPayload(values=values)),)


def _strip_quotes(token: str) -> str:
    if len(token) >= 2 and token[0] == '"' and token[-1] == '"':
        return token[1:-1]
    return token


def _type_handler(violation: dict) -> tuple[ClassificationResult, ...]:
    type_name = _params(violation).get("{{ type }}")
    if not isinstance(type_name, str) or not type_name:
        return ()
    return ((ObservationKind.TYPE_MISMATCH, TypeMismatchPayload(type_name=type_name)),)


_CODE_HANDLERS: dict[str, DictHandler] = {
    _NOT_BLANK: required_handler,
    _NOT_NULL: required_handler,
    _EMAIL: format_handler("email"),
    _URL: format_handler("uri"),
    _UUID: format_handler("uuid"),
    _DATE: format_handler("date"),
    _DATETIME: format_handler("date-time"),
    _LENGTH_MIN: _size_handler(BoundDirection.MIN),
    _LENGTH_MAX: _size_handler(BoundDirection.MAX),
    _COUNT_MIN: _size_handler(BoundDirection.MIN),
    _COUNT_MAX: _size_handler(BoundDirection.MAX),
    _GTE: _bound_handler(BoundDirection.MIN, exclusive=False),
    _LTE: _bound_handler(BoundDirection.MAX, exclusive=False),
    _GT: _bound_handler(BoundDirection.MIN, exclusive=True),
    _LT: _bound_handler(BoundDirection.MAX, exclusive=True),
    _RANGE: _range_handler,
    _CHOICE: _choice_handler,
    _REGEX: _regex_handler,
    _TYPE: _type_handler,
}


def _is_violation(item: object) -> bool:
    return isinstance(item, dict) and isinstance(item.get("propertyPath"), str)


def _extract_violations(body: object) -> list[dict] | None:
    """Locate the violations list — top-level list (JSON-default) or under the `violations` key (API-Platform)."""
    if isinstance(body, list):
        if body and all(_is_violation(item) for item in body):
            return body
        return None
    if isinstance(body, dict):
        violations = body.get("violations")
        if isinstance(violations, list) and violations and all(_is_violation(v) for v in violations):
            return violations
    return None


def _has_symfony_signature(violations: list[dict]) -> bool:
    return any(_extract_code(v) is not None for v in violations)


@PARSERS.register
class SymfonyParser:
    """Parser for Symfony Validator envelopes — JSON-default and API-Platform / RFC 7807 forms."""

    priority = 5

    def can_parse(self, *, body: object) -> bool:
        violations = _extract_violations(body)
        return violations is not None and _has_symfony_signature(violations)

    def parse(self, *, operation: APIOperation, body: object, case: Case) -> tuple[Observation, ...]:
        violations = _extract_violations(body)
        if violations is None or not _has_symfony_signature(violations):
            return ()
        location = location_for_method(operation.method)
        observations: list[Observation] = []
        for violation in violations:
            path = _split_property_path(violation["propertyPath"])
            if not path:
                continue
            code = _extract_code(violation)
            if code is None:
                continue
            handler = _CODE_HANDLERS.get(code)
            if handler is None:
                continue
            message = violation.get("message") or violation.get("title") or ""
            raw_message = message if isinstance(message, str) else ""
            for kind, payload in handler(violation):
                observations.append(
                    Observation(
                        operation_label=operation.label,
                        location=location,
                        parameter_path=path,
                        kind=kind,
                        raw_message=raw_message,
                        payload=payload,
                    )
                )
        return tuple(observations)


__all__ = ["SymfonyParser"]
