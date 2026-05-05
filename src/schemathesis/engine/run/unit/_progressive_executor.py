from __future__ import annotations

import time
import unittest
import uuid
from typing import TYPE_CHECKING, Any
from warnings import catch_warnings

from requests.structures import CaseInsensitiveDict

from schemathesis.checks import CheckContext
from schemathesis.core.control import SkipTest
from schemathesis.core.errors import AuthenticationError
from schemathesis.core.failures import Failure, FailureGroup
from schemathesis.engine import Status, events
from schemathesis.engine.errors import TestingState, UnexpectedError, deduplicate_errors
from schemathesis.engine.recorder import ScenarioRecorder
from schemathesis.engine.run import PhaseName
from schemathesis.engine.run.unit._case import record_extra_data_from_recorder, run_one_case
from schemathesis.engine.run.unit._errors import (
    get_invalid_regular_expression_message,
    iter_controller_error_events,
    translate_iteration_exception,
)
from schemathesis.generation import overrides
from schemathesis.generation.hypothesis.reporting import ignore_hypothesis_output
from schemathesis.generation.progressive import CoverageGenerator

if TYPE_CHECKING:
    from schemathesis.engine.context import EngineContext
    from schemathesis.generation.progressive import CaseGenerator
    from schemathesis.schemas import APIOperation


def build_coverage_generator(
    operation: APIOperation,
    ctx: EngineContext,
    as_strategy_kwargs: dict[str, Any],
) -> CaseGenerator:
    """Construct the Coverage-phase case generator."""
    phases_config = ctx.config.phases_for(operation=operation)
    generation = ctx.config.generation_for(operation=operation)
    return CoverageGenerator(
        operation=operation,
        generation_modes=generation.modes,
        generate_duplicate_query_parameters=phases_config.coverage.generate_duplicate_query_parameters,
        unexpected_methods=phases_config.coverage.unexpected_methods,
        generation_config=generation,
        auth_storage=as_strategy_kwargs.get("auth_storage"),
        as_strategy_kwargs=as_strategy_kwargs,
        unexpected_methods_seen=ctx.schema.coverage_unexpected_methods_seen,
    )


def run_progressive(
    *,
    generator: CaseGenerator,
    ctx: EngineContext,
    phase: PhaseName,
    suite_id: uuid.UUID,
    scenario_id: uuid.UUID,
) -> events.EventGenerator:
    """Drive a progressive `CaseGenerator` directly, one case at a time."""
    operation = generator.operation
    errors: list[Exception] = []
    skip_reason: str | None = None
    test_start_time = time.monotonic()
    recorder = ScenarioRecorder(label=operation.label)
    state = TestingState()

    def non_fatal_error(error: Exception, code_sample: str | None = None) -> events.NonFatalError:
        return events.NonFatalError(
            error=error, phase=phase, label=operation.label, related_to_operation=True, code_sample=code_sample
        )

    def scenario_finished(status: Status) -> events.ScenarioFinished:
        return events.ScenarioFinished(
            id=scenario_id,
            suite_id=suite_id,
            phase=phase,
            label=operation.label,
            recorder=recorder,
            status=status,
            elapsed_time=time.monotonic() - test_start_time,
            skip_reason=skip_reason,
            is_final=False,
        )

    operation_config = ctx.config.operations.get_for_operation(operation)
    continue_on_failure = operation_config.continue_on_failure or ctx.config.continue_on_failure or False
    generation = ctx.config.generation_for(operation=operation, phase=phase.value.lower())
    override = overrides.for_operation(ctx.config, operation=operation)
    auth = ctx.config.auth_for(operation=operation)
    headers = ctx.config.headers_for(operation=operation)
    transport_kwargs = ctx.get_transport_kwargs(operation=operation)
    check_ctx = CheckContext(
        override=override,
        auth=auth,
        headers=CaseInsensitiveDict(headers) if headers else None,
        config=ctx.config.checks_config_for(operation=operation, phase=phase.value.lower()),
        transport_kwargs=transport_kwargs,
        recorder=recorder,
    )

    if ctx.error_feedback is not None:
        ctx.error_feedback.checkpoint()

    status: Status | None = None
    any_case_ran = False
    try:
        # `_iter_coverage_cases` draws from hypothesis-jsonschema strategies internally, which may
        # emit UserWarning about invalid regex patterns; suppress those here and translate them below.
        # `ignore_hypothesis_output` silences Hypothesis's own stderr chatter for the same reason.
        with catch_warnings(record=True) as warnings_list, ignore_hypothesis_output():
            for case in generator:
                if ctx.has_to_stop or generator.controller.is_stopped:
                    break
                any_case_ran = True
                run_one_case(
                    case=case,
                    ctx=ctx,
                    check_ctx=check_ctx,
                    recorder=recorder,
                    generation=generation,
                    transport_kwargs=transport_kwargs,
                    continue_on_failure=continue_on_failure,
                    state=state,
                    errors=errors,
                )
            if any_case_ran:
                status = Status.SUCCESS
            else:
                status = Status.SKIP
                skip_reason = "No coverage cases generated"
    except (SkipTest, unittest.case.SkipTest) as exc:
        status = Status.SKIP
        skip_reason = str(exc)
    except (FailureGroup, Failure):
        status = Status.FAILURE
    except UnexpectedError:
        status = Status.ERROR
    except KeyboardInterrupt:
        yield scenario_finished(Status.INTERRUPTED)
        yield events.Interrupted(phase=phase)
        return
    except AuthenticationError as exc:
        status = Status.ERROR
        yield non_fatal_error(exc)
    except Exception as exc:
        status = Status.ERROR
        invalid_regex = get_invalid_regular_expression_message(warnings_list)
        yield translate_iteration_exception(
            exc,
            operation=operation,
            state=state,
            non_fatal_error=non_fatal_error,
            invalid_regex_message=invalid_regex,
        )

    assert status is not None
    if (
        status == Status.SUCCESS
        and continue_on_failure
        and any(check.status == Status.FAILURE for checks in recorder.checks.values() for check in checks)
    ):
        status = Status.FAILURE

    for event in iter_controller_error_events(
        controller=generator.controller,
        non_fatal_error=non_fatal_error,
    ):
        status = Status.ERROR
        yield event

    for error in deduplicate_errors(errors):
        yield non_fatal_error(error)

    record_extra_data_from_recorder(ctx, operation, recorder)

    yield scenario_finished(status)
