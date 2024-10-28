from __future__ import annotations

import queue
import threading
import warnings
from dataclasses import dataclass, field
from queue import Queue
from types import TracebackType
from typing import TYPE_CHECKING, Any, Callable, Generator, Iterable

from hypothesis.errors import HypothesisWarning

from ..._hypothesis._builder import create_test
from ...internal.result import Ok, Result
from ...transports.auth import get_requests_auth
from .. import events
from .._hypothesis import capture_hypothesis_output
from .core import BaseRunner, asgi_test, get_session, handle_schema_error, network_test, run_test, wsgi_test

if TYPE_CHECKING:
    import hypothesis

    from ...generation import DataGenerationMethod, GenerationConfig
    from ...internal.checks import CheckFunction
    from ...schemas import BaseSchema
    from ...targets import Target
    from ...transports import RequestConfig
    from ...types import RawAuth
    from .context import RunnerContext


@dataclass
class WorkerControl:
    """Control structure for worker threads."""

    should_stop: threading.Event = field(default_factory=threading.Event)
    tasks_available: threading.Event = field(default_factory=threading.Event)
    all_tasks_processed: threading.Event = field(default_factory=threading.Event)


class TaskProducer:
    """Manages task generation for workers."""

    def __init__(self, schema: BaseSchema, generation_config: GenerationConfig | None) -> None:
        self.tasks_generator = iter(schema.get_all_operations(generation_config=generation_config))
        self.lock = threading.Lock()

    def get_next_task(self) -> Result | None:
        with self.lock:
            try:
                return next(self.tasks_generator)
            except StopIteration:
                return None


class WorkerPool:
    """Manages a pool of worker threads."""

    def __init__(self, num_workers: int, producer: TaskProducer, worker_factory: Callable, worker_kwargs: dict):
        self.num_workers = num_workers
        self.producer = producer
        self.worker_factory = worker_factory
        self.worker_kwargs = worker_kwargs
        self.workers: list[threading.Thread] = []
        self.events_queue: Queue = Queue()
        self.control = WorkerControl()

    def start(self) -> None:
        """Start all worker threads."""
        for i in range(self.num_workers):
            worker = threading.Thread(
                target=self.worker_factory,
                kwargs={
                    **self.worker_kwargs,
                    "worker_control": self.control,
                    "events_queue": self.events_queue,
                    "producer": self.producer,
                },
                name=f"schemathesis_{i}",
                daemon=True,
            )
            self.workers.append(worker)
            worker.start()

    def stop(self) -> None:
        """Stop all workers gracefully."""
        self.control.should_stop.set()
        for worker in self.workers:
            worker.join()

    def __enter__(self) -> WorkerPool:
        self.start()
        return self

    def __exit__(
        self, exc_type: type[BaseException] | None, exc_val: BaseException | None, exc_tb: TracebackType | None
    ) -> None:
        self.stop()


def worker_task(
    *,
    worker_control: WorkerControl,
    events_queue: Queue,
    producer: TaskProducer,
    test_func: Callable,
    checks: Iterable[CheckFunction],
    targets: Iterable[Target],
    data_generation_methods: Iterable[DataGenerationMethod],
    settings: hypothesis.settings,
    generation_config: GenerationConfig,
    ctx: RunnerContext,
    headers: dict[str, Any] | None = None,
    **kwargs: Any,
) -> None:
    """Generic worker task implementation."""
    warnings.filterwarnings("ignore", message="The recursion limit will not be reset", category=HypothesisWarning)

    as_strategy_kwargs = {}
    if headers is not None:
        as_strategy_kwargs["headers"] = {key: value for key, value in headers.items() if key.lower() != "user-agent"}

    with capture_hypothesis_output():
        while not worker_control.should_stop.is_set():
            result = producer.get_next_task()
            if result is None:
                break

            if isinstance(result, Ok):
                operation = result.ok()
                test_function = create_test(
                    operation=operation,
                    test=test_func,
                    settings=settings,
                    seed=ctx.seed,
                    data_generation_methods=list(data_generation_methods),
                    generation_config=generation_config,
                    as_strategy_kwargs=as_strategy_kwargs,
                )

                # The test is blocking, meaning that even if CTRL-C comes to the main thread, this tasks will continue
                # executing. However, as we set a stop event, it will be checked before the next network request.
                # However, this is still suboptimal, as there could be slow requests and they will block for longer
                for event in run_test(
                    operation,
                    test_function,
                    checks,
                    data_generation_methods,
                    targets,
                    ctx=ctx,
                    headers=headers,
                    **kwargs,
                ):
                    events_queue.put(event)
            else:
                for event in handle_schema_error(result.err(), ctx, data_generation_methods):
                    events_queue.put(event)


def network_worker_task(*, auth: RawAuth | None, auth_type: str | None, **kwargs: Any) -> None:
    """Network-specific worker implementation."""
    prepared_auth = get_requests_auth(auth, auth_type)

    with get_session(prepared_auth) as session:
        worker_task(test_func=network_test, session=session, **kwargs)


def wsgi_worker_task(*, request_config: RequestConfig, **kwargs: Any) -> None:
    """WSGI-specific worker implementation."""
    worker_task(test_func=wsgi_test, **kwargs)


def asgi_worker_task(
    *, auth: RawAuth | None, auth_type: str | None, request_config: RequestConfig, **kwargs: Any
) -> None:
    """ASGI-specific worker implementation."""
    worker_task(test_func=asgi_test, **kwargs)


@dataclass
class ThreadPoolRunner(BaseRunner):
    """Base thread pool runner implementation."""

    workers_num: int = 2

    def _execute(self, ctx: RunnerContext) -> Generator[events.ExecutionEvent, None, None]:
        producer = TaskProducer(self.schema, self.generation_config)

        with WorkerPool(self.workers_num, producer, self._get_worker_task(), self._get_worker_kwargs(ctx)) as pool:
            try:
                while True:
                    try:
                        event = pool.events_queue.get(timeout=0.1)
                        if self._should_stop(event) or ctx.is_stopped:
                            break
                        yield event
                    except queue.Empty:
                        if all(not worker.is_alive() for worker in pool.workers):
                            break
                        continue

            except KeyboardInterrupt:
                ctx.stop_event.set()
                yield events.Interrupted()

    def _get_worker_task(self) -> Callable:
        return network_worker_task

    def _get_worker_kwargs(self, ctx: RunnerContext) -> dict[str, Any]:
        return {
            "checks": self.checks,
            "targets": self.targets,
            "settings": self.hypothesis_settings,
            "generation_config": self.generation_config,
            "auth": self.auth,
            "auth_type": self.auth_type,
            "headers": self.headers,
            "ctx": ctx,
            "data_generation_methods": self.schema.data_generation_methods,
            "request_config": self.request_config,
            "store_interactions": self.store_interactions,
            "max_response_time": self.max_response_time,
            "dry_run": self.dry_run,
        }


class ThreadPoolWSGIRunner(ThreadPoolRunner):
    """WSGI-specific thread pool runner."""

    def _get_worker_task(self) -> Callable:
        return wsgi_worker_task


class ThreadPoolASGIRunner(ThreadPoolRunner):
    """ASGI-specific thread pool runner."""

    def _get_worker_task(self) -> Callable:
        return asgi_worker_task
