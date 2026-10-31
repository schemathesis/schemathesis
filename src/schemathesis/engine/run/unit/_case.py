from __future__ import annotations

from typing import TYPE_CHECKING, Any, NoReturn

import requests

from schemathesis.core.error_feedback.collector import parse_observations
from schemathesis.core.errors import InvalidSchema, MalformedMediaType
from schemathesis.core.failures import Failure
from schemathesis.engine import StopReason, events
from schemathesis.engine._rate_limit_retry import (
    TRANSPORT_ERRORS,
    is_unrecoverable_transport_error,
    send_tracked,
    validate_case,
)
from schemathesis.engine.control import BudgetExpired
from schemathesis.engine.errors import (
    UnexpectedError,
    UnrecoverableNetworkError,
    build_code_sample,
)
from schemathesis.engine.recorder import ScenarioRecorder
from schemathesis.engine.supervisor import SchedulingDirective
from schemathesis.generation import metrics
from schemathesis.generation.case import Case
from schemathesis.generation.meta import CONTENT_TYPE_PROBES, coverage_scenario
from schemathesis.resources import record_delete_attempt, record_interaction
from schemathesis.wfc.escalation import record_auth_outcome

if TYPE_CHECKING:
    from schemathesis.core.transport import Response
    from schemathesis.engine.context import EngineContext
    from schemathesis.engine.run.unit._scenario import Scenario
    from schemathesis.schemas import APIOperation


def _is_ordinary_request(case: Case) -> bool:
    """True when the response to `case` says something about the operation under test.

    Responses to requests malformed on purpose (an undeclared method, a malformed `Content-Type`) describe how the
    server rejects the request, so response-driven signal must be filtered through this check before being
    attributed to the operation.
    """
    return case.method.lower() == case.operation.method.lower() and coverage_scenario(case) not in CONTENT_TYPE_PROBES


class ServerWentAway(KeyboardInterrupt):
    """Raised once the server stopped responding, so no refusal is reported per operation."""


def raise_if_stopped(stop_reason: StopReason) -> None:
    """Raise the interrupt that matches why the run stops, if it does."""
    if stop_reason is StopReason.MAX_TIME:
        raise BudgetExpired
    if stop_reason is StopReason.SERVER_UNAVAILABLE:
        raise ServerWentAway
    if stop_reason in (StopReason.INTERRUPTED, StopReason.FAILURE_LIMIT):
        raise KeyboardInterrupt


def run_one_case(
    *,
    case: Case,
    ctx: EngineContext,
    scenario: Scenario,
    errors: list[Exception],
    pending_events: list[events.EngineEvent],
) -> None:
    """Run one case end-to-end: call, record, validate, classify."""
    try:
        # A slice may be shorter than a single case takes. Let the first one through anyway, so an
        # operation is never selected and then left with nothing to show for it.
        if ctx.is_operation_slice_expired and scenario.recorder.interactions:
            raise BudgetExpired
        # One snapshot: reading the clock twice lets the deadline pass in between and turn a spent
        # budget into a phantom interrupt.
        raise_if_stopped(ctx.stop_reason)
        # Honor a supervisor SKIP verdict that flipped mid-scenario; without this,
        # cases already drawn or queued would still hit the server.
        if _is_skipped_by_supervisor(case, ctx):
            return
        if scenario.generation.unique_inputs:
            _call_and_validate_once(case=case, ctx=ctx, scenario=scenario, pending_events=pending_events)
        else:
            _do_call_and_validate(
                case=case,
                ctx=ctx,
                scenario=scenario,
                pending_events=pending_events,
            )
    except (KeyboardInterrupt, Failure):
        raise
    except Exception as exc:
        _raise_case_error(exc, case=case, ctx=ctx, scenario=scenario, errors=errors)


def _is_skipped_by_supervisor(case: Case, ctx: EngineContext) -> bool:
    return (
        _is_ordinary_request(case)
        and ctx.supervisor.verdict(case.operation.label).directive is SchedulingDirective.SKIP
    )


def _call_and_validate_once(
    *, case: Case, ctx: EngineContext, scenario: Scenario, pending_events: list[events.EngineEvent]
) -> None:
    """Send an input only the first time it is seen; later copies repeat the first outcome."""
    # Hooks may change the case before it is sent; it is identified by what was generated.
    key = hash(case)
    cached = ctx.get_cached_outcome(key)
    if isinstance(cached, BaseException):
        raise cached
    if cached is None:
        return
    try:
        _do_call_and_validate(case=case, ctx=ctx, scenario=scenario, pending_events=pending_events)
    except BaseException as exc:
        ctx.cache_outcome(key, exc)
        raise
    else:
        ctx.cache_outcome(key, None)


