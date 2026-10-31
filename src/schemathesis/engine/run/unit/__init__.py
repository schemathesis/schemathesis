"""Unit testing by Schemathesis Engine.

This module provides high-level flow for single-, and multi-threaded modes.
"""

from __future__ import annotations

import uuid
import warnings
from queue import Queue
from typing import TYPE_CHECKING

from hypothesis.errors import InvalidArgument
from jsonschema_rs import ValidationError

from schemathesis.config import CoveragePhaseConfig, ExamplesPhaseConfig, FuzzingPhaseConfig, OperationOrdering
from schemathesis.core.errors import (
    AuthenticationError,
    HookExecutionError,
    InvalidRegexPattern,
    InvalidSchema,
    is_regex_validation_error,
)
from schemathesis.core.result import Err, Ok, Result
from schemathesis.core.spec import Scheduler
from schemathesis.engine import Status, StopReason, events
from schemathesis.engine.recorder import ScenarioRecorder
from schemathesis.engine.run import UNIT_PHASES, PhaseName, PhaseProgress, relay_events, report_outage
from schemathesis.engine.run.unit._direct_executor import run_driver
from schemathesis.engine.run.unit._layered_scheduler import LayeredScheduler
from schemathesis.engine.run.unit._pool import WORKER_FINISHED, DefaultScheduler, WorkerPool, split_results
from schemathesis.engine.supervisor import SchedulingDirective, Verdict
from schemathesis.generation import overrides
from schemathesis.generation.drivers import CaseGenerator, CoverageGenerator, ExamplesGenerator
from schemathesis.generation.feedback import FeedbackSources
from schemathesis.generation.hypothesis.builder import HypothesisTestConfig, HypothesisTestMode
from schemathesis.generation.hypothesis.reporting import ignore_hypothesis_output

if TYPE_CHECKING:
    from schemathesis.config import PhasesConfig
    from schemathesis.engine.context import EngineContext
    from schemathesis.engine.run import Phase
    from schemathesis.generation.overrides import Override
    from schemathesis.schemas import APIOperation

WORKER_TIMEOUT = 0.1


def _build_coverage_generator(
    operation: APIOperation, ctx: EngineContext, override: Override, feedback: FeedbackSources
) -> CoverageGenerator:
    generation = ctx.config.generation_for(operation=operation, phase=PhaseName.COVERAGE.value)
    return CoverageGenerator(
        operation=operation,
        generation_modes=generation.modes,
        generation_config=generation,
        auth_storage=None,
        override=override,
        feedback=feedback,
        session=ctx.coverage_session,
    )


