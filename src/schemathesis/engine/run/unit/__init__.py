"""Unit testing by Schemathesis Engine.

This module provides high-level flow for single-, and multi-threaded modes.
"""

from __future__ import annotations

import queue
import uuid
import warnings
from dataclasses import dataclass
from queue import Queue
from typing import TYPE_CHECKING, Any

from hypothesis.errors import InvalidArgument
from jsonschema_rs import ValidationError

from schemathesis.core.errors import (
    AuthenticationError,
    HookExecutionError,
    InvalidRegexPattern,
    InvalidSchema,
    is_regex_validation_error,
)
from schemathesis.core.result import Ok, Result
from schemathesis.core.spec import Scheduler
from schemathesis.engine import Status, events
from schemathesis.engine.recorder import ScenarioRecorder
from schemathesis.engine.run import PhaseName, PhaseSkipReason
from schemathesis.engine.run.unit._direct_executor import run_driver
from schemathesis.engine.run.unit._pool import WORKER_FINISHED, WorkerPool
from schemathesis.engine.supervisor import SchedulingDirective, Verdict
from schemathesis.generation import overrides
from schemathesis.generation.drivers import CaseGenerator, CoverageGenerator, ExamplesGenerator
from schemathesis.generation.feedback import FeedbackSources
from schemathesis.generation.hypothesis.builder import HypothesisTestConfig, HypothesisTestMode
from schemathesis.generation.hypothesis.reporting import ignore_hypothesis_output

if TYPE_CHECKING:
    from schemathesis.config import PhasesConfig, SchemathesisWarning
    from schemathesis.engine.context import EngineContext
    from schemathesis.engine.run import Phase
    from schemathesis.schemas import APIOperation

WORKER_TIMEOUT = 0.1


def _build_coverage_generator(
    operation: APIOperation, ctx: EngineContext, as_strategy_kwargs: dict[str, Any], feedback: FeedbackSources
) -> CoverageGenerator:
    generation = ctx.config.generation_for(operation=operation)
    return CoverageGenerator(
        operation=operation,
        generation_modes=generation.modes,
        generation_config=generation,
        auth_storage=as_strategy_kwargs.get("auth_storage"),
        as_strategy_kwargs=as_strategy_kwargs,
        feedback=feedback,
        session=ctx.coverage_session,
        unexpected_methods_seen=ctx.coverage_unexpected_methods_seen,
    )


def _build_examples_generator(
    operation: APIOperation, ctx: EngineContext, as_strategy_kwargs: dict[str, Any], feedback: FeedbackSources
) -> ExamplesGenerator:
    phases_config = ctx.config.phases_for(operation=operation)
    return ExamplesGenerator(
        operation=operation,
        as_strategy_kwargs=as_strategy_kwargs,
        feedback=feedback,
        fill_missing=phases_config.examples.fill_missing,
    )


def _create_scheduler(
    engine: EngineContext, phase: Phase, *, only: frozenset[str] | None = None
) -> tuple[Scheduler, int]:
    """Create the appropriate scheduler and count the entries it will hand out."""
    operations: list[Result[APIOperation, InvalidSchema]] = list(engine.schema.get_all_operations())
    if phase.name != PhaseName.COVERAGE:
        # An operation whose required body has no usable schema cannot be sent valid data, so every phase
        # but coverage - which tests it by omitting the body - would judge it on data it never described.
        operations = [item for item in operations if not (isinstance(item, Ok) and item.ok().has_skipped_required_body)]
    if only is not None:
        operations = [item for item in operations if isinstance(item, Ok) and item.ok().label in only]
    # Entries the schema could not produce never claim a share, so counting them shrinks every other one.
    engine.start_unit_phase(total_operations=sum(1 for item in operations if isinstance(item, Ok)))
    return engine.schema.get_unit_scheduler(operations, phase), len(operations)


_TEST_MODES = {PhaseName.EXAMPLES: HypothesisTestMode.EXAMPLES, PhaseName.COVERAGE: HypothesisTestMode.COVERAGE}


@dataclass(slots=True)
class _SuiteProgress:
    """What the event stream has shown so far, kept across interrupts."""

    status: Status | None = None
    is_executed: bool = False
    checks_ran: bool = False


