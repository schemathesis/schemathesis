from __future__ import annotations

import re
from typing import TYPE_CHECKING, TypeGuard

from schemathesis.core.error_feedback.parsers import PARSERS
from schemathesis.core.error_feedback.parsers.extractors import (
    ClassificationResult,
    RegexHandler,
    location_for_method,
    numeric_bound,
    size_bound,
)
from schemathesis.core.error_feedback.store import (
    BoundDirection,
    EnumPayload,
    FormatPayload,
    Observation,
    ObservationKind,
    PatternPayload,
    TypeMismatchPayload,
)

if TYPE_CHECKING:
    from schemathesis.generation.case import Case
    from schemathesis.schemas import APIOperation

# Flask-RESTX wraps every validation envelope under this fixed top-level message.
_RESTX_TOP_MESSAGE = "Input payload validation failed"

# `reqparse` family — Flask-RESTful's argument validator and Flask-RESTX's
# `api.parser()`. Single-string-per-field with a small set of pinned messages.
_REQPARSE_MISSING = re.compile(r"^Missing required parameter\b")
_REQPARSE_PATTERN = re.compile(r'^Value does not match pattern: "(?P<regex>[^"]+)"$')
_REQPARSE_INT_COERCION = re.compile(r"^invalid literal for int\(\) with base 10:")
_REQPARSE_FLOAT_COERCION = re.compile(r"^could not convert string to float:")

# `jsonschema` / `jsonschema_rs` family — used by Flask-RESTX's
# `@api.expect(model, validate=True)` and any other Python validator built on
# either implementation. Both quote styles (`'X'` and `"X"`) are accepted to
# cover the rs port's deliberate divergence from Python's single-quote default.
_QUOTE = r"['\"]"
_JSONSCHEMA_REQUIRED = re.compile(rf"^{_QUOTE}(?P<key>[^'\"]+){_QUOTE} is a required property$")
_JSONSCHEMA_TYPE = re.compile(rf"^.+ is not of type {_QUOTE}(?P<type>[^'\"]+){_QUOTE}$")
_JSONSCHEMA_PATTERN = re.compile(rf"^{_QUOTE}[^'\"]*{_QUOTE} does not match {_QUOTE}(?P<regex>[^'\"]+){_QUOTE}$")
_JSONSCHEMA_FORMAT = re.compile(rf"^{_QUOTE}[^'\"]*{_QUOTE} is not a {_QUOTE}(?P<format>[^'\"]+){_QUOTE}$")
# Bracketed enum form (`['a', 'b']`) — always carries the full choice list.
_JSONSCHEMA_ENUM_PY = re.compile(rf"^{_QUOTE}[^'\"]*{_QUOTE} is not one of \[(?P<choices>.+)\]$")
# Prose enum form (`"a", "b" or "c"`) — full list only when 1-3 choices;
# 4+ collapse to `X, Y or N other candidates`, which we deliberately drop.
_JSONSCHEMA_ENUM_RS = re.compile(rf"^{_QUOTE}[^'\"]*{_QUOTE} is not one of (?P<choices>{_QUOTE}.+)$")
_RS_ENUM_TRUNCATED = re.compile(r"\bor \d+ other candidates?$")

_NUMBER = r"-?\d+(?:\.\d+)?"
_NUMERIC_HANDLERS: tuple[tuple[re.Pattern[str], RegexHandler], ...] = (
    (
        re.compile(rf"^.+ is less than the minimum of ({_NUMBER})$"),
        numeric_bound(direction=BoundDirection.MIN, exclusive=False),
    ),
    (
        re.compile(rf"^.+ is greater than the maximum of ({_NUMBER})$"),
        numeric_bound(direction=BoundDirection.MAX, exclusive=False),
    ),
    (
        re.compile(rf"^.+ is less than or equal to the minimum of ({_NUMBER})$"),
        numeric_bound(direction=BoundDirection.MIN, exclusive=True),
    ),
    (
        re.compile(rf"^.+ is greater than or equal to the maximum of ({_NUMBER})$"),
        numeric_bound(direction=BoundDirection.MAX, exclusive=True),
    ),
)

# rs-only size messages; py's bare "is too short"/"is too long" carry no threshold.
_SIZE_HANDLERS: tuple[tuple[re.Pattern[str], RegexHandler], ...] = (
    (
        re.compile(rf"^{_QUOTE}[^'\"]*{_QUOTE} is shorter than (\d+) characters?$"),
        size_bound(direction=BoundDirection.MIN),
    ),
    (
        re.compile(rf"^{_QUOTE}[^'\"]*{_QUOTE} is longer than (\d+) characters?$"),
        size_bound(direction=BoundDirection.MAX),
    ),
    (re.compile(r"^.+ has less than (\d+) items?$"), size_bound(direction=BoundDirection.MIN)),
    (re.compile(r"^.+ has more than (\d+) items?$"), size_bound(direction=BoundDirection.MAX)),
    (re.compile(r"^.+ has less than (\d+) (?:property|properties)$"), size_bound(direction=BoundDirection.MIN)),
    (re.compile(r"^.+ has more than (\d+) (?:property|properties)$"), size_bound(direction=BoundDirection.MAX)),
)


