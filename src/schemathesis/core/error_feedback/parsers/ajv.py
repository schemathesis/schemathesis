from __future__ import annotations

import re
from collections.abc import Callable
from typing import TYPE_CHECKING, TypeGuard

from schemathesis.core import NOT_SET
from schemathesis.core.error_feedback.parsers import PARSERS
from schemathesis.core.error_feedback.parsers.extractors import (
    ClassificationResult,
    RegexHandler,
    location_for_method,
    numeric_bound,
    required_handler,
    size_bound,
)
from schemathesis.core.error_feedback.store import (
    BoundDirection,
    EnumPayload,
    FormatPayload,
    NumericBoundPayload,
    Observation,
    ObservationKind,
    PatternPayload,
    SizeBoundPayload,
    TypeMismatchPayload,
)
from schemathesis.core.parameters import ParameterLocation
from schemathesis.core.transforms import decode_pointer

if TYPE_CHECKING:
    from schemathesis.generation.case import Case
    from schemathesis.schemas import APIOperation

KeywordHandler = Callable[[dict], tuple[ClassificationResult, ...]]
ParameterPath = tuple[str | int, ...]

_LOCATION_PREFIXES: dict[str, ParameterLocation] = {
    "body": ParameterLocation.BODY,
    "query": ParameterLocation.QUERY,
    "params": ParameterLocation.PATH,
    "headers": ParameterLocation.HEADER,
}

_FASTIFY_VALIDATION_CODE = "FST_ERR_VALIDATION"


def _to_segment(raw: str) -> str | int:
    """Decode an RFC 6901 segment; ASCII-digit segments become ints (array indices)."""
    decoded = decode_pointer(raw)
    return int(decoded) if decoded.isascii() and decoded.isdigit() else decoded


def _split_instance_path(raw: str) -> ParameterPath:
    """`/user/email` → ('user', 'email'); `/tags/0` → ('tags', 0); '' → ()."""
    if not raw:
        return ()
    return tuple(_to_segment(s) for s in raw.lstrip("/").split("/"))


def _split_dotted_path(raw: str) -> ParameterPath:
    """Legacy AJV 6 `dataPath` form: `.email` / `.user.email` / `[0]` / `['weird key']`."""
    if not raw:
        return ()
    segments: list[str | int] = []
    for token in re.findall(r"\.([^.\[]+)|\[([0-9]+)\]|\['([^']+)'\]", raw):
        prop, index, quoted = token
        if index:
            segments.append(int(index))
        elif quoted:
            segments.append(quoted)
        else:
            segments.append(prop)
    return tuple(segments)


def _bound_handler(direction: BoundDirection, exclusive: bool) -> KeywordHandler:
    def handler(error: dict) -> tuple[ClassificationResult, ...]:
        params = error.get("params")
        if not isinstance(params, dict):
            return ()
        limit = params.get("limit")
        if not isinstance(limit, (int, float)) or isinstance(limit, bool):
            return ()
        return (
            (
                ObservationKind.NUMERIC_BOUND,
                NumericBoundPayload(bound=float(limit), direction=direction, exclusive=exclusive),
            ),
        )

    return handler


def _size_handler(direction: BoundDirection) -> KeywordHandler:
    def handler(error: dict) -> tuple[ClassificationResult, ...]:
        params = error.get("params")
        if not isinstance(params, dict):
            return ()
        limit = params.get("limit")
        if not isinstance(limit, int) or isinstance(limit, bool):
            return ()
        if direction is BoundDirection.MIN:
            payload = SizeBoundPayload(min=limit, max=None)
        else:
            payload = SizeBoundPayload(min=None, max=limit)
        return ((ObservationKind.SIZE_BOUND, payload),)

    return handler


def _format_handler(error: dict) -> tuple[ClassificationResult, ...]:
    params = error.get("params")
    if not isinstance(params, dict):
        return ()
    name = params.get("format")
    if not isinstance(name, str):
        return ()
    return ((ObservationKind.FORMAT, FormatPayload(name=name)),)


def _pattern_handler(error: dict) -> tuple[ClassificationResult, ...]:
    params = error.get("params")
    if not isinstance(params, dict):
        return ()
    pattern = params.get("pattern")
    if not isinstance(pattern, str):
        return ()
    return ((ObservationKind.PATTERN, PatternPayload(regex=pattern)),)


