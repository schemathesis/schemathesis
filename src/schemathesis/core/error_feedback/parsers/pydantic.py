from __future__ import annotations

import re
from collections.abc import Callable
from decimal import Decimal

from schemathesis.core.error_feedback.parsers import PARSERS
from schemathesis.core.error_feedback.store import (
    BoundDirection,
    EnumPayload,
    FormatPayload,
    NumericBoundPayload,
    Observation,
    ObservationKind,
    ObservationPayload,
    PatternPayload,
    SizeBoundPayload,
)
from schemathesis.core.parameters import ParameterLocation

# HTTP frameworks (FastAPI, Litestar, Starlette) prepend a location segment to
# `loc`. When the prefix is absent or unrecognised, treat the whole `loc` as a
# body-relative path.
_LOC_PREFIX_TO_LOCATION: dict[str, ParameterLocation] = {
    "body": ParameterLocation.BODY,
    "query": ParameterLocation.QUERY,
    "path": ParameterLocation.PATH,
    "header": ParameterLocation.HEADER,
    "cookie": ParameterLocation.COOKIE,
    "form": ParameterLocation.BODY,
}

# `ctx.expected` for `enum`/`literal_error` is a human-readable string like
# "'USER' or 'ADMIN'". Pydantic switches quote style for values containing the
# other quote (e.g. `"O'Brien" or 'Smith'`), so accept both forms.
_EXPECTED_TOKEN = re.compile(r"'([^']+)'|\"([^\"]+)\"")


HandlerResult = tuple[ObservationKind, ObservationPayload] | tuple[None, None]
Handler = Callable[[dict], HandlerResult]


def _split_loc(loc: list) -> tuple[ParameterLocation, tuple[str | int, ...]]:
    if loc and isinstance(loc[0], str) and loc[0] in _LOC_PREFIX_TO_LOCATION:
        return _LOC_PREFIX_TO_LOCATION[loc[0]], tuple(loc[1:])
    return ParameterLocation.BODY, tuple(loc)


def _parse_expected(text: object) -> tuple[str, ...] | None:
    if not isinstance(text, str):
        return None
    values = tuple(match.group(1) or match.group(2) for match in _EXPECTED_TOKEN.finditer(text))
    return values or None


def _missing(_context: dict) -> HandlerResult:
    return ObservationKind.MUST_NOT_BE_BLANK, None


def _string_too_short(context: dict) -> HandlerResult:
    min_length = context.get("min_length")
    if not isinstance(min_length, int):
        return None, None
    return ObservationKind.SIZE_BOUND, SizeBoundPayload(min=min_length, max=None)


def _string_too_long(context: dict) -> HandlerResult:
    max_length = context.get("max_length")
    if not isinstance(max_length, int):
        return None, None
    return ObservationKind.SIZE_BOUND, SizeBoundPayload(min=None, max=max_length)


def _numeric_bound(direction: BoundDirection, exclusive: bool, context_key: str) -> Handler:
    def handler(context: dict) -> HandlerResult:
        bound = context.get(context_key)
        # Decimal-typed fields (money, prices) put a Decimal in `ctx`; coerce to
        # float since downstream JSON Schema bounds are numeric-typed anyway.
        if not isinstance(bound, (int, float, Decimal)) or isinstance(bound, bool):
            return None, None
        return ObservationKind.NUMERIC_BOUND, NumericBoundPayload(
            bound=float(bound), direction=direction, exclusive=exclusive
        )

    return handler


def _string_pattern_mismatch(context: dict) -> HandlerResult:
    pattern = context.get("pattern")
    if not isinstance(pattern, str):
        return None, None
    return ObservationKind.PATTERN, PatternPayload(regex=pattern)


def _enum_handler(context: dict) -> HandlerResult:
    values = _parse_expected(context.get("expected"))
    if values is None:
        return None, None
    return ObservationKind.ENUM, EnumPayload(values=values)


def _format_handler(format_name: str) -> Handler:
    def handler(_context: dict) -> HandlerResult:
        return ObservationKind.FORMAT, FormatPayload(name=format_name)

    return handler


_TYPE_CODE_HANDLERS: dict[str, Handler] = {
    "missing": _missing,
    "string_too_short": _string_too_short,
    "string_too_long": _string_too_long,
    "greater_than": _numeric_bound(BoundDirection.MIN, exclusive=True, context_key="gt"),
    "greater_than_equal": _numeric_bound(BoundDirection.MIN, exclusive=False, context_key="ge"),
    "less_than": _numeric_bound(BoundDirection.MAX, exclusive=True, context_key="lt"),
    "less_than_equal": _numeric_bound(BoundDirection.MAX, exclusive=False, context_key="le"),
    "string_pattern_mismatch": _string_pattern_mismatch,
    "enum": _enum_handler,
    "literal_error": _enum_handler,
    # Date/datetime failures surface under several codes depending on the input
    # shape; map every variant to the same JSON-Schema format.
    "date_parsing": _format_handler("date"),
    "date_from_datetime_parsing": _format_handler("date"),
    "date_from_datetime_inexact": _format_handler("date"),
    "datetime_parsing": _format_handler("date-time"),
    "datetime_from_date_parsing": _format_handler("date-time"),
    "time_parsing": _format_handler("time"),
    "uuid_parsing": _format_handler("uuid"),
    "url_parsing": _format_handler("uri"),
}


@PARSERS.register
class PydanticParser:
    """Parser for Pydantic v2 `ValidationError` envelopes — `{"detail": [{type, loc, ctx}, ...]}`."""

    priority = 7

    def can_parse(self, *, body: object) -> bool:
        if not isinstance(body, dict):
            return False
        detail = body.get("detail")
        if not isinstance(detail, list) or not detail:
            return False
        first = detail[0]
        return isinstance(first, dict) and isinstance(first.get("type"), str) and isinstance(first.get("loc"), list)

    def parse(
        self,
        *,
        operation_label: str,
        body: object,
    ) -> tuple[Observation, ...]:
        if not isinstance(body, dict):
            return ()
        detail = body.get("detail")
        if not isinstance(detail, list):
            return ()
        observations: list[Observation] = []
        for entry in detail:
            if not isinstance(entry, dict):
                continue
            type_code = entry.get("type")
            loc = entry.get("loc")
            if not isinstance(type_code, str) or not isinstance(loc, list):
                continue
            handler = _TYPE_CODE_HANDLERS.get(type_code)
            if handler is None:
                continue
            location, path = _split_loc(loc)
            if not path:
                continue
            raw_context = entry.get("ctx")
            context = raw_context if isinstance(raw_context, dict) else {}
            kind, payload = handler(context)
            if kind is None:
                continue
            raw_message = entry.get("msg")
            observations.append(
                Observation(
                    operation_label=operation_label,
                    location=location,
                    parameter_path=path,
                    kind=kind,
                    raw_message=raw_message if isinstance(raw_message, str) else "",
                    payload=payload,
                )
            )
        return tuple(observations)
