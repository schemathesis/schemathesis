from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from schemathesis.core.error_feedback.parsers import PARSERS
from schemathesis.core.error_feedback.parsers.extractors import ClassificationResult, location_for_method
from schemathesis.core.error_feedback.store import (
    BoundDirection,
    EnumPayload,
    FormatPayload,
    NumericBoundPayload,
    Observation,
    ObservationKind,
    SizeBoundPayload,
    TypeMismatchPayload,
)
from schemathesis.core.parameters import ParameterLocation

if TYPE_CHECKING:
    from schemathesis.generation.case import Case
    from schemathesis.schemas import APIOperation

CodeHandler = Callable[[dict], tuple[ClassificationResult, ...]]

_STRING_VALIDATION_FORMATS: dict[str, str] = {
    "email": "email",
    "url": "uri",
    "uuid": "uuid",
    "datetime": "date-time",
}


def _is_issues_list(candidate: object) -> bool:
    return isinstance(candidate, list) and bool(candidate) and all(isinstance(item, dict) for item in candidate)


def _extract_issues(body: object) -> list[dict] | None:
    """Locate the issues list across the shapes captured from real servers (hand-rolled, Hono, Express)."""
    if isinstance(body, list):
        if not body or not isinstance(body[0], dict):
            return None
        return _extract_issues(body[0])
    if not isinstance(body, dict):
        return None
    for key in ("issues", "errors"):
        candidate = body.get(key)
        if _is_issues_list(candidate):
            return candidate
    for nested_key in ("error", "errors"):
        nested = body.get(nested_key)
        if isinstance(nested, dict) and _is_issues_list(nested.get("issues")):
            return nested["issues"]
    return None


def _has_zod_signature(issues: list[dict]) -> bool:
    """At least one issue must carry both a string `code` and a list `path` — Zod's structural discriminator."""
    return any(isinstance(issue.get("code"), str) and isinstance(issue.get("path"), list) for issue in issues)


def _extract_path(issue: dict) -> tuple[str | int, ...] | None:
    raw = issue.get("path")
    if not isinstance(raw, list):
        return None
    if any(isinstance(p, bool) or not isinstance(p, (str, int)) for p in raw):
        return None
    return tuple(raw)


def _bound_classifier(value_key: str, direction: BoundDirection) -> CodeHandler:
    """Build a handler for `too_small` (`minimum`/MIN) or `too_big` (`maximum`/MAX)."""

    def handler(issue: dict) -> tuple[ClassificationResult, ...]:
        bound = issue.get(value_key)
        if not isinstance(bound, (int, float)) or isinstance(bound, bool):
            return ()
        type_name = issue.get("type")
        if type_name in ("string", "array"):
            if direction is BoundDirection.MIN:
                size_payload = SizeBoundPayload(min=int(bound), max=None)
            else:
                size_payload = SizeBoundPayload(min=None, max=int(bound))
            return ((ObservationKind.SIZE_BOUND, size_payload),)
        if type_name == "number":
            return (
                (
                    ObservationKind.NUMERIC_BOUND,
                    NumericBoundPayload(
                        bound=float(bound),
                        direction=direction,
                        exclusive=not bool(issue.get("inclusive")),
                    ),
                ),
            )
        return ()

    return handler


def _classify_invalid_type(issue: dict) -> tuple[ClassificationResult, ...]:
    # `received: "undefined"` is Zod's `Required` envelope when a field is missing.
    if issue.get("received") == "undefined":
        return ((ObservationKind.MUST_NOT_BE_BLANK, None),)
    expected = issue.get("expected")
    if not isinstance(expected, str):
        return ()
    return ((ObservationKind.TYPE_MISMATCH, TypeMismatchPayload(type_name=expected)),)


def _classify_invalid_enum_value(issue: dict) -> tuple[ClassificationResult, ...]:
    options = issue.get("options")
    if not isinstance(options, list) or not options or not all(isinstance(o, str) for o in options):
        return ()
    return ((ObservationKind.ENUM, EnumPayload(values=tuple(options))),)


def _classify_invalid_string(issue: dict) -> tuple[ClassificationResult, ...]:
    validation = issue.get("validation")
    if not isinstance(validation, str) or validation not in _STRING_VALIDATION_FORMATS:
        return ()
    return ((ObservationKind.FORMAT, FormatPayload(name=_STRING_VALIDATION_FORMATS[validation])),)


def _classify_invalid_date(issue: dict) -> tuple[ClassificationResult, ...]:
    return ((ObservationKind.FORMAT, FormatPayload(name="date-time")),)


_CODE_HANDLERS: dict[str, CodeHandler] = {
    "invalid_string": _classify_invalid_string,
    "too_small": _bound_classifier("minimum", BoundDirection.MIN),
    "too_big": _bound_classifier("maximum", BoundDirection.MAX),
    "invalid_type": _classify_invalid_type,
    "invalid_enum_value": _classify_invalid_enum_value,
    "invalid_date": _classify_invalid_date,
}


def _classify(issue: dict) -> tuple[ClassificationResult, ...]:
    code = issue.get("code")
    handler = _CODE_HANDLERS.get(code) if isinstance(code, str) else None
    return handler(issue) if handler is not None else ()


@PARSERS.register
class ZodParser:
    """Parser for Zod `ZodError` envelopes — structured `{"errors": [{code, path, ...}]}` (or `issues` key)."""

    priority = 11

    def can_parse(self, *, body: object) -> bool:
        issues = _extract_issues(body)
        return issues is not None and _has_zod_signature(issues)

    def parse(self, *, operation: APIOperation, body: object, case: Case) -> tuple[Observation, ...]:
        issues = _extract_issues(body)
        if issues is None or not _has_zod_signature(issues):
            return ()
        location = location_for_method(operation.method)
        observations: list[Observation] = []
        for issue in issues:
            path = _extract_path(issue)
            if path is None:
                continue
            message = issue.get("message")
            raw_message = message if isinstance(message, str) else ""
            for kind, payload in _classify(issue):
                if not path and (
                    location is not ParameterLocation.BODY or kind is not ObservationKind.MUST_NOT_BE_BLANK
                ):
                    continue
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


__all__ = ["ZodParser"]