def _build_examples_generator(
    operation: APIOperation, ctx: EngineContext, override: Override, feedback: FeedbackSources
) -> ExamplesGenerator:
    phases_config = ctx.config.phases_for(operation=operation)
    return ExamplesGenerator(
        operation=operation,
        override=override,
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
    return _order_operations(engine, phase, operations), len(operations)


def _order_operations(
    engine: EngineContext, phase: Phase, operations: list[Result[APIOperation, InvalidSchema]]
) -> Scheduler:
    if _keeps_schema_order(engine, phase):
        return DefaultScheduler(operations=operations)
    successes, errors = split_results(operations)
    return _scheduler_for_layers(engine.schema.get_operation_layers(successes), operations, errors)


def _keeps_schema_order(engine: EngineContext, phase: Phase) -> bool:
    phase_config = engine.config.phases.get_by_name(name=phase.name.value)
    assert isinstance(phase_config, (FuzzingPhaseConfig, CoveragePhaseConfig, ExamplesPhaseConfig))
    return phase_config.operation_ordering == OperationOrdering.NONE


def _scheduler_for_layers(
    layers: list[list[APIOperation]],
    operations: list[Result[APIOperation, InvalidSchema]],
    errors: list[InvalidSchema],
) -> Scheduler:
    if not layers:
        return DefaultScheduler(operations=operations)
    if len(layers) == 1:
        return DefaultScheduler(
            operations=[*(Ok(operation) for operation in layers[0]), *(Err(error) for error in errors)]
        )
    return LayeredScheduler(layers, errors=errors)


def execute(engine: EngineContext, phase: Phase, *, only: frozenset[str] | None = None) -> events.EventGenerator:
    """Run a set of unit tests.

    Implemented as a producer-consumer pattern via a task queue.
    The main thread provides an iterator over API operations and worker threads create test functions and run them.
    """
    # Create scheduler based on ordering configuration
    try:
        scheduler, total_entries = _create_scheduler(engine, phase, only=only)
    except HookExecutionError as exc:
        yield from events.fail_phase(phase, exc, label=f"`{exc.hook_name}` hook")
        return

    suite_started = events.SuiteStarted(phase=phase.name)

    yield suite_started

    progress = PhaseProgress()

    try:
        with WorkerPool(
            # A worker beyond the entry count would find nothing to run, yet its thread still costs memory.
            workers_num=min(engine.config.workers, total_entries),
            scheduler=scheduler,
            worker_factory=worker_task,
            ctx=engine,
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

    yield from report_outage(engine, phase.name, progress)
    yield from _report_unreached(engine, scheduler, phase.name, suite_started.id)
    status = progress.final_status(phase)
    # NOTE: Right now there is just one suite, hence two events go one after another
    yield events.SuiteFinished(id=suite_started.id, phase=phase.name, status=status)
    yield events.PhaseFinished(phase=phase, status=status, payload=None)


def _consume_worker_events(
    pool: WorkerPool, engine: EngineContext, phase: PhaseName, progress: PhaseProgress
) -> events.EventGenerator:
    """Forward worker events until every worker finishes, the run stops, or the failure limit is reached."""
    finished = 0
    for event in relay_events(pool.events_queue, pool.workers, timeout=WORKER_TIMEOUT):
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


def _fold_event(engine: EngineContext, phase: PhaseName, event: events.EngineEvent, progress: PhaseProgress) -> None:
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


def _report_unreached(
    engine: EngineContext, scheduler: Scheduler, phase: PhaseName, suite_id: uuid.UUID
) -> events.EventGenerator:
    """Skip the operations the time limit kept this phase from; a repeated pass already reached them once."""
    if engine.stop_reason is not StopReason.MAX_TIME or engine.cycle_index > 0:
        return
    while (result := scheduler.next_operation()) is not None:
        if isinstance(result, Ok):
            yield from events.iter_skipped_scenario(
                result.ok().label, phase, suite_id, skip_reason=StopReason.MAX_TIME.skip_explanation
            )


def worker_task(
    *,
    events_queue: Queue,
    scheduler: Scheduler,
    ctx: EngineContext,
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
                    _run_operation(result.ok(), events_queue=events_queue, ctx=ctx, phase=phase, suite_id=suite_id)
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
    phase: PhaseName,
    suite_id: uuid.UUID,
) -> None:
    """Test one operation in this phase, or report why it is skipped."""
    ctx.take_operation_slice()
    for error in operation.schema_errors:
        events_queue.put(
            events.NonFatalError(error=error, phase=phase, label=operation.label, related_to_operation=True)
        )
    verdict = _scheduling_verdict(ctx, operation, phase)
    if verdict.directive is SchedulingDirective.SKIP:
        events.put_skipped_scenario(
            events_queue, operation.label, phase, suite_id, skip_reason=verdict.reason, skip_warning=verdict.warning
        )
        return
    _run_scenario(operation, events_queue=events_queue, ctx=ctx, phase=phase, suite_id=suite_id)


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
    phase: PhaseName,
    suite_id: uuid.UUID,
) -> None:
    scenario_started = events.ScenarioStarted(label=operation.label, phase=phase, suite_id=suite_id)
    events_queue.put(scenario_started)
    try:
        override = overrides.with_config_headers(ctx.config, operation=operation)
        feedback = ctx.feedback_for(operation=operation, phase=phase)
        if phase in (PhaseName.COVERAGE, PhaseName.EXAMPLES):
            generator = _build_driver_generator(operation, ctx, phase, override, feedback)
            _put_all(
                events_queue,
                run_driver(
                    generator=generator, ctx=ctx, phase=phase, suite_id=suite_id, scenario_id=scenario_started.id
                ),
            )
        else:
            _run_hypothesis_test(
                operation,
                events_queue=events_queue,
                ctx=ctx,
                phase=phase,
                scenario_started=scenario_started,
                override=override,
                feedback=feedback,
            )
    except Exception as exc:
        # An unexpected crash still closes the scenario as an error, otherwise the operation reads as skipped.
        _finish_with_error(events_queue, exc, scenario_started)


def _build_driver_generator(
    operation: APIOperation,
    ctx: EngineContext,
    phase: PhaseName,
    override: Override,
    feedback: FeedbackSources,
) -> CaseGenerator:
    if phase == PhaseName.COVERAGE:
        return _build_coverage_generator(operation, ctx, override, feedback)
    return _build_examples_generator(operation, ctx, override, feedback)


def _run_hypothesis_test(
    operation: APIOperation,
    *,
    events_queue: Queue,
    ctx: EngineContext,
    phase: PhaseName,
    scenario_started: events.ScenarioStarted,
    override: Override,
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
                modes=[HypothesisTestMode.FUZZING],
                settings=ctx.config.get_hypothesis_settings(
                    operation=operation, phase=phase.value, apply_ci_profile=False
                ),
                explicit_setting_names=ctx.config.explicit_hypothesis_settings(operation=operation, phase=phase.value),
                apply_ci_profile=False,
                seed=ctx.operation_seed(operation),
                project=ctx.config,
                override=override,
                feedback=feedback,
            ),
        )
    except (InvalidSchema, InvalidArgument, AuthenticationError, ValidationError) as exc:
        if is_regex_validation_error(exc):
            exc = InvalidRegexPattern.from_jsonschema_rs_error(exc)
        _finish_with_error(events_queue, exc, scenario_started)
        return
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
        _finish_with_error(events_queue, error, scenario_started)
    else:
        events_queue.put(
            events.NonFatalError(
                error=error,
                phase=phase,
                label=path or "-",
                related_to_operation=False,
            )
        )


def _finish_with_error(events_queue: Queue, error: Exception, scenario_started: events.ScenarioStarted) -> None:
    label = scenario_started.label
    assert label is not None
    phase = scenario_started.phase
    events_queue.put(events.NonFatalError(error=error, phase=phase, label=label, related_to_operation=True))
    events_queue.put(
        events.ScenarioFinished(
            id=scenario_started.id,
            suite_id=scenario_started.suite_id,
            phase=phase,
            label=label,
            status=Status.ERROR,
            recorder=ScenarioRecorder(label="Error"),
            elapsed_time=0.0,
            skip_reason=None,
            is_final=True,
        )
    )


def _is_phase_disabled(phases: PhasesConfig, phase: PhaseName) -> bool:
    return phase in UNIT_PHASES and not phases.get_by_name(name=phase).enabled
