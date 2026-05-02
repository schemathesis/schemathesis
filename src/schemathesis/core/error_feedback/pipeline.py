from __future__ import annotations

import threading
from collections.abc import Iterable
from typing import TYPE_CHECKING

from schemathesis.core.deserialization import (
    DeserializationContext,
    deserialize_response,
    has_deserializer,
)
from schemathesis.core.error_feedback.parsers import PARSERS, ResponseParser
from schemathesis.core.error_feedback.store import Observation

if TYPE_CHECKING:
    from schemathesis.core.transport import Response
    from schemathesis.generation.case import Case
    from schemathesis.schemas import APIOperation


class FeedbackPipeline:
    """Dispatches a response through registered parsers with MRU bias.

    A server almost always emits one error shape; the MRU slot lets us try
    the last winner first instead of re-checking every parser on each call.
    """

    __slots__ = ("_parsers", "_last_match", "_lock")

    @classmethod
    def from_registry(cls) -> FeedbackPipeline:
        return cls(parser_cls() for parser_cls in PARSERS.get_all())

    def __init__(self, parsers: Iterable[ResponseParser]) -> None:
        self._parsers = tuple(sorted(parsers, key=lambda p: -p.priority))
        self._last_match: ResponseParser | None = None
        self._lock = threading.Lock()

    def parse(
        self,
        *,
        operation: APIOperation,
        case: Case,
        response: Response,
    ) -> tuple[Observation, ...]:
        content_types = response.headers.get("content-type") or []
        content_type = content_types[0] if content_types else ""
        if not content_type or not has_deserializer(content_type):
            return ()
        try:
            body = deserialize_response(
                response,
                content_type,
                context=DeserializationContext(operation=operation, case=case),
            )
        except Exception:
            # User-registered deserializer raised; surfaced elsewhere — skip here.
            return ()

        # Same-shape consecutive calls go through the MRU slot only.
        last = self._last_match
        if last is not None and last.can_parse(body=body):
            observations = last.parse(operation=operation, body=body)
            if observations:
                return observations
        # It is possible to have different parsers applied for API gateways, where the actual backends are different
        for parser in self._parsers:
            if parser is last or not parser.can_parse(body=body):
                continue
            observations = parser.parse(operation=operation, body=body)
            if observations:
                with self._lock:
                    self._last_match = parser
                return observations
        return ()


_PIPELINE: FeedbackPipeline | None = None
_PIPELINE_LOCK = threading.Lock()


def get_pipeline() -> FeedbackPipeline:
    global _PIPELINE
    if _PIPELINE is None:
        with _PIPELINE_LOCK:
            if _PIPELINE is None:
                _PIPELINE = FeedbackPipeline.from_registry()
    return _PIPELINE


def _reset_pipeline_for_tests() -> None:
    # MRU state would otherwise leak between tests and break snapshot determinism.
    global _PIPELINE
    with _PIPELINE_LOCK:
        _PIPELINE = None
