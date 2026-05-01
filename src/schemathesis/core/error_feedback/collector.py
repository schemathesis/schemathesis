from __future__ import annotations

from typing import TYPE_CHECKING

from schemathesis.core.error_feedback.pipeline import get_pipeline
from schemathesis.core.error_feedback.store import ErrorFeedbackStore

if TYPE_CHECKING:
    from schemathesis.core.transport import Response
    from schemathesis.generation.case import Case
    from schemathesis.schemas import APIOperation


def record_response(
    *,
    store: ErrorFeedbackStore,
    operation: APIOperation,
    case: Case,
    response: Response,
) -> None:
    """Route a response through the parser pipeline into the store."""
    status = response.status_code
    if (
        # Only 4xx is actionable: 5xx is already a check finding; 401/403 are auth noise.
        status < 400
        or status >= 500
        or status in (401, 403)
        # Negative-mode is meant to fail — recording it would poison the signal.
        or (case.meta is not None and case.meta.generation.mode.is_negative)
    ):
        return
    pipeline = get_pipeline()
    for observation in pipeline.parse(operation=operation, case=case, response=response):
        store.record(observation)