def _raise_case_error(
    exc: Exception,
    *,
    case: Case,
    ctx: EngineContext,
    scenario: Scenario,
    errors: list[Exception],
) -> NoReturn:
    """Classify an error the case raised: a server outage, an unrecoverable network error or a per-case error."""
    if isinstance(exc, MalformedMediaType) and case.media_type is not None:
        exc = InvalidSchema.from_malformed_media_type(
            exc, case.media_type, path=case.operation.path, method=case.operation.method
        )
    network_error = _as_unrecoverable_network_error(exc, case, scenario.transport_kwargs)
    outage = isinstance(exc, requests.ConnectionError) and ctx.detect_server_outage(exc)
    if outage and ctx.server.is_after_outage(exc):
        # A check's own request may be the refused one; the case itself was answered then.
        if scenario.recorder.find_response(case_id=case.id) is None:
            scenario.recorder.forget_case(case_id=case.id)
        raise ServerWentAway from None
    if network_error is not None:
        # Server likely has crashed and does not accept any connections at all
        # Don't report these error - only the original crash should be reported
        scenario.state.store_unrecoverable_network_error(network_error)
        if outage:
            raise ServerWentAway from None
        raise
    errors.append(exc)
    raise UnexpectedError from None


def _as_unrecoverable_network_error(
    exc: Exception, case: Case, transport_kwargs: dict[str, Any]
) -> UnrecoverableNetworkError | None:
    if is_unrecoverable_transport_error(exc):
        code_sample = build_code_sample(case, exc.request, transport_kwargs)
        return UnrecoverableNetworkError(error=exc, code_sample=code_sample)
    return None


def _do_call_and_validate(
    *, case: Case, ctx: EngineContext, scenario: Scenario, pending_events: list[events.EngineEvent]
) -> None:
    recorder = scenario.recorder
    transport_kwargs = scenario.transport_kwargs
    recorder.record_case(case=case)
    rate_limited, response = _call_with_reauth(
        case=case, ctx=ctx, recorder=recorder, transport_kwargs=transport_kwargs, pending_events=pending_events
    )
    recorder.record_response(case_id=case.id, response=response)
    _record_response_signals(
        case=case, ctx=ctx, recorder=recorder, response=response, transport_kwargs=transport_kwargs
    )
    metrics.maximize(scenario.generation.maximize, case=case, response=response)
    validate_case(
        ctx,
        recorder,
        case=case,
        check_ctx=scenario.check_ctx,
        rate_limited=rate_limited,
        response=response,
        continue_on_failure=scenario.continue_on_failure,
    )
    response.clear_cache()


def _call_with_reauth(
    *,
    case: Case,
    ctx: EngineContext,
    recorder: ScenarioRecorder,
    transport_kwargs: dict[str, Any],
    pending_events: list[events.EngineEvent],
) -> tuple[list[Response], Response]:
    """Call the server, retrying on rate limits and replaying once with fresh credentials when auth expired.

    Returns (rate_limited_responses, final_response).
    """
    try:
        return send_tracked(ctx, case, transport_kwargs=transport_kwargs, emit=pending_events.append)
    except TRANSPORT_ERRORS as error:
        recorder.record_network_error(case_id=case.id, error=error)
        raise
    except BudgetExpired:
        # Cut off by the time limit, the request says nothing about the operation.
        recorder.forget_case(case_id=case.id)
        raise


def _record_response_signals(
    *,
    case: Case,
    ctx: EngineContext,
    recorder: ScenarioRecorder,
    response: Response,
    transport_kwargs: dict[str, Any],
) -> None:
    """Feed the response to error feedback, the supervisor, auth escalation and DELETE decay."""
    if ctx.error_feedback is not None:
        ctx.record_error_feedback(
            case=case,
            response=response,
            recorder=recorder,
            observations=parse_observations(operation=case.operation, case=case, response=response),
            transport_kwargs=transport_kwargs,
        )
    if _is_ordinary_request(case):
        record_auth_outcome(case, response.status_code)
        ctx.supervisor.observe(case, response.status_code, cache_writer=ctx.cache.writer)
    # Record DELETE attempts immediately to influence subsequent strategy draws.
    if ctx.extra_data_source is not None:
        record_delete_attempt(ctx.extra_data_source, case, response.status_code)


def record_extra_data_from_recorder(ctx: EngineContext, operation: APIOperation, recorder: ScenarioRecorder) -> None:
    """Replay the recorder's interactions into the runtime resource pool."""
    phases_config = ctx.config.phases_for(operation=operation)
    fuzzing_config = phases_config.fuzzing
    should_record = (
        (fuzzing_config.enabled and fuzzing_config.extra_data_sources.is_enabled)
        or (phases_config.examples.enabled and ctx.extra_data_source is not None)
        or (phases_config.coverage.enabled and ctx.extra_data_source is not None)
    )
    if not should_record:
        return
    extra_data_source = ctx.extra_data_source
    if extra_data_source is None:
        return
    # Bodies over the recorded payload limit are replayed as a prefix, so values living past it are not reused.
    for case_id, interaction in recorder.interactions.items():
        response = interaction.response
        if response is None:
            continue
        case = recorder.cases[case_id].value
        if _is_ordinary_request(case):
            record_interaction(extra_data_source, case, response)
        response.clear_cache()
