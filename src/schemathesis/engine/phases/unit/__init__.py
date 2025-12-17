"""Unit testing by Schemathesis Engine.

This module provides high-level flow for single-, and multi-threaded modes.
"""

from __future__ import annotations

import queue
import uuid
import warnings
from queue import Queue
from typing import TYPE_CHECKING, Any

from schemathesis.config import (
    CoveragePhaseConfig,
    ExamplesPhaseConfig,
    FuzzingPhaseConfig,
    OperationOrdering,
)
from schemathesis.core.errors import AuthenticationError, HookExecutionError, InvalidSchema
from schemathesis.core.result import Ok, Result
from schemathesis.engine import Status, events
from schemathesis.engine.phases import PhaseName, PhaseSkipReason
from schemathesis.engine.phases.unit._layered_scheduler import LayeredScheduler
from schemathesis.engine.phases.unit._ordering import compute_operation_layers
from schemathesis.engine.phases.unit._pool import DefaultScheduler, WorkerPool
from schemathesis.engine.recorder import ScenarioRecorder
from schemathesis.generation import overrides
from schemathesis.generation.hypothesis.builder import HypothesisTestConfig, HypothesisTestMode
from schemathesis.generation.hypothesis.reporting import ignore_hypothesis_output
from schemathesis.specs.openapi.schemas import OpenApiSchema

if TYPE_CHECKING:
    from schemathesis.engine.context import EngineContext
    from schemathesis.engine.phases import Phase
    from schemathesis.schemas import APIOperation

WORKER_TIMEOUT = 0.1


def _create_scheduler(engine: EngineContext, phase: Phase) -> DefaultScheduler | LayeredScheduler:
    """Create the appropriate scheduler based on ordering configuration.

    Args:
        engine: Engine context
        phase: Current phase

    Returns:
        Task scheduler (default or layered)

    """
    operations: list[Result[APIOperation, InvalidSchema]] = list(engine.schema.get_all_operations())
    # Check if this is an OpenAPI schema (ordering only works for OpenAPI)
    if not isinstance(engine.schema, OpenApiSchema):
        return DefaultScheduler(operations=operations)

    # Get operation ordering config for this phase
    phase_config = engine.config.phases.get_by_name(name=phase.name.name)
    assert isinstance(phase_config, (FuzzingPhaseConfig, CoveragePhaseConfig, ExamplesPhaseConfig))
    ordering = phase_config.operation_ordering

    # If ordering is disabled, use regular scheduler with collected operations
    if ordering == OperationOrdering.NONE:
        return DefaultScheduler(operations=operations)

    # Extract successful operations for layer computation and collect errors
    successes: list[APIOperation] = []
    errors: list[InvalidSchema] = []
    for result in operations:
        if isinstance(result, Ok):
            successes.append(result.ok())
        else:
            errors.append(result.err())

    if not successes:
        return DefaultScheduler(operations=operations)

    layers = compute_operation_layers(engine.schema, successes)

    # If only one layer or no layers, no coordination needed - use default scheduler
    if len(layers) <= 1:
        return DefaultScheduler(operations=operations)

    # Pass errors so they are reported after all successful operations are processed
    return LayeredScheduler(layers, errors=errors)


def execute(engine: EngineContext, phase: Phase) -> events.EventGenerator:
    """Run a set of unit tests.

    Implemented as a producer-consumer pattern via a task queue.
    The main thread provides an iterator over API operations and worker threads create test functions and run them.
    """
    if phase.name == PhaseName.EXAMPLES:
        mode = HypothesisTestMode.EXAMPLES
    elif phase.name == PhaseName.COVERAGE:
        mode = HypothesisTestMode.COVERAGE
    else:
        mode = HypothesisTestMode.FUZZING

    # Create scheduler based on ordering configuration
    try:
        scheduler = _create_scheduler(engine, phase)
    except HookExecutionError as exc:
        yield events.NonFatalError(
            error=exc, phase=phase.name, label=f"`{exc.hook_name}` hook", related_to_operation=False
        )
        yield events.PhaseFinished(phase=phase, status=Status.ERROR, payload=None)
        return

    suite_started = events.SuiteStarted(phase=phase.name)

    yield suite_started

    status = None
    is_executed = False

    try:
        with WorkerPool(
            workers_num=engine.config.workers,
            scheduler=scheduler,
            worker_factory=worker_task,
            ctx=engine,
            mode=mode,
            phase=phase.name,
            suite_id=suite_started.id,
        ) as pool:
            try:
                while True:
                    try:
                        event = pool.events_queue.get(timeout=WORKER_TIMEOUT)
                        is_executed = True
                        if engine.is_interrupted:
                            raise KeyboardInterrupt
                        yield event
                        if isinstance(event, events.NonFatalError):
                            status = Status.ERROR
                        if isinstance(event, events.ScenarioFinished):
                            if event.status != Status.SKIP and (status is None or status < event.status):
                                status = event.status
                            if event.status in (Status.ERROR, Status.FAILURE):
                                engine.control.count_failure()
                            engine.record_observations(event.recorder)
                        if isinstance(event, events.Interrupted) or engine.is_interrupted:
                            status = Status.INTERRUPTED
                            engine.stop()
                        if engine.has_to_stop:
                            break
                    except queue.Empty:
                        if all(not worker.is_alive() for worker in pool.workers):
                            break
                        continue
            except KeyboardInterrupt:
                # Soft stop, waiting for workers to terminate
                engine.stop()
                status = Status.INTERRUPTED
                yield events.Interrupted(phase=phase.name)
    except KeyboardInterrupt:
        # Hard stop, don't wait for worker threads
        pass

    if not is_executed:
        phase.skip_reason = PhaseSkipReason.NOTHING_TO_TEST
        status = Status.SKIP
    elif status is None:
        status = Status.SKIP
    # NOTE: Right now there is just one suite, hence two events go one after another
    yield events.SuiteFinished(id=suite_started.id, phase=phase.name, status=status)
    yield events.PhaseFinished(phase=phase, status=status, payload=None)


