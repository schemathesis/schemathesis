"""Unit testing by Schemathesis Engine.

This module provides high-level flow for single-, and multi-threaded modes.
"""

from __future__ import annotations

import queue
import uuid
import warnings
from contextlib import contextmanager
from functools import partial
from queue import Queue
from typing import TYPE_CHECKING, Any, Callable, Generator

from schemathesis.core.errors import InvalidSchema
from schemathesis.core.result import Ok
from schemathesis.generation.hypothesis.reporting import ignore_hypothesis_output

from ... import events
from ...events import EventGenerator
from ...models.outcome import TestResult
from ...models.status import Status
from ._pool import TaskProducer, WorkerPool

if TYPE_CHECKING:
    from requests import Session

    from ....schemas import APIOperation
    from ...context import EngineContext

WORKER_TIMEOUT = 0.1


def execute(ctx: EngineContext) -> EventGenerator:
    """Run a set of unit tests."""
    if ctx.config.execution.workers_num > 1:
        yield from multi_threaded(ctx)
    else:
        yield from single_threaded(ctx)


def single_threaded(ctx: EngineContext) -> EventGenerator:
    from schemathesis.generation.hypothesis.builder import get_all_tests

    from ._executor import run_test

    with network_test_function(ctx) as test_func:
        for result in get_all_tests(
            ctx.config.schema,
            test_func,
            settings=ctx.config.execution.hypothesis_settings,
            generation_config=ctx.config.execution.generation_config,
            seed=ctx.config.execution.seed,
            as_strategy_kwargs=lambda op: get_strategy_kwargs(ctx, op),
        ):
            if isinstance(result, Ok):
                operation, test_function = result.ok()
                correlation_id = None
                try:
                    for event in run_test(operation=operation, test_function=test_function, ctx=ctx):
                        yield event
                        ctx.on_event(event)
                        if isinstance(event, events.BeforeExecution):
                            correlation_id = event.correlation_id
                        if isinstance(event, events.Interrupted) or ctx.is_stopped:
                            return
                except InvalidSchema as error:
                    yield from on_schema_error(exc=error, ctx=ctx, correlation_id=correlation_id)
            else:
                yield from on_schema_error(exc=result.err(), ctx=ctx)


def multi_threaded(ctx: EngineContext) -> EventGenerator:
    """Execute tests in multiple threads.

    Implemented as a producer-consumer pattern via a task queue.
    The main thread provides an iterator over API operations and worker threads create test functions and run them.
    """
    producer = TaskProducer(ctx)
    workers_num = ctx.config.execution.workers_num

    with WorkerPool(workers_num=workers_num, producer=producer, worker_factory=worker_task, ctx=ctx) as pool:
        try:
            while True:
                try:
                    event = pool.events_queue.get(timeout=WORKER_TIMEOUT)
                    if ctx.is_stopped:
                        break
                    yield event
                    ctx.on_event(event)
                    if ctx.is_stopped:
                        return  # type: ignore[unreachable]
                except queue.Empty:
                    if all(not worker.is_alive() for worker in pool.workers):
                        break
                    continue
        except KeyboardInterrupt:
            ctx.control.stop()
            yield events.Interrupted()


def worker_task(*, events_queue: Queue, producer: TaskProducer, ctx: EngineContext) -> None:
    from hypothesis.errors import HypothesisWarning

    from schemathesis.generation.hypothesis.builder import create_test

    from ._executor import run_test

    warnings.filterwarnings("ignore", message="The recursion limit will not be reset", category=HypothesisWarning)
    with network_test_function(ctx) as test_func, ignore_hypothesis_output():
        while not ctx.is_stopped:
            result = producer.next_operation()
            if result is None:
                break

            if isinstance(result, Ok):
                operation = result.ok()
                as_strategy_kwargs = get_strategy_kwargs(ctx, operation)
                test_function = create_test(
                    operation=operation,
                    test=test_func,
                    settings=ctx.config.execution.hypothesis_settings,
                    seed=ctx.config.execution.seed,
                    generation_config=ctx.config.execution.generation_config,
                    as_strategy_kwargs=as_strategy_kwargs,
                )

                # The test is blocking, meaning that even if CTRL-C comes to the main thread, this tasks will continue
                # executing. However, as we set a stop event, it will be checked before the next network request.
                # However, this is still suboptimal, as there could be slow requests and they will block for longer
                for event in run_test(operation=operation, test_function=test_function, ctx=ctx):
                    events_queue.put(event)
            else:
                for event in on_schema_error(exc=result.err(), ctx=ctx):
                    events_queue.put(event)


def on_schema_error(*, exc: InvalidSchema, ctx: EngineContext, correlation_id: str | None = None) -> EventGenerator:
    """Handle schema-related errors during test execution."""
    if exc.method is not None:
        assert exc.path is not None
        assert exc.full_path is not None

        method = exc.method.upper()
        verbose_name = f"{method} {exc.full_path}"

        result = TestResult(verbose_name=verbose_name)
        result.add_error(exc)

        if correlation_id is None:
            correlation_id = uuid.uuid4().hex
            yield events.BeforeExecution(verbose_name=verbose_name, correlation_id=correlation_id)

        yield events.AfterExecution(
            status=Status.ERROR,
            result=result,
            elapsed_time=0.0,
            correlation_id=correlation_id,
        )
        ctx.add_result(result)
    else:
        ctx.add_error(exc)


@contextmanager
def get_session(auth: tuple[str, str] | None = None) -> Generator[Session, None, None]:
    from requests import Session

    with Session() as session:
        if auth is not None:
            session.auth = auth
        yield session


@contextmanager
def network_test_function(ctx: EngineContext) -> Generator[Callable, None, None]:
    from ._executor import network_test

    with get_session(ctx.config.network.auth) as session:
        yield partial(network_test, session=session)


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