def _enum_handler(error: dict) -> tuple[ClassificationResult, ...]:
    params = error.get("params")
    if not isinstance(params, dict):
        return ()
    values = params.get("allowedValues")
    if not isinstance(values, list) or not values or not all(isinstance(v, str) for v in values):
        return ()
    return ((ObservationKind.ENUM, EnumPayload(values=tuple(values))),)


def _type_handler(error: dict) -> tuple[ClassificationResult, ...]:
    params = error.get("params")
    if not isinstance(params, dict):
        return ()
    expected = params.get("type")
    if not isinstance(expected, str):
        return ()
    return ((ObservationKind.TYPE_MISMATCH, TypeMismatchPayload(type_name=expected)),)


_KEYWORD_HANDLERS: dict[str, KeywordHandler] = {
    "format": _format_handler,
    "pattern": _pattern_handler,
    "enum": _enum_handler,
    "type": _type_handler,
    "required": required_handler,
    "minLength": _size_handler(BoundDirection.MIN),
    "maxLength": _size_handler(BoundDirection.MAX),
    "minItems": _size_handler(BoundDirection.MIN),
    "maxItems": _size_handler(BoundDirection.MAX),
    "minimum": _bound_handler(BoundDirection.MIN, exclusive=False),
    "maximum": _bound_handler(BoundDirection.MAX, exclusive=False),
    "exclusiveMinimum": _bound_handler(BoundDirection.MIN, exclusive=True),
    "exclusiveMaximum": _bound_handler(BoundDirection.MAX, exclusive=True),
}


def _array_base_path(error: dict) -> ParameterPath:
    raw = error.get("instancePath")
    return _split_instance_path(raw) if isinstance(raw, str) else _split_dotted_path(error["dataPath"])


def _array_form_path(error: dict, base_path: ParameterPath) -> ParameterPath | None:
    if error.get("keyword") == "required":
        params = error.get("params")
        if not isinstance(params, dict):
            return None
        missing = params.get("missingProperty")
        if not isinstance(missing, str):
            return None
        return (*base_path, missing)
    if not base_path:
        return None
    return base_path


def _is_root_body_type_error(error: dict, location: ParameterLocation, case: Case) -> bool:
    if location is not ParameterLocation.BODY:
        return False
    if case.body is not NOT_SET and case.body is not None:
        return False
    if error.get("keyword") != "type":
        return False
    params = error.get("params")
    return isinstance(params, dict) and isinstance(params.get("type"), str)


def _is_ajv_array_error(error: object) -> TypeGuard[dict]:
    return (
        isinstance(error, dict)
        and isinstance(error.get("keyword"), str)
        and (isinstance(error.get("instancePath"), str) or isinstance(error.get("dataPath"), str))
    )


def _extract_array_errors(body: object) -> list[dict] | None:
    if not isinstance(body, dict):
        return None
    candidate = body.get("errors")
    if not isinstance(candidate, list) or not candidate or not all(_is_ajv_array_error(e) for e in candidate):
        return None
    return candidate


# Fastify form: single-message envelope. Matches `body must <phrase>` (root) or `<location>/<path> must <phrase>`.
_FASTIFY_CLAUSE = re.compile(
    r"(?P<location>body|query|params|headers)(?:/(?P<path>[^ ]+))? must "
    r"(?P<rest>.+?)(?=, (?:body|query|params|headers)(?:/| )|$)"
)
_FASTIFY_REQUIRED = re.compile(r"have required property '(.+?)'")


def _fastify_format(match: re.Match[str]) -> ClassificationResult:
    return ObservationKind.FORMAT, FormatPayload(name=match.group(1))


def _fastify_pattern(match: re.Match[str]) -> ClassificationResult:
    return ObservationKind.PATTERN, PatternPayload(regex=match.group(1))


_FASTIFY_PHRASE_HANDLERS: tuple[tuple[re.Pattern[str], RegexHandler], ...] = (
    (re.compile(r'^match format "(.+?)"$'), _fastify_format),
    (re.compile(r'^match pattern "(.+?)"$'), _fastify_pattern),
    (re.compile(r"^NOT have fewer than (\d+) characters$"), size_bound(direction=BoundDirection.MIN)),
    (re.compile(r"^NOT have more than (\d+) characters$"), size_bound(direction=BoundDirection.MAX)),
    (re.compile(r"^NOT have fewer than (\d+) items$"), size_bound(direction=BoundDirection.MIN)),
    (re.compile(r"^NOT have more than (\d+) items$"), size_bound(direction=BoundDirection.MAX)),
    (re.compile(r"^be >= (-?\d+(?:\.\d+)?)$"), numeric_bound(direction=BoundDirection.MIN, exclusive=False)),
    (re.compile(r"^be <= (-?\d+(?:\.\d+)?)$"), numeric_bound(direction=BoundDirection.MAX, exclusive=False)),
    (re.compile(r"^be > (-?\d+(?:\.\d+)?)$"), numeric_bound(direction=BoundDirection.MIN, exclusive=True)),
    (re.compile(r"^be < (-?\d+(?:\.\d+)?)$"), numeric_bound(direction=BoundDirection.MAX, exclusive=True)),
)


