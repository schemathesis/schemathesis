from __future__ import annotations

from typing import TYPE_CHECKING, Any

import requests
from requests.exceptions import ChunkedEncodingError

from schemathesis.auths import reauth_and_replay
from schemathesis.checks import CheckContext
from schemathesis.config._generation import GenerationConfig
from schemathesis.core.error_feedback.collector import record_response
from schemathesis.core.errors import InvalidSchema, MalformedMediaType
from schemathesis.core.failures import Failure
from schemathesis.engine import events
from schemathesis.engine._rate_limit_retry import call_with_retry
from schemathesis.engine._validate import validate_response
from schemathesis.engine.errors import (
    TestingState,
    UnexpectedError,
    UnrecoverableNetworkError,
    is_unrecoverable_network_error,
)
from schemathesis.engine.recorder import ScenarioRecorder
from schemathesis.engine.supervisor import SchedulingDirective
from schemathesis.generation import metrics
from schemathesis.generation.case import Case

if TYPE_CHECKING:
    from schemathesis.core.transport import Response
    from schemathesis.engine.context import EngineContext
    from schemathesis.schemas import APIOperation


def _targets_declared_method(case: Case) -> bool:
    """True when `case` exercises the operation's declared HTTP method.

    Method-mutated cases (e.g. coverage's `METHOD` scenario sending POST to a
    GET-only route) yield 2xx/4xx that describe the mutated method's path, not
    the operation under test — response-driven signal must be filtered through
    this check before being attributed to it.
    """
    return case.method.lower() == case.operation.method.lower()


def run_one_case(
    *,
    case: Case,
    ctx: EngineContext,
    check_ctx: CheckContext,
    recorder: ScenarioRecorder,
    generation: GenerationConfig,
    transport_kwargs: dict[str, Any],
    continue_on_failure: bool,
    state: TestingState,
    errors: list[Exception],
    pending_events: list[events.EngineEvent],
) -> None:
    """Run one case end-to-end: call, record, validate, classify."""
    try:
        if ctx.has_to_stop:
            raise KeyboardInterrupt
        # Honor a supervisor SKIP verdict that flipped mid-scenario; without this,
        # cases already drawn or queued would still hit the server.
        if (
            _targets_declared_method(case)
            and ctx.supervisor.verdict(case.operation.label).directive is SchedulingDirective.SKIP
        ):
            return
        if generation.unique_inputs:
            cached = ctx.get_cached_outcome(case)
            if isinstance(cached, BaseException):
                raise cached
            if cached is None:
                return
            try:
                _do_call_and_validate(
                    case=case,
                    ctx=ctx,
                    check_ctx=check_ctx,
                    recorder=recorder,
                    generation=generation,
                    transport_kwargs=transport_kwargs,
                    continue_on_failure=continue_on_failure,
                    pending_events=pending_events,
                )
            except BaseException as exc:
                ctx.cache_outcome(case, exc)
                raise
            else:
                ctx.cache_outcome(case, None)
        else:
            _do_call_and_validate(
                case=case,
                ctx=ctx,
                check_ctx=check_ctx,
                recorder=recorder,
                generation=generation,
                transport_kwargs=transport_kwargs,
                continue_on_failure=continue_on_failure,
                pending_events=pending_events,
            )
    except (KeyboardInterrupt, Failure):
        raise
    except Exception as exc:
        if isinstance(exc, MalformedMediaType) and case.media_type is not None:
            exc = InvalidSchema.from_malformed_media_type(
                exc, case.media_type, path=case.operation.path, method=case.operation.method
            )
        if isinstance(
            exc, requests.ConnectionError | ChunkedEncodingError | requests.Timeout
        ) and is_unrecoverable_network_error(exc):
            # Server likely has crashed and does not accept any connections at all
            # Don't report these error - only the original crash should be reported
            if exc.request is not None:
                headers = dict(exc.request.headers)
            else:
                headers = {**dict(case.headers or {}), **transport_kwargs.get("headers", {})}
            verify = transport_kwargs.get("verify", True)
            code_sample = case.as_curl_command(headers=headers, verify=verify)
            state.store_unrecoverable_network_error(UnrecoverableNetworkError(error=exc, code_sample=code_sample))
            raise
        errors.append(exc)
        raise UnexpectedError from None


def _do_call_and_validate(
    *,
    case: Case,
    ctx: EngineContext,
    check_ctx: CheckContext,
    recorder: ScenarioRecorder,
    generation: GenerationConfig,
    transport_kwargs: dict[str, Any],
    continue_on_failure: bool,
    pending_events: list[events.EngineEvent],
) -> None:
    recorder.record_case(parent_id=None, case=case, transition=None, is_transition_applied=False)
    auto_mode = ctx.config.rate_limit_for(operation=case.operation) == "auto"

    def _call() -> Response:
        return case.call(**transport_kwargs)

    def _on_delay(delay: float, retries_left: int) -> None:
        pending_events.append(
            events.RateLimitRetry(operation=case.operation.label, delay=delay, retries_left=retries_left)
        )

    def _perform_call() -> Response:
        try:
            _, response = call_with_retry(call_fn=_call, auto_mode=auto_mode, on_delay=_on_delay)
        except (requests.Timeout, requests.ConnectionError, ChunkedEncodingError) as error:
            if isinstance(error.request, requests.Request):
                recorder.record_request(case_id=case.id, request=error.request.prepare())
            elif isinstance(error.request, requests.PreparedRequest):
                recorder.record_request(case_id=case.id, request=error.request)
            raise
        return response

    response = _perform_call()
    # Replay through `_perform_call` so it keeps rate-limit and network-error handling.
    response = reauth_and_replay(case, response, ctx.reauth, _perform_call)
    recorder.record_response(case_id=case.id, response=response)
    if ctx.error_feedback is not None:
        record_response(
            store=ctx.error_feedback,
            operation=case.operation,
            case=case,
            response=response,
            cache_writer=ctx.cache.writer,
        )
        case.operation.schema.record_runtime_observations(
            store=ctx.error_feedback,
            recorder=recorder,
            case=case,
            response=response,
            transport_kwargs=transport_kwargs,
            cache_writer=ctx.cache.writer,
        )
    if _targets_declared_method(case):
        is_documented_status = case.operation.responses.find_by_status_code(response.status_code) is not None
        ctx.supervisor.record_response(
            operation_label=case.operation.label,
            status_code=response.status_code,
            is_documented_status=is_documented_status,
            case=case,
            cache_writer=ctx.cache.writer,
        )
    # Record DELETE attempts immediately to influence subsequent strategy draws.
    # Include both successful (2xx) and 404 responses - each attempt increases decay
    # to avoid hammering the same resource repeatedly.
    if ctx.extra_data_source is not None:
        status = response.status_code
        if 200 <= status < 300 or status == 404:
            ctx.extra_data_source.record_successful_delete(operation=case.operation, case=case)
    metrics.maximize(generation.maximize, case=case, response=response)
    validate_response(
        case=case,
        ctx=check_ctx,
        response=response,
        continue_on_failure=continue_on_failure,
        recorder=recorder,
    )
    response.clear_cache()


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
    for case_id, interaction in recorder.interactions.items():
        response = interaction.response
        if response is None:
            continue
        case = recorder.cases[case_id].value
        if extra_data_source.should_record(operation=operation.label) and _targets_declared_method(case):
            extra_data_source.record_response(operation=operation, response=response, case=case)
        if extra_data_source.should_record_request(operation=operation.label) and _targets_declared_method(case):
            extra_data_source.record_request(operation=operation, case=case, status_code=response.status_code)
        response.clear_cache()