def execute(engine: EngineContext, phase: Phase, *, only: frozenset[str] | None = None) -> events.EventGenerator:
    """Run a set of unit tests.

    Implemented as a producer-consumer pattern via a task queue.
    The main thread provides an iterator over API operations and worker threads create test functions and run them.
    """
    mode = _TEST_MODES.get(phase.name, HypothesisTestMode.FUZZING)

    # Create scheduler based on ordering configuration
    try:
        scheduler, total_entries = _create_scheduler(engine, phase, only=only)
    except HookExecutionError as exc:
        yield events.NonFatalError(
            error=exc, phase=phase.name, label=f"`{exc.hook_name}` hook", related_to_operation=False
        )
        yield events.PhaseFinished(phase=phase, status=Status.ERROR, payload=None)
        return

    suite_started = events.SuiteStarted(phase=phase.name)

    yield suite_started

    progress = _SuiteProgress()

    try:
        with WorkerPool(
            # A worker beyond the entry count would find nothing to run, yet its thread still costs memory.
            workers_num=min(engine.config.workers, total_entries),
            scheduler=scheduler,
            worker_factory=worker_task,
            ctx=engine,
            mode=mode,
            phase=phase.name,
            suite_id=suite_started.id,
        ) as pool:
            try:
                yield from _consume_worker_events(pool, engine, phase.name, progress)
            except KeyboardInterrupt:
                # Soft stop, waiting for workers to terminate
                engine.stop()
                progress.status = Status.INTERRUPTED
                yield events.Interrupted(phase=phase.name)
    except KeyboardInterrupt as exc:
        # Hard stop, don't wait for worker threads
        if isinstance(exc.__context__, GeneratorExit):
            # The consumer abandoned the event stream and the interrupt arrived while workers were winding
            # down. Honor the close instead of emitting the events below into a generator that is going away.
            raise exc.__context__ from None

    yield from _report_outage(engine, phase.name, progress)
    status = _final_status(phase, progress)
    # NOTE: Right now there is just one suite, hence two events go one after another
    yield events.SuiteFinished(id=suite_started.id, phase=phase.name, status=status)
    yield events.PhaseFinished(phase=phase, status=status, payload=None)


def _consume_worker_events(
    pool: WorkerPool, engine: EngineContext, phase: PhaseName, progress: _SuiteProgress
) -> events.EventGenerator:
    """Forward worker events until every worker finishes, the run stops, or the failure limit is reached."""
    finished = 0
    while True:
        try:
            event = pool.events_queue.get(timeout=WORKER_TIMEOUT)
        except queue.Empty:
            # A worker may put its final events and exit between this thread's
            # get(timeout=...) raising Empty and the liveness check below.
            # Stop only when no producer remains AND nothing is left to drain.
            if _is_drained(pool):
                break
            continue
        if event is WORKER_FINISHED:
            finished += 1
            if finished == len(pool.workers):
                break
            continue
        progress.is_executed = True
        if engine.is_interrupted:
            raise KeyboardInterrupt
        yield event
        _fold_event(engine, phase, event, progress)
        # A reached failure limit stops at N by design. A spent budget has no cap to
        # keep, so let the workers wind down and hand over what they already produced.
        if engine.has_reached_the_failure_limit:
            break


def _is_drained(pool: WorkerPool) -> bool:
    return all(not worker.is_alive() for worker in pool.workers) and pool.events_queue.empty()


def _fold_event(engine: EngineContext, phase: PhaseName, event: events.EngineEvent, progress: _SuiteProgress) -> None:
    if isinstance(event, events.NonFatalError):
        progress.status = Status.ERROR
    if isinstance(event, events.ScenarioFinished):
        progress.status = _record_scenario_outcome(engine, phase, event, progress.status)
        progress.checks_ran = progress.checks_ran or any(event.recorder.checks.values())
    if isinstance(event, events.Interrupted) or engine.is_interrupted:
        progress.status = Status.INTERRUPTED
        engine.stop()


def _record_scenario_outcome(
    engine: EngineContext, phase: PhaseName, event: events.ScenarioFinished, status: Status | None
) -> Status | None:
    """Record a finished scenario's failures and observations, and fold its status into the suite status."""
    if event.status != Status.SKIP and (status is None or status < event.status):
        status = event.status
    if event.status in (Status.ERROR, Status.FAILURE):
        engine.control.count_failure((phase, event.label))
    engine.record_observations(event.recorder)
    return status


def _report_outage(engine: EngineContext, phase: PhaseName, progress: _SuiteProgress) -> events.EventGenerator:
    outage = engine.server.take_report(phase)
    if outage is not None:
        progress.is_executed = True
        progress.status = Status.ERROR
        yield outage


