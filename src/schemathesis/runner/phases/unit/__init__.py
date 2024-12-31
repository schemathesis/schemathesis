"""Unit testing by Schemathesis Engine.

This module provides high-level flow for single-, and multi-threaded modes.
"""

from __future__ import annotations

import queue
import uuid
import warnings
from queue import Queue
from typing import TYPE_CHECKING, Any

from schemathesis.core.result import Ok
from schemathesis.generation.hypothesis.builder import HypothesisTestConfig
from schemathesis.generation.hypothesis.reporting import ignore_hypothesis_output
from schemathesis.runner import Status, events
from schemathesis.runner.models import TestResult
from schemathesis.runner.phases import PhaseName

from ._pool import TaskProducer, WorkerPool

if TYPE_CHECKING:
    from schemathesis.runner.phases import Phase

    from ....schemas import APIOperation
    from ...context import EngineContext

WORKER_TIMEOUT = 0.1


def execute(engine: EngineContext, phase: Phase) -> events.EventGenerator:
    """Run a set of unit tests.

    Implemented as a producer-consumer pattern via a task queue.
    The main thread provides an iterator over API operations and worker threads create test functions and run them.
    """
    producer = TaskProducer(engine)
    workers_num = engine.config.execution.workers_num

    suite_started = events.SuiteStarted(phase=phase.name)

    yield suite_started

    status = Status.SUCCESS

    with WorkerPool(workers_num=workers_num, producer=producer, worker_factory=worker_task, ctx=engine) as pool:
        try:
            while True:
                try:
                    event = pool.events_queue.get(timeout=WORKER_TIMEOUT)
                    if engine.is_stopped:
                        break
                    yield event
                    engine.on_event(event)
                    if isinstance(event, events.AfterExecution):
                        engine.record_item(event.status)
                        if status not in (Status.ERROR, Status.INTERRUPTED) and event.status in (
                            Status.FAILURE,
                            Status.ERROR,
                            Status.INTERRUPTED,
                        ):
                            status = event.status
                    if engine.is_stopped:
                        return  # type: ignore[unreachable]
                except queue.Empty:
                    if all(not worker.is_alive() for worker in pool.workers):
                        break
                    continue
        except KeyboardInterrupt:
            engine.control.stop()
            yield events.Interrupted(phase=PhaseName.UNIT_TESTING)

    # NOTE: Right now there is just one suite, hence two events go one after another
    yield events.SuiteFinished(id=suite_started.id, phase=phase.name, status=status)
    yield events.PhaseFinished(phase=phase, status=status, payload=None)


def worker_task(*, events_queue: Queue, producer: TaskProducer, ctx: EngineContext) -> None:
    from hypothesis.errors import HypothesisWarning

    from schemathesis.generation.hypothesis.builder import create_test

    from ._executor import run_test, test_func

    warnings.filterwarnings("ignore", message="The recursion limit will not be reset", category=HypothesisWarning)
    with ignore_hypothesis_output():
        try:
            while not ctx.is_stopped:
                result = producer.next_operation()
                if result is None:
                    break

                if isinstance(result, Ok):
                    operation = result.ok()
                    as_strategy_kwargs = get_strategy_kwargs(ctx, operation)
                    test_function = create_test(
                        operation=operation,
                        test_func=test_func,
                        config=HypothesisTestConfig(
                            settings=ctx.config.execution.hypothesis_settings,
                            seed=ctx.config.execution.seed,
                            generation=ctx.config.execution.generation_config,
                            as_strategy_kwargs=as_strategy_kwargs,
                        ),
                    )

                    # The test is blocking, meaning that even if CTRL-C comes to the main thread, this tasks will continue
                    # executing. However, as we set a stop event, it will be checked before the next network request.
                    # However, this is still suboptimal, as there could be slow requests and they will block for longer
                    for event in run_test(operation=operation, test_function=test_function, ctx=ctx):
                        events_queue.put(event)
                else:
                    error = result.err()
                    if error.method:
                        label = f"{error.method.upper()} {error.full_path}"
                        test_result = TestResult(label=label)

                        correlation_id = uuid.uuid4()
                        events_queue.put(events.BeforeExecution(label=label, correlation_id=correlation_id))

                        events_queue.put(events.NonFatalError(error=error, phase=PhaseName.UNIT_TESTING, label=label))

                        events_queue.put(
                            events.AfterExecution(
                                status=Status.ERROR,
                                result=test_result,
                                elapsed_time=0.0,
                                correlation_id=correlation_id,
                                skip_reason=None,
                            )
                        )
                    else:
                        assert error.full_path is not None
                        events_queue.put(
                            events.NonFatalError(error=error, phase=PhaseName.UNIT_TESTING, label=error.full_path)
                        )
        except KeyboardInterrupt:
            events_queue.put(events.Interrupted(phase=PhaseName.UNIT_TESTING))


def get_strategy_kwargs(ctx: EngineContext, operation: APIOperation) -> dict[str, Any]:
    kwargs = {}
    if ctx.config.override is not None:
        for location, entry in ctx.config.override.for_operation(operation).items():
            if entry:
                kwargs[location] = entry
    if ctx.config.network.headers:
        kwargs["headers"] = {
            key: value for key, value in ctx.config.network.headers.items() if key.lower() != "user-agent"
        }
    return kwargs