def worker_task(
    *,
    events_queue: Queue,
    scheduler: DefaultScheduler | LayeredScheduler,
    ctx: EngineContext,
    mode: HypothesisTestMode,
    phase: PhaseName,
    suite_id: uuid.UUID,
) -> None:
    from hypothesis.errors import HypothesisWarning, InvalidArgument

    from schemathesis.engine.phases.unit._executor import run_test, test_func
    from schemathesis.generation.hypothesis.builder import create_test

    def on_error(error: Exception, *, method: str | None = None, path: str | None = None) -> None:
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

    warnings.filterwarnings("ignore", message="The recursion limit will not be reset", category=HypothesisWarning)
    with ignore_hypothesis_output():
        try:
            while not ctx.has_to_stop:
                result = scheduler.next_operation()
                if result is None:
                    # All operations exhausted
                    break

                if isinstance(result, Ok):
                    operation = result.ok()
                    phases = ctx.config.phases_for(operation=operation)
                    # Skip tests if this phase is disabled
                    if (
                        (phase == PhaseName.EXAMPLES and not phases.examples.enabled)
                        or (phase == PhaseName.FUZZING and not phases.fuzzing.enabled)
                        or (phase == PhaseName.COVERAGE and not phases.coverage.enabled)
                    ):
                        continue
                    as_strategy_kwargs = get_strategy_kwargs(ctx, operation=operation, phase=phase)
                    scenario_started = events.ScenarioStarted(label=operation.label, phase=phase, suite_id=suite_id)
                    events_queue.put(scenario_started)
                    try:
                        test_function = create_test(
                            operation=operation,
                            test_func=test_func,
                            config=HypothesisTestConfig(
                                modes=[mode],
                                settings=ctx.config.get_hypothesis_settings(operation=operation, phase=phase.name),
                                seed=ctx.config.seed,
                                project=ctx.config,
                                as_strategy_kwargs=as_strategy_kwargs,
                            ),
                        )
                    except (InvalidSchema, InvalidArgument, AuthenticationError) as exc:
                        on_error(exc, method=operation.method, path=operation.path)
                        continue

                    # The test is blocking, meaning that even if CTRL-C comes to the main thread, this tasks will continue
                    # executing. However, as we set a stop event, it will be checked before the next network request.
                    # However, this is still suboptimal, as there could be slow requests and they will block for longer
                    for event in run_test(
                        operation=operation,
                        test_function=test_function,
                        ctx=ctx,
                        phase=phase,
                        suite_id=suite_id,
                        scenario_id=scenario_started.id,
                    ):
                        events_queue.put(event)
                else:
                    error = result.err()
                    on_error(error, method=error.method, path=error.path)
        except KeyboardInterrupt:
            events_queue.put(events.Interrupted(phase=phase))


def get_strategy_kwargs(ctx: EngineContext, *, operation: APIOperation, phase: PhaseName) -> dict[str, Any]:
    kwargs = {}
    override = overrides.for_operation(ctx.config, operation=operation)
    for location in ("query", "headers", "cookies", "path_parameters"):
        entry = getattr(override, location)
        if entry:
            kwargs[location] = entry
    headers = ctx.config.headers_for(operation=operation)
    if headers:
        kwargs["headers"] = {key: value for key, value in headers.items() if key.lower() != "user-agent"}

    # If extra data sources are enabled, pass them to augment data generation
    phase_config = ctx.config.phases_for(operation=operation).get_by_name(name=phase.name)
    if isinstance(phase_config, FuzzingPhaseConfig) and phase_config.extra_data_sources.is_enabled:
        kwargs["extra_data_source"] = ctx.extra_data_source

    return kwargs
