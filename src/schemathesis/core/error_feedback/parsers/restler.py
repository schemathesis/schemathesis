from __future__ import annotations

import re
from typing import TYPE_CHECKING

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
    ParameterPath,
    PatternPayload,
    TypeMismatchPayload,
)

if TYPE_CHECKING:
    from schemathesis.generation.case import Case
    from schemathesis.schemas import APIOperation

# Restler 3-5 prefix the HTTP reason phrase; Restler 6 sends the bare message.
_PREFIX = "Bad Request: "
# Nested fields are rendered as `user[email]` or `tags[0]`.
_NAME = r"(?P<name>\w+(?:\[\w+\])*)"

_REQUIRED_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(rf"^`{_NAME}` is required\.$"),
    # Hand-written body checks common in Restler apps.
    re.compile(rf"^{_NAME} field (?:missing|absent in json at root level)$"),
)
# Restler 6 leaves array-level names unquoted.
_INVALID_VALUE = re.compile(rf"^Invalid value specified for (?P<quote>`?){_NAME}(?P=quote)\. (?P<detail>.+)$")


def _pattern(regex: str) -> ClassificationResult:
    return ObservationKind.PATTERN, PatternPayload(regex=regex)


# Patterns mirror each built-in `{@type ...}` check, narrowed to what every Restler version accepts in ASCII.
_STATIC_DETAILS: tuple[tuple[re.Pattern[str], ClassificationResult], ...] = (
    (re.compile(r"^Expecting only alphabetic characters\.$"), _pattern("^[a-zA-Z]+$")),
    (re.compile(r"^Expecting only alpha numeric characters\.$"), _pattern("^[a-zA-Z0-9]+$")),
    (re.compile(r"^Expecting only numeric characters\.$"), _pattern("^[0-9]+$")),
    (re.compile(r"^Expecting only printable characters\.$"), _pattern("^[ -~]+$")),
    (re.compile(r"^Expecting only hexadecimal digits\.$"), _pattern("^[0-9a-fA-F]+$")),
    # Restler 6 also accepts `#abc`, Restler 3-5 do not.
    (re.compile(r"^Expecting color as hexadecimal digits\.$"), _pattern("^#[0-9a-fA-F]{6}$")),
    (re.compile(r"^Expecting phone number, a numeric value with optional `\+` prefix$"), _pattern(r"^\+?[0-9]+$")),
    # The examples that follow these messages change with the server clock.
    (re.compile(r"^Expecting time in `HH:MM:SS` format"), _pattern("^([01]?[0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9]$")),
    (
        re.compile(r"^Expecting time in 12 hour format"),
        _pattern("^([1-9]|1[0-2]|0[1-9])(:[0-5][0-9])? ?([aApP][mM])?$"),
    ),
    (
        re.compile(r"^Expecting date and time in `YYYY-MM-DD HH:MM:SS` format"),
        _pattern(
            "^(19[0-9]{2}|20[0-9]{2})-(0[1-9]|1[0-2])-(0[1-9]|[12][0-9]|3[01]) "
            "([01][0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9]$"
        ),
    ),
    (re.compile(r"^Expecting unix timestamp"), (ObservationKind.TYPE_MISMATCH, TypeMismatchPayload("integer"))),
    # Either version is accepted; IPv4 is the narrower choice.
    (re.compile(r"^Expecting IP address in IPV6 or IPV4 format$"), (ObservationKind.FORMAT, FormatPayload("ipv4"))),
    (re.compile(r"^Expecting integer value$"), (ObservationKind.TYPE_MISMATCH, TypeMismatchPayload("integer"))),
    (re.compile(r"^Expecting numeric value$"), (ObservationKind.TYPE_MISMATCH, TypeMismatchPayload("number"))),
    (re.compile(r"^Expecting boolean value$"), (ObservationKind.TYPE_MISMATCH, TypeMismatchPayload("boolean"))),
    (re.compile(r"^Expecting alpha numeric value$"), (ObservationKind.TYPE_MISMATCH, TypeMismatchPayload("string"))),
    (re.compile(r"^Expecting items of type `\w+`$"), (ObservationKind.TYPE_MISMATCH, TypeMismatchPayload("array"))),
    (re.compile(r"^Expecting email in `name@example\.com` format$"), (ObservationKind.FORMAT, FormatPayload("email"))),
    (
        re.compile(r"^Expecting a Universally Unique IDentifier \(UUID\) string\.$"),
        (ObservationKind.FORMAT, FormatPayload("uuid")),
    ),
    (re.compile(r"^Expecting url in `http://example\.com` format$"), (ObservationKind.FORMAT, FormatPayload("uri"))),
    # The example date that follows changes every day.
    (re.compile(r"^Expecting date in `YYYY-MM-DD` format"), (ObservationKind.FORMAT, FormatPayload("date"))),
)