def _final_status(phase: Phase, progress: _SuiteProgress) -> Status:
    if not progress.is_executed:
        phase.skip_reason = PhaseSkipReason.NOTHING_TO_TEST
        return Status.SKIP
    if progress.status is None:
        return Status.SKIP
    if progress.status == Status.SUCCESS and not progress.checks_ran:
        phase.skip_reason = PhaseSkipReason.NO_CHECKS_RAN
        return Status.SKIP
    return progress.status


def worker_task(
    *,
    events_queue: Queue,
    scheduler: Scheduler,
    ctx: EngineContext,
    mode: HypothesisTestMode,
    phase: PhaseName,
    suite_id: uuid.UUID,
) -> None:
    from hypothesis.errors import HypothesisWarning

    warnings.filterwarnings("ignore", message="The recursion limit will not be reset", category=HypothesisWarning)
    with ignore_hypothesis_output():
        try:
            while not ctx.has_to_stop:
                result = scheduler.next_operation()
                if result is None:
                    # All operations exhausted
                    break

                if isinstance(result, Ok):
                    _run_operation(
                        result.ok(), events_queue=events_queue, ctx=ctx, mode=mode, phase=phase, suite_id=suite_id
                    )
                else:
                    error = result.err()
                    _put_error(
                        events_queue, error, phase=phase, suite_id=suite_id, method=error.method, path=error.path
                    )
        except KeyboardInterrupt:
            events_queue.put(events.Interrupted(phase=phase))


def _run_operation(
    operation: APIOperation,
    *,
    events_queue: Queue,
    ctx: EngineContext,
    mode: HypothesisTestMode,
    phase: PhaseName,
    suite_id: uuid.UUID,
) -> None:
    """Test one operation in this phase, or report why it is skipped."""
    ctx.take_operation_slice()
    verdict = _scheduling_verdict(ctx, operation, phase)
    if verdict.directive is SchedulingDirective.SKIP:
        _put_skipped_scenario(
            events_queue, operation, phase, suite_id, skip_reason=verdict.reason, skip_warning=verdict.warning
        )
        return
    _run_scenario(operation, events_queue=events_queue, ctx=ctx, mode=mode, phase=phase, suite_id=suite_id)


_DISABLED_VERDICT = Verdict(directive=SchedulingDirective.SKIP, reason="Disabled for this operation")


def _scheduling_verdict(ctx: EngineContext, operation: APIOperation, phase: PhaseName) -> Verdict:
    phases = ctx.config.phases_for(operation=operation)
    if _is_phase_disabled(phases, phase):
        return _DISABLED_VERDICT
    return ctx.supervisor.verdict(operation.label)


def _run_scenario(
    operation: APIOperation,
    *,
    events_queue: Queue,
    ctx: EngineContext,
    mode: HypothesisTestMode,
    phase: PhaseName,
    suite_id: uuid.UUID,
) -> None:
    as_strategy_kwargs = get_strategy_kwargs(ctx, operation=operation, phase=phase)
    feedback = ctx.feedback_for(operation=operation, phase=phase)
    scenario_started = events.ScenarioStarted(label=operation.label, phase=phase, suite_id=suite_id)
    if phase in (PhaseName.COVERAGE, PhaseName.EXAMPLES):
        generator = _build_driver_generator(operation, ctx, phase, as_strategy_kwargs, feedback)
        events_queue.put(scenario_started)
        _put_all(
            events_queue,
            run_driver(generator=generator, ctx=ctx, phase=phase, suite_id=suite_id, scenario_id=scenario_started.id),
        )
    else:
        _run_hypothesis_test(
            operation,
            events_queue=events_queue,
            ctx=ctx,
            mode=mode,
            phase=phase,
            scenario_started=scenario_started,
            as_strategy_kwargs=as_strategy_kwargs,
            feedback=feedback,
        )


def _build_driver_generator(
    operation: APIOperation,
    ctx: EngineContext,
    phase: PhaseName,
    as_strategy_kwargs: dict[str, Any],
    feedback: FeedbackSources,
) -> CaseGenerator:
    if phase == PhaseName.COVERAGE:
        return _build_coverage_generator(operation, ctx, as_strategy_kwargs, feedback)
    return _build_examples_generator(operation, ctx, as_strategy_kwargs, feedback)


