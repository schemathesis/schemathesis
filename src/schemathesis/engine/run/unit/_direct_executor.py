from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from schemathesis.core.errors import AuthenticationError
from schemathesis.core.failures import Failure, FailureGroup
from schemathesis.engine import Status, StopReason, events
from schemathesis.engine._baseline import has_new_failures
from schemathesis.engine.errors import UnexpectedError
from schemathesis.engine.run import PhaseName
from schemathesis.engine.run.unit._case import (
    BudgetExpired,
    ServerWentAway,
    run_one_case,
)
from schemathesis.engine.run.unit._errors import (
    iter_controller_error_events,
    translate_iteration_exception,
)
from schemathesis.engine.run.unit._scenario import iter_closing_events, start_scenario
from schemathesis.generation.hypothesis.reporting import ignore_hypothesis_output
from schemathesis.generation.meta import content_type_probes_first

if TYPE_CHECKING:
    from schemathesis.engine.context import EngineContext
    from schemathesis.generation.case import Case
    from schemathesis.generation.drivers import CaseGenerator


def _collect_within_budget(generator: CaseGenerator, ctx: EngineContext) -> list[Case]:
    """Enumerate the generator, stopping once the run has no time left to test what it produces."""
    cases = []
    for case in generator:
        cases.append(case)
        if ctx.has_to_stop or ctx.is_operation_slice_expired:
            break
    return cases


def run_driver(
    *,
    generator: CaseGenerator,
    ctx: EngineContext,
    phase: PhaseName,
    suite_id: uuid.UUID,
    scenario_id: uuid.UUID,
) -> events.EventGenerator:
    """Drive a progressive case generator directly, one case at a time."""
    errors: list[Exception] = []
    skip_reason: str | None = None
    scenario = start_scenario(
        operation=generator.operation, ctx=ctx, phase=phase, suite_id=suite_id, scenario_id=scenario_id
    )
    operation = scenario.operation
    recorder = scenario.recorder
    state = scenario.state
    continue_on_failure = scenario.continue_on_failure
    non_fatal_error = scenario.non_fatal_error

    status = Status.SUCCESS
    any_case_ran = False
    any_case_errored = False
    budget_expired = False
    server_went_away = False
    pending_events: list[events.EngineEvent] = []
    try:
        # Silence Hypothesis stderr chatter so it doesn't leak into the engine's event stream.
        with ignore_hypothesis_output():
            # Match LIFO order from Hypothesis `Phase.explicit` so engine output matches the pytest path.
            for case in reversed(content_type_probes_first(_collect_within_budget(generator, ctx))):
                # One snapshot: reading the clock twice lets the deadline pass in between and turn a
                # spent budget into a phantom interrupt.
                stop_reason = ctx.stop_reason
                if stop_reason is StopReason.MAX_TIME:
                    raise BudgetExpired
                if stop_reason is StopReason.SERVER_UNAVAILABLE:
                    raise ServerWentAway
                if stop_reason in (StopReason.INTERRUPTED, StopReason.FAILURE_LIMIT):
                    raise KeyboardInterrupt
                any_case_ran = True
                try:
                    run_one_case(
                        case=case,
                        ctx=ctx,
                        check_ctx=scenario.check_ctx,
                        recorder=recorder,
                        generation=scenario.generation,
                        transport_kwargs=scenario.transport_kwargs,
                        continue_on_failure=continue_on_failure,
                        state=state,
                        errors=errors,
                        pending_events=pending_events,
                    )
                except UnexpectedError:
                    # Per-case runtime error — already appended to `errors`. Continue iterating so
                    # subsequent cases still run; their errors are accumulated and surfaced together.
                    any_case_errored = True
                    continue
    except (FailureGroup, Failure):
        status = Status.FAILURE
    except BudgetExpired:
        budget_expired = True
    except ServerWentAway:
        server_went_away = True
        stored = state.unrecoverable_network_error
        if stored is not None:
            # A crash caught before the outage was confirmed is still this operation's own finding.
            status = Status.ERROR
            yield non_fatal_error(stored.error, code_sample=stored.code_sample)
    except KeyboardInterrupt:
        yield scenario.finished(Status.INTERRUPTED, skip_reason)
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

    if status == Status.SUCCESS:
        if not any_case_ran:
            status = Status.SKIP
            skip_reason = "Time limit reached" if budget_expired else "No examples in schema"
        elif any_case_errored:
            status = Status.ERROR
        elif server_went_away and not recorder.has_responses():
            status = Status.SKIP
            skip_reason = StopReason.SERVER_UNAVAILABLE.skip_explanation

    if status == Status.SUCCESS and continue_on_failure and has_new_failures(recorder, ctx.config.load_baseline()):
        status = Status.FAILURE

    for event in iter_controller_error_events(
        controller=generator.controller,
        non_fatal_error=non_fatal_error,
    ):
        status = Status.ERROR
        yield event

    yield from iter_closing_events(scenario, ctx, pending_events=pending_events, errors=errors)

    yield scenario.finished(status, skip_reason)