def _parse_py_enum_choices(raw: str) -> tuple[str, ...] | None:
    """Parse Python jsonschema's `'a', 'b', 'c'` form (always quoted, comma-separated)."""
    values = []
    for token in raw.split(","):
        stripped = token.strip()
        if len(stripped) >= 2 and stripped[0] == stripped[-1] and stripped[0] in {"'", '"'}:
            values.append(stripped[1:-1])
        else:
            return None
    return tuple(values) if values else None


def _parse_rs_enum_choices(raw: str) -> tuple[str, ...] | None:
    """Parse jsonschema_rs's `"a", "b" or "c"` form; bail out on the 4+ truncation."""
    if _RS_ENUM_TRUNCATED.search(raw) is not None:
        return None
    head, _, tail = raw.rpartition(" or ")
    pieces = [token.strip() for token in head.split(",")] if head else []
    pieces.append(tail.strip())
    values = []
    for piece in pieces:
        if len(piece) >= 2 and piece[0] == piece[-1] == '"':
            values.append(piece[1:-1])
        else:
            return None
    return tuple(values)


def _classify(message: str) -> ClassificationResult | None:
    if _REQPARSE_MISSING.match(message) or _JSONSCHEMA_REQUIRED.match(message):
        return ObservationKind.MUST_NOT_BE_BLANK, None
    pattern_match = _REQPARSE_PATTERN.match(message) or _JSONSCHEMA_PATTERN.match(message)
    if pattern_match is not None:
        return ObservationKind.PATTERN, PatternPayload(regex=pattern_match.group("regex"))
    if _REQPARSE_INT_COERCION.match(message):
        return ObservationKind.TYPE_MISMATCH, TypeMismatchPayload(type_name="integer")
    if _REQPARSE_FLOAT_COERCION.match(message):
        return ObservationKind.TYPE_MISMATCH, TypeMismatchPayload(type_name="number")
    type_match = _JSONSCHEMA_TYPE.match(message)
    if type_match is not None:
        return ObservationKind.TYPE_MISMATCH, TypeMismatchPayload(type_name=type_match.group("type"))
    format_match = _JSONSCHEMA_FORMAT.match(message)
    if format_match is not None:
        return ObservationKind.FORMAT, FormatPayload(name=format_match.group("format"))
    for pattern, handler in _NUMERIC_HANDLERS:
        match = pattern.match(message)
        if match is not None:
            return handler(match)
    for pattern, handler in _SIZE_HANDLERS:
        match = pattern.match(message)
        if match is not None:
            return handler(match)
    enum_py = _JSONSCHEMA_ENUM_PY.match(message)
    if enum_py is not None:
        values = _parse_py_enum_choices(enum_py.group("choices"))
        if values is not None:
            return ObservationKind.ENUM, EnumPayload(values=values)
    enum_rs = _JSONSCHEMA_ENUM_RS.match(message)
    if enum_rs is not None:
        values = _parse_rs_enum_choices(enum_rs.group("choices"))
        if values is not None:
            return ObservationKind.ENUM, EnumPayload(values=values)
    return None


def _is_str_to_str(value: object) -> TypeGuard[dict[str, str]]:
    return (
        isinstance(value, dict)
        and bool(value)
        and all(isinstance(key, str) and isinstance(item, str) for key, item in value.items())
    )


def _extract_issues(body: object) -> dict[str, str] | None:
    """Locate the issues map — Flask-RESTX (`errors`) takes precedence over Flask-RESTful (`message`)."""
    if not isinstance(body, dict):
        return None
    errors = body.get("errors")
    if _is_str_to_str(errors):
        return errors
    message = body.get("message")
    if _is_str_to_str(message):
        return message
    return None


def _has_restx_marker(body: object) -> bool:
    """Flask-RESTX wraps every validation envelope under this fixed top-level message."""
    return isinstance(body, dict) and body.get("message") == _RESTX_TOP_MESSAGE and _is_str_to_str(body.get("errors"))


@PARSERS.register
class FlaskRestParser:
    """Parser for Flask-RESTful and Flask-RESTX 400 validation responses."""

    priority = 4

    def can_parse(self, *, body: object) -> bool:
        if _has_restx_marker(body):
            return True
        issues = _extract_issues(body)
        if issues is None:
            return False
        return any(_classify(message) is not None for message in issues.values())

    def parse(self, *, operation: APIOperation, body: object, case: Case) -> tuple[Observation, ...]:
        issues = _extract_issues(body)
        if issues is None:
            return ()
        location = location_for_method(operation.method)
        observations: list[Observation] = []
        for field, message in issues.items():
            classification = _classify(message)
            if classification is None:
                continue
            kind, payload = classification
            target = field if kind is not ObservationKind.MUST_NOT_BE_BLANK else _required_field(message, field)
            observations.append(
                Observation(
                    operation_label=operation.label,
                    location=location,
                    parameter_path=(target,),
                    kind=kind,
                    raw_message=message,
                    payload=payload,
                )
            )
        return tuple(observations)


def _required_field(message: str, fallback: str) -> str:
    """Jsonschema's `'X' is a required property` may report the missing key under a different field key."""
    match = _JSONSCHEMA_REQUIRED.match(message)
    return match.group("key") if match is not None else fallback


__all__ = ["FlaskRestParser"]