def _run_hypothesis_test(
    operation: APIOperation,
    *,
    events_queue: Queue,
    ctx: EngineContext,
    mode: HypothesisTestMode,
    phase: PhaseName,
    scenario_started: events.ScenarioStarted,
    as_strategy_kwargs: dict[str, Any],
    feedback: FeedbackSources,
) -> None:
    from schemathesis.engine.run.unit._case import run_one_case
    from schemathesis.engine.run.unit._hypothesis_executor import run_test
    from schemathesis.generation.hypothesis.builder import create_test

    try:
        test_function = create_test(
            operation=operation,
            test_func=run_one_case,
            config=HypothesisTestConfig(
                modes=[mode],
                settings=ctx.config.get_hypothesis_settings(
                    operation=operation, phase=phase.value, apply_ci_profile=False
                ),
                apply_ci_profile=False,
                seed=ctx.operation_seed(operation),
                project=ctx.config,
                as_strategy_kwargs=as_strategy_kwargs,
                feedback=feedback,
            ),
        )
    except (InvalidSchema, InvalidArgument, AuthenticationError, ValidationError) as exc:
        if is_regex_validation_error(exc):
            exc = InvalidRegexPattern.from_jsonschema_rs_error(exc)
        _put_error(
            events_queue,
            exc,
            phase=phase,
            suite_id=scenario_started.suite_id,
            method=operation.method,
            path=operation.path,
        )
        return
    events_queue.put(scenario_started)
    # The test is blocking, meaning that even if CTRL-C comes to the main thread, this tasks will
    # continue executing. However, as we set a stop event, it will be checked before the next
    # network request. However, this is still suboptimal, as there could be slow requests and they
    # will block for longer
    _put_all(
        events_queue,
        run_test(
            operation=operation,
            test_function=test_function,
            ctx=ctx,
            phase=phase,
            suite_id=scenario_started.suite_id,
            scenario_id=scenario_started.id,
        ),
    )


def _put_all(events_queue: Queue, stream: events.EventGenerator) -> None:
    for event in stream:
        events_queue.put(event)


def _put_error(
    events_queue: Queue,
    error: Exception,
    *,
    phase: PhaseName,
    suite_id: uuid.UUID,
    method: str | None,
    path: str | None,
) -> None:
    if method and path:
        label = f"{method.upper()} {path}"
        scenario_started = events.ScenarioStarted(label=label, phase=phase, suite_id=suite_id)
        events_queue.put(scenario_started)

        events_queue.put(events.NonFatalError(error=error, phase=phase, label=label, related_to_operation=True))

        events_queue.put(
            events.ScenarioFinished(
                id=scenario_started.id,
                suite_id=suite_id,
                phase=phase,
                label=label,
                status=Status.ERROR,
                recorder=ScenarioRecorder(label="Error"),
                elapsed_time=0.0,
                skip_reason=None,
                is_final=True,
            )
        )
    else:
        events_queue.put(
            events.NonFatalError(
                error=error,
                phase=phase,
                label=path or "-",
                related_to_operation=False,
            )
        )


def _is_phase_disabled(phases: PhasesConfig, phase: PhaseName) -> bool:
    return (
        (phase == PhaseName.EXAMPLES and not phases.examples.enabled)
        or (phase == PhaseName.FUZZING and not phases.fuzzing.enabled)
        or (phase == PhaseName.COVERAGE and not phases.coverage.enabled)
    )


def _put_skipped_scenario(
    events_queue: Queue,
    operation: APIOperation,
    phase: PhaseName,
    suite_id: uuid.UUID,
    *,
    skip_reason: str | None,
    skip_warning: SchemathesisWarning | None = None,
) -> None:
    scenario_started = events.ScenarioStarted(label=operation.label, phase=phase, suite_id=suite_id)
    events_queue.put(scenario_started)
    events_queue.put(
        events.ScenarioFinished(
            id=scenario_started.id,
            suite_id=suite_id,
            phase=phase,
            label=operation.label,
            status=Status.SKIP,
            recorder=ScenarioRecorder(label=operation.label),
            elapsed_time=0.0,
            skip_reason=skip_reason,
            skip_warning=skip_warning,
            is_final=True,
        )
    )


def get_strategy_kwargs(ctx: EngineContext, *, operation: APIOperation, phase: PhaseName) -> dict[str, Any]:
    kwargs = {}
    override = overrides.for_operation(ctx.config, operation=operation)
    for location in ("query", "headers", "cookies", "path_parameters"):
        entry = getattr(override, location)
        if entry:
            kwargs[location] = entry
    headers = ctx.config.headers_for(operation=operation)
    if headers:
        kwargs["headers"] = {
            **kwargs.get("headers", {}),
            **{key: value for key, value in headers.items() if key.lower() != "user-agent"},
        }

    return kwargs