def _classify_fastify_phrase(phrase: str) -> tuple[ClassificationResult, ...]:
    for pattern, handler in _FASTIFY_PHRASE_HANDLERS:
        match = pattern.match(phrase)
        if match is not None:
            return (handler(match),)
    return ()


def _split_fastify_clauses(message: str) -> list[tuple[ParameterLocation, ParameterPath, str]]:
    clauses: list[tuple[ParameterLocation, ParameterPath, str]] = []
    for match in _FASTIFY_CLAUSE.finditer(message):
        location = _LOCATION_PREFIXES[match.group("location")]
        raw_path = match.group("path") or ""
        path = tuple(_to_segment(s) for s in raw_path.split("/")) if raw_path else ()
        clauses.append((location, path, match.group("rest").strip()))
    return clauses


def _is_fastify_envelope(body: object) -> bool:
    if not isinstance(body, dict):
        return False
    if body.get("code") != _FASTIFY_VALIDATION_CODE:
        return False
    return isinstance(body.get("message"), str)


def _fastify_observations(body: dict, operation_label: str) -> tuple[Observation, ...]:
    message = body["message"]
    clauses = _split_fastify_clauses(message)
    if not clauses:
        return ()
    observations: list[Observation] = []
    for location, path, phrase in clauses:
        required_match = _FASTIFY_REQUIRED.match(phrase)
        if required_match is not None:
            full_path = (*path, required_match.group(1))
            observations.append(
                Observation(
                    operation_label=operation_label,
                    location=location,
                    parameter_path=full_path,
                    kind=ObservationKind.MUST_NOT_BE_BLANK,
                    raw_message=message,
                    payload=None,
                )
            )
            continue
        if not path:
            continue
        for kind, payload in _classify_fastify_phrase(phrase):
            observations.append(
                Observation(
                    operation_label=operation_label,
                    location=location,
                    parameter_path=path,
                    kind=kind,
                    raw_message=message,
                    payload=payload,
                )
            )
    return tuple(observations)


def _array_observations(
    errors: list[dict], operation_label: str, location: ParameterLocation, case: Case
) -> tuple[Observation, ...]:
    observations: list[Observation] = []
    for error in errors:
        handler = _KEYWORD_HANDLERS.get(error["keyword"])
        if handler is None:
            continue
        message = error.get("message")
        raw_message = message if isinstance(message, str) else ""
        base_path = _array_base_path(error)
        path = _array_form_path(error, base_path)
        if path is None:
            if not base_path and _is_root_body_type_error(error, location, case):
                observations.append(
                    Observation(
                        operation_label=operation_label,
                        location=location,
                        parameter_path=(),
                        kind=ObservationKind.MUST_NOT_BE_BLANK,
                        raw_message=raw_message,
                        payload=None,
                    )
                )
            continue
        for kind, payload in handler(error):
            observations.append(
                Observation(
                    operation_label=operation_label,
                    location=location,
                    parameter_path=path,
                    kind=kind,
                    raw_message=raw_message,
                    payload=payload,
                )
            )
    return tuple(observations)


@PARSERS.register
class AjvParser:
    """Parser for AJV / Fastify validation envelopes."""

    priority = 12

    def can_parse(self, *, body: object) -> bool:
        if _extract_array_errors(body) is not None:
            return True
        if _is_fastify_envelope(body):
            assert isinstance(body, dict)
            return bool(_split_fastify_clauses(body["message"]))
        return False

    def parse(self, *, operation: APIOperation, body: object, case: Case) -> tuple[Observation, ...]:
        errors = _extract_array_errors(body)
        if errors is not None:
            return _array_observations(errors, operation.label, location_for_method(operation.method), case)
        if _is_fastify_envelope(body):
            assert isinstance(body, dict)
            return _fastify_observations(body, operation.label)
        return ()


__all__ = ["AjvParser"]
