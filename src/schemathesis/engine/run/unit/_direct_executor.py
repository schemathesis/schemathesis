from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from requests.structures import CaseInsensitiveDict

from schemathesis.checks import CheckContext
from schemathesis.core.errors import AuthenticationError
from schemathesis.core.failures import Failure, FailureGroup
from schemathesis.core.timing import Instant
from schemathesis.engine import Status, events
from schemathesis.engine.errors import TestingState, UnexpectedError, deduplicate_errors
from schemathesis.engine.recorder import ScenarioRecorder
from schemathesis.engine.run import PhaseName
from schemathesis.engine.run.unit._case import record_extra_data_from_recorder, run_one_case
from schemathesis.engine.run.unit._errors import (
    iter_controller_error_events,
    translate_iteration_exception,
)
from schemathesis.generation import overrides
from schemathesis.generation.drivers import CoverageGenerator, ExamplesGenerator
from schemathesis.generation.hypothesis.reporting import ignore_hypothesis_output

if TYPE_CHECKING:
    from schemathesis.engine.context import EngineContext


def run_driver(
    *,
    generator: CoverageGenerator | ExamplesGenerator,
    ctx: EngineContext,
    phase: PhaseName,
    suite_id: uuid.UUID,
    scenario_id: uuid.UUID,
) -> events.EventGenerator:
    """Drive a progressive case generator directly, one case at a time."""
    operation = generator.operation
    errors: list[Exception] = []
    skip_reason: str | None = None
    started_at = Instant()
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
            elapsed_time=started_at.elapsed,
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
    checks_config = ctx.config.checks_config_for(operation=operation, phase=phase.value.lower())
    check_ctx = CheckContext(
        override=override,
        auth=auth,
        headers=CaseInsensitiveDict(headers) if headers else None,
        config=checks_config,
        transport_kwargs=transport_kwargs,
        recorder=recorder,
        response_checks=ctx.checks.for_responses(),
        phase=phase,
    )

    if ctx.error_feedback is not None:
        ctx.error_feedback.checkpoint()

    status = Status.SUCCESS
    any_case_ran = False
    any_case_errored = False
    try:
        # Silence Hypothesis stderr chatter so it doesn't leak into the engine's event stream.
        with ignore_hypothesis_output():
            # Match LIFO order from Hypothesis `Phase.explicit` so engine output matches the pytest path.
            for case in reversed(list(generator)):
                if ctx.has_to_stop:
                    # Promote the stop signal so `KeyboardInterrupt` handler reports INTERRUPTED.
                    raise KeyboardInterrupt
                any_case_ran = True
                try:
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
                except UnexpectedError:
                    # Per-case runtime error — already appended to `errors`. Continue iterating so
                    # subsequent cases still run; their errors are accumulated and surfaced together.
                    any_case_errored = True
                    continue
            if not any_case_ran:
                status = Status.SKIP
                skip_reason = "No examples in schema"
            elif any_case_errored:
                status = Status.ERROR
    except (FailureGroup, Failure):
        status = Status.FAILURE
    except KeyboardInterrupt:
        yield scenario_finished(Status.INTERRUPTED)
        yield events.Interrupted(phase=phase)
        return
    except AuthenticationError as exc:
        status = Status.ERROR
        yield non_fatal_error(exc)
    except Exception as exc:
        status = Status.ERROR
        yield translate_iteration_exception(
            exc,
            operation=operation,
            state=state,
            non_fatal_error=non_fatal_error,
        )

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
