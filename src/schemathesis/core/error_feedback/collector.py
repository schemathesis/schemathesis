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
    """Parse a client error response into structured observations without storing them.

    Returns `()` for other statuses, auth failures, and negative-mode cases.
    """
    status_class = operation.schema.classify_status(response.status_code)
    if (
        # Only client errors are actionable: server errors are already check findings; auth failures are noise.
        not status_class.is_client_error
        or status_class.is_auth_failure
        # Negative-mode is meant to fail — recording it would poison the signal.
        or (case.meta is not None and case.meta.generation.mode.is_negative)
    ):
        return ()
    return operation.schema.parse_rejection(operation, case, response) or tuple(
        get_pipeline().parse(operation=operation, case=case, response=response)
    )


def record_response(
    *,
    store: ErrorFeedbackStore,
    operation: APIOperation,
    case: Case,
    response: Response,
    cache_writer: CacheWriter | None = None,
) -> None:
    """Route a response through the parser pipeline into the store; buffer one cache entry per response."""
    record_observations(
        store=store,
        operation=operation,
        case=case,
        observations=parse_observations(operation=operation, case=case, response=response),
        cache_writer=cache_writer,
    )


def record_observations(
    *,
    store: ErrorFeedbackStore,
    operation: APIOperation,
    case: Case,
    observations: tuple[Observation, ...],
    cache_writer: CacheWriter | None = None,
) -> None:
    """Store already-parsed observations; buffer one cache entry per response."""
    keys: list[str] = []
    for observation in observations:
        store.record(observation)
        if cache_writer is not None:
            keys.append(observation_fingerprint(observation))
    if cache_writer is not None and keys:
        cache_writer.record(Kind.ERROR_FEEDBACK, operation.label, request_from_case(case), observation_keys=keys)
