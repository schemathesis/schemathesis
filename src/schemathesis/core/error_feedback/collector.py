from __future__ import annotations

from typing import TYPE_CHECKING

from schemathesis.core.cache import CacheWriter, Kind, request_from_case
from schemathesis.core.error_feedback.pipeline import get_pipeline
from schemathesis.core.error_feedback.store import ErrorFeedbackStore, Observation, observation_fingerprint

if TYPE_CHECKING:
    from schemathesis.core.transport import Response
    from schemathesis.generation.case import Case
    from schemathesis.schemas import APIOperation


def parse_observations(
    operation: APIOperation,
    case: Case,
    response: Response,
) -> tuple[Observation, ...]:
    """Parse a 4xx response into structured observations without storing them.

    Returns `()` for non-4xx, 401/403, and negative-mode cases.
    """
    status = response.status_code
    if (
        # Only 4xx is actionable: 5xx is already a check finding; 401/403 are auth noise.
        status < 400
        or status >= 500
        or status in (401, 403)
        # Negative-mode is meant to fail — recording it would poison the signal.
        or (case.meta is not None and case.meta.generation.mode.is_negative)
    ):
        return ()
    return tuple(get_pipeline().parse(operation=operation, case=case, response=response))


def record_response(
    *,
    store: ErrorFeedbackStore,
    operation: APIOperation,
    case: Case,
    response: Response,
    cache_writer: CacheWriter | None = None,
) -> None:
    """Route a response through the parser pipeline into the store; buffer one cache entry per response."""
    keys: list[str] = []
    for observation in parse_observations(operation=operation, case=case, response=response):
        store.record(observation)
        if cache_writer is not None:
            keys.append(observation_fingerprint(observation))
    if cache_writer is not None and keys:
        cache_writer.record(Kind.ERROR_FEEDBACK, operation.label, request_from_case(case), observation_keys=keys)
