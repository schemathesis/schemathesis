from __future__ import annotations

import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from schemathesis.core.failures import Failure, FailureGroup
from schemathesis.core.rate_limit import (
    RATE_LIMIT_AUTO_MAX_RETRIES,
    RATE_LIMIT_AUTO_REPORT_THRESHOLD,
    parse_retry_after,
)
from schemathesis.engine import events
from schemathesis.engine._validate import validate_response

if TYPE_CHECKING:
    from schemathesis.checks import CheckContext
    from schemathesis.core.transport import Response
    from schemathesis.engine.context import EngineContext
    from schemathesis.engine.recorder import ScenarioRecorder
    from schemathesis.generation.case import Case


def _get_retry_after(response: Response) -> str:
    value = response.headers.get("retry-after") or response.headers.get("Retry-After")
    if isinstance(value, list):
        return value[0] if value else ""
    return value or ""


def _call_with_retry(
    *,
    call_fn: Callable[[], Response],
    auto_mode: bool,
    on_delay: Callable[[float, int], None],
) -> tuple[list[Response], Response]:
    """Call an API, retrying on 429 + Retry-After.

    Returns (rate_limited_responses, final_response).
    rate_limited_responses contains each 429 received before the final response.
    """
    retries_left = RATE_LIMIT_AUTO_MAX_RETRIES
    rate_limited: list[Response] = []

    while True:
        response = call_fn()
        if response.status_code == 429 and auto_mode and retries_left > 0:
            delay = parse_retry_after(_get_retry_after(response))
            if delay is not None:
                rate_limited.append(response)
                retries_left -= 1
                if delay >= RATE_LIMIT_AUTO_REPORT_THRESHOLD:
                    on_delay(delay, retries_left)
                time.sleep(delay)
                continue
        break

    return rate_limited, response


def send_tracked(
    ctx: EngineContext,
    case: Case,
    *,
    transport_kwargs: dict[str, Any],
    emit: Callable[[events.EngineEvent], None],
    send: Callable[[], Response] | None = None,
    after_response: Callable[[Response], None] | None = None,
) -> tuple[list[Response], Response]:
    """Send a request the outage tracker remembers, waiting out rate limits when configured.

    `send` defaults to `case.call(**transport_kwargs)`; `after_response` runs on every answered attempt,
    including each retried 429.
    Returns (rate_limited_responses, final_response).
    """
    call = send or (lambda: case.call(**transport_kwargs))

    def call_fn() -> Response:
        response = ctx.server.track(case, call, transport_kwargs=transport_kwargs)
        if after_response is not None:
            after_response(response)
        return response

    def on_delay(delay: float, retries_left: int) -> None:
        emit(events.RateLimitRetry(operation=case.operation.label, delay=delay, retries_left=retries_left))

    return _call_with_retry(
        call_fn=call_fn,
        auto_mode=ctx.config.rate_limit_for(operation=case.operation) == "auto",
        on_delay=on_delay,
    )


def validate_with_rate_limited(
    rate_limited: list[Response],
    final: Response,
    validate_fn: Callable[[Response], None],
    recorder: ScenarioRecorder,
    case_id: str,
) -> Response:
    """Validate every 429 received before the final response, then the final one.

    Failures from 429s are merged with failures from the final response, and the first failing 429
    is recorded as the case's response so reports show the response the failure came from.
    """
    pending_failures: list[Failure] = []
    failing: Response | None = None
    for response in rate_limited:
        try:
            validate_fn(response)
        except FailureGroup as exc:
            pending_failures.extend(f for f in exc.exceptions if isinstance(f, Failure))
            if failing is None:
                failing = response

    try:
        validate_fn(final)
    except FailureGroup as exc:
        if failing is not None:
            recorder.record_response(case_id=case_id, response=failing)
            final_failures = [f for f in exc.exceptions if isinstance(f, Failure)]
            raise FailureGroup(pending_failures + final_failures) from None
        raise

    if failing is not None:
        recorder.record_response(case_id=case_id, response=failing)
        raise FailureGroup(pending_failures)
    return final


def validate_case(
    ctx: EngineContext,
    recorder: ScenarioRecorder,
    *,
    case: Case,
    check_ctx: CheckContext,
    rate_limited: list[Response],
    response: Response,
    continue_on_failure: bool,
) -> None:
    """Check the case's final response and every 429 it waited out before it."""
    validate_with_rate_limited(
        rate_limited,
        response,
        lambda answer: validate_response(
            case=case,
            ctx=check_ctx,
            response=answer,
            continue_on_failure=continue_on_failure,
            recorder=recorder,
            baseline=ctx.config.load_baseline(),
        ),
        recorder,
        case.id,
    )
