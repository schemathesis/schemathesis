from __future__ import annotations

import unittest
import uuid
from collections.abc import Callable
from typing import TYPE_CHECKING
from warnings import catch_warnings

from schemathesis.config._generation import GenerationConfig
from schemathesis.core.compat import BaseExceptionGroup
from schemathesis.core.control import SkipTest
from schemathesis.engine import Status, StopReason, events
from schemathesis.engine._baseline import has_new_failures
from schemathesis.engine.context import EngineContext
from schemathesis.engine.run import PhaseName
from schemathesis.engine.run.unit._case import (
    BudgetExpired,
    ServerWentAway,
)
from schemathesis.engine.run.unit._errors import classify_test_exception, iter_mark_error_events
from schemathesis.engine.run.unit._scenario import iter_closing_events, start_scenario
from schemathesis.generation.hypothesis.reporting import ignore_hypothesis_output

if TYPE_CHECKING:
    from schemathesis.schemas import APIOperation


def run_test(
    *,
    operation: APIOperation,
    test_function: Callable,
    ctx: EngineContext,
    phase: PhaseName,
    suite_id: uuid.UUID,
    scenario_id: uuid.UUID,
) -> events.EventGenerator:
    """A single test run with all error handling needed."""
    errors: list[Exception] = []
    skip_reason = None
    assert phase.value in ("examples", "coverage", "fuzzing", "stateful")
    scenario = start_scenario(operation=operation, ctx=ctx, phase=phase, suite_id=suite_id, scenario_id=scenario_id)
    recorder = scenario.recorder
    state = scenario.state
    generation = scenario.generation
    non_fatal_error = scenario.non_fatal_error

    pending_events: list[events.EngineEvent] = []
    try:
        setup_hypothesis_database_key(test_function, operation, generation=generation)
        with catch_warnings(record=True), ignore_hypothesis_output():
            test_function(
                ctx=ctx,
                state=state,
                errors=errors,
                check_ctx=scenario.check_ctx,
                recorder=recorder,
                generation=generation,
                transport_kwargs=scenario.transport_kwargs,
                continue_on_failure=scenario.continue_on_failure,
                pending_events=pending_events,
            )
        # Test body was not executed at all - Hypothesis did not generate any tests, but there is no error
        status = Status.SUCCESS
    except (SkipTest, unittest.case.SkipTest) as exc:
        status = Status.SKIP
        skip_reason = {"Hypothesis has been told to run no examples for this test.": "No examples in schema"}.get(
            str(exc), str(exc)
        )
    except BudgetExpired:
        # The operation ran out of its share, not the whole run — keep whatever it already produced,
        # errors included: running out of time does not undo them.
        if errors:
            status = Status.ERROR
        elif not recorder.interactions:
            status = Status.SKIP
            skip_reason = "Time limit reached"
        else:
            status = Status.SUCCESS
    except ServerWentAway:
        stored = state.unrecoverable_network_error
        if stored is not None:
            # A crash caught before the replay was refused is still this operation's own finding.
            status = Status.ERROR
            yield non_fatal_error(stored.error, code_sample=stored.code_sample)
        elif errors:
            status = Status.ERROR
        elif not recorder.has_responses():
            status = Status.SKIP
            skip_reason = StopReason.SERVER_UNAVAILABLE.skip_explanation
        else:
            status = Status.SUCCESS
    except KeyboardInterrupt:
        yield scenario.finished(Status.INTERRUPTED, skip_reason)
        yield events.Interrupted(phase=phase)
        return
    except (Exception, BaseExceptionGroup) as exc:
        status, error_events = classify_test_exception(
            exc, operation=operation, state=state, errors=errors, non_fatal_error=non_fatal_error
        )
        yield from error_events

    if status == Status.SUCCESS and has_new_failures(recorder, ctx.config.load_baseline()):
        status = Status.FAILURE

    for event in iter_mark_error_events(
        test_function=test_function,
        non_fatal_error=non_fatal_error,
    ):
        status = Status.ERROR
        yield event

    yield from iter_closing_events(scenario, ctx, pending_events=pending_events, errors=errors)

    yield scenario.finished(status, skip_reason)


def setup_hypothesis_database_key(test: Callable, operation: APIOperation, generation: GenerationConfig) -> None:
    """Make Hypothesis use separate database entries for every API operation.

    It increases the effectiveness of the Hypothesis database in the CLI.
    """
    if generation.database is not None and generation.database.lower() == "none":
        test._hypothesis_internal_database_key = None  # type: ignore[attr-defined]
        return
    test.hypothesis.inner_test._hypothesis_internal_add_digest = operation.label.encode("utf8")  # type: ignore[attr-defined]
