from __future__ import annotations

import unittest
import uuid
from collections.abc import Callable
from typing import TYPE_CHECKING
from warnings import catch_warnings

from requests.structures import CaseInsensitiveDict

from schemathesis.checks import CheckContext
from schemathesis.config._generation import GenerationConfig
from schemathesis.core.compat import BaseExceptionGroup
from schemathesis.core.control import SkipTest
from schemathesis.core.errors import SERIALIZERS_SUGGESTION_MESSAGE
from schemathesis.core.timing import Instant
from schemathesis.engine import Status, events
from schemathesis.engine.context import EngineContext
from schemathesis.engine.errors import TestingState, deduplicate_errors
from schemathesis.engine.recorder import ScenarioRecorder
from schemathesis.engine.run import PhaseName
from schemathesis.engine.run.unit._case import BudgetExpired, record_extra_data_from_recorder
from schemathesis.engine.run.unit._errors import classify_test_exception, iter_mark_error_events
from schemathesis.generation import overrides
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
    error: Exception
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

    phase_name = phase.value
    assert phase_name in ("examples", "coverage", "fuzzing", "stateful")

    operation_config = ctx.config.operations.get_for_operation(operation)
    continue_on_failure = operation_config.continue_on_failure or ctx.config.continue_on_failure or False
    generation = ctx.config.generation_for(operation=operation, phase=phase_name)
    override = overrides.for_operation(ctx.config, operation=operation)
    auth = ctx.config.auth_for(operation=operation)
    headers = ctx.config.headers_for(operation=operation)
    transport_kwargs = ctx.get_transport_kwargs(operation=operation)
    checks_config = ctx.config.checks_config_for(operation=operation, phase=phase_name)
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

    pending_events: list[events.EngineEvent] = []
    try:
        setup_hypothesis_database_key(test_function, operation, generation=generation)
        with catch_warnings(record=True), ignore_hypothesis_output():
            test_function(
                ctx=ctx,
                state=state,
                errors=errors,
                check_ctx=check_ctx,
                recorder=recorder,
                generation=generation,
                transport_kwargs=transport_kwargs,
                continue_on_failure=continue_on_failure,
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
    except KeyboardInterrupt:
        yield scenario_finished(Status.INTERRUPTED)
        yield events.Interrupted(phase=phase)
        return
    except (Exception, BaseExceptionGroup) as exc:
        status, error_events = classify_test_exception(
            exc, operation=operation, state=state, errors=errors, non_fatal_error=non_fatal_error
        )
        yield from error_events

    if status == Status.SUCCESS and any(
        check.status == Status.FAILURE for checks in recorder.checks.values() for check in checks
    ):
        status = Status.FAILURE

    for event in iter_mark_error_events(
        test_function=test_function,
        non_fatal_error=non_fatal_error,
        current_status=status,
        serializers_suggestion=SERIALIZERS_SUGGESTION_MESSAGE,
    ):
        status = Status.ERROR
        yield event

    yield from pending_events

    for error in deduplicate_errors(errors):
        yield non_fatal_error(error)

    record_extra_data_from_recorder(ctx, operation, recorder)

    yield scenario_finished(status)


def setup_hypothesis_database_key(test: Callable, operation: APIOperation, generation: GenerationConfig) -> None:
    """Make Hypothesis use separate database entries for every API operation.

    It increases the effectiveness of the Hypothesis database in the CLI.
    """
    if generation.database is not None and generation.database.lower() == "none":
        test._hypothesis_internal_database_key = None  # type: ignore[attr-defined]
        return
    test.hypothesis.inner_test._hypothesis_internal_add_digest = operation.label.encode("utf8")  # type: ignore[attr-defined]