def _choice(match: re.Match[str]) -> ClassificationResult:
    return ObservationKind.ENUM, EnumPayload(values=tuple(match.group(1).split(",")))


_DYNAMIC_DETAILS: tuple[tuple[re.Pattern[str], RegexHandler], ...] = (
    (
        re.compile(r"^Minimum required value is (-?\d+(?:\.\d+)?)\.$"),
        numeric_bound(direction=BoundDirection.MIN, exclusive=False),
    ),
    (
        re.compile(r"^Maximum allowed value is (-?\d+(?:\.\d+)?)\.$"),
        numeric_bound(direction=BoundDirection.MAX, exclusive=False),
    ),
    (re.compile(r"^Minimum (\d+) (?:characters?|items?) required\.$"), size_bound(direction=BoundDirection.MIN)),
    (re.compile(r"^Maximum (\d+) (?:characters?|items?) allowed\.$"), size_bound(direction=BoundDirection.MAX)),
    (re.compile(r"^Expected one of \((.+)\)\.$"), _choice),
)


def _path(name: str) -> ParameterPath:
    head, *keys = re.split(r"\]?\[", name.rstrip("]"))
    return (head, *(int(key) if key.isdecimal() else key for key in keys))


def _classify_detail(detail: str) -> ClassificationResult | None:
    for pattern, result in _STATIC_DETAILS:
        if pattern.match(detail):
            return result
    for pattern, handler in _DYNAMIC_DETAILS:
        match = pattern.match(detail)
        if match is not None:
            return handler(match)
    return None


def _classify(body: object) -> tuple[ParameterPath, ClassificationResult] | None:
    if not isinstance(body, dict):
        return None
    error = body.get("error")
    if not isinstance(error, dict) or not isinstance(error.get("code"), int):
        return None
    message = error.get("message")
    if not isinstance(message, str):
        return None
    message = message.removeprefix(_PREFIX)
    for pattern in _REQUIRED_PATTERNS:
        match = pattern.match(message)
        if match is not None:
            return _path(match.group("name")), (ObservationKind.MUST_NOT_BE_BLANK, None)
    match = _INVALID_VALUE.match(message)
    if match is None:
        return None
    classification = _classify_detail(match.group("detail"))
    if classification is None:
        return None
    return _path(match.group("name")), classification


@PARSERS.register
class RestlerParser:
    """Parser for Luracast Restler envelopes — `{"error": {"code": 400, "message": "Bad Request: ..."}}`."""

    priority = 5

    def can_parse(self, *, body: object) -> bool:
        return _classify(body) is not None

    def parse(self, *, operation: APIOperation, body: object, case: Case) -> tuple[Observation, ...]:
        classified = _classify(body)
        if classified is None:
            return ()
        path, (kind, payload) = classified
        assert isinstance(body, dict)
        return (
            Observation(
                operation_label=operation.label,
                location=location_for_method(operation.method),
                parameter_path=path,
                kind=kind,
                raw_message=body["error"]["message"],
                payload=payload,
            ),
        )


__all__ = ["RestlerParser"]
