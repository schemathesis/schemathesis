from __future__ import annotations

import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, TypeAlias, TypeGuard

import requests
from requests.exceptions import ChunkedEncodingError
from urllib3.exceptions import ReadTimeoutError

from schemathesis.auths import reauth_and_replay
from schemathesis.core.failures import Failure, FailureGroup
from schemathesis.core.rate_limit import (
    RATE_LIMIT_AUTO_MAX_RETRIES,
    RATE_LIMIT_AUTO_REPORT_THRESHOLD,
    parse_retry_after,
)
from schemathesis.core.transport import DEFAULT_RESPONSE_TIMEOUT
from schemathesis.engine import events
from schemathesis.engine._validate import validate_response
from schemathesis.engine.control import BudgetExpired
from schemathesis.engine.errors import is_unrecoverable_network_error

if TYPE_CHECKING:
    from schemathesis.checks import CheckContext
    from schemathesis.core.transport import Response
    from schemathesis.engine.context import EngineContext
    from schemathesis.engine.recorder import ScenarioRecorder
    from schemathesis.generation.case import Case


# Failures `send_tracked` raises when the request got no response.
TransportError: TypeAlias = requests.ConnectionError | ChunkedEncodingError | requests.Timeout
TRANSPORT_ERRORS = (requests.ConnectionError, ChunkedEncodingError, requests.Timeout)


def is_unrecoverable_transport_error(exc: Exception) -> TypeGuard[TransportError]:
    return isinstance(exc, TRANSPORT_ERRORS) and is_unrecoverable_network_error(exc)


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


def _within_time_budget(ctx: EngineContext, transport_kwargs: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    """Shorten the request timeout to the time the run has left; also tells whether it was shortened."""
    remaining = ctx.control.remaining_time
    # A budget spent before the first request still lets it through, under the usual timeout.
    if remaining is None or remaining <= 0:
        return transport_kwargs, False
    timeout = transport_kwargs.get("timeout", DEFAULT_RESPONSE_TIMEOUT)
    if timeout is not None and timeout <= remaining:
        return transport_kwargs, False
    return {**transport_kwargs, "timeout": remaining}, True


def send_tracked(
    ctx: EngineContext,
    case: Case,
    *,
    transport_kwargs: dict[str, Any],
    emit: Callable[[events.EngineEvent], None],
    send: Callable[[dict[str, Any]], Response] | None = None,
    after_response: Callable[[Response], None] | None = None,
) -> tuple[list[Response], Response]:
    """Send a request the outage tracker remembers, waiting out rate limits when configured.

    A response signalling expired auth is replayed once with fresh credentials.
    `send` defaults to `case.call(**kwargs)`; `after_response` runs on every answered attempt,
    including each retried 429. A request still waiting when the time budget runs out raises `BudgetExpired`.
    Returns (rate_limited_responses, final_response).
    """
    call = send or (lambda kwargs: case.call(**kwargs))

    def call_fn() -> Response:
        kwargs, is_shortened = _within_time_budget(ctx, transport_kwargs)
        try:
            response = ctx.server.track(case, lambda: call(kwargs), transport_kwargs=kwargs)
        except (requests.Timeout, requests.ConnectionError) as exc:
            # A timeout while reading the body arrives as a connection error that wraps the read timeout.
            timed_out = isinstance(exc, requests.Timeout) or isinstance(
                exc.args[0] if exc.args else None, ReadTimeoutError
            )
            if is_shortened and timed_out:
                # The clock may still show a few milliseconds left; every later check must agree the run is over.
                ctx.control.spend_time_budget()
                raise BudgetExpired from None
            raise
        if after_response is not None:
            after_response(response)
        return response

    def on_delay(delay: float, retries_left: int) -> None:
        emit(events.RateLimitRetry(operation=case.operation.label, delay=delay, retries_left=retries_left))

    auto_mode = ctx.config.rate_limit_for(operation=case.operation) == "auto"
    rate_limited, response = _call_with_retry(call_fn=call_fn, auto_mode=auto_mode, on_delay=on_delay)

    def replay() -> Response:
        retried, replayed = _call_with_retry(call_fn=call_fn, auto_mode=auto_mode, on_delay=on_delay)
        rate_limited.extend(retried)
        return replayed

    # Replay before validation so checks see the recovered response, not the stale-token one.
    return rate_limited, reauth_and_replay(case, response, ctx.reauth, replay)


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
