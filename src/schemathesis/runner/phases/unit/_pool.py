from __future__ import annotations

import threading
from queue import Queue
from types import TracebackType
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from ....internal.result import Result
    from ...context import EngineContext


class TaskProducer:
    """Produces test tasks for workers to execute."""

    def __init__(self, ctx: EngineContext) -> None:
        self.operations = ctx.config.schema.get_all_operations(generation_config=ctx.config.execution.generation_config)
        self.lock = threading.Lock()

    def next_operation(self) -> Result | None:
        """Get next API operation in a thread-safe manner."""
        with self.lock:
            return next(self.operations, None)


class WorkerPool:
    """Manages a pool of worker threads."""

    def __init__(self, workers_num: int, producer: TaskProducer, worker_factory: Callable, ctx: EngineContext) -> None:
        self.workers_num = workers_num
        self.producer = producer
        self.worker_factory = worker_factory
        self.ctx = ctx
        self.workers: list[threading.Thread] = []
        self.events_queue: Queue = Queue()

    def start(self) -> None:
        """Start all worker threads."""
        for i in range(self.workers_num):
            worker = threading.Thread(
                target=self.worker_factory,
                kwargs={
                    "ctx": self.ctx,
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
        for worker in self.workers:
            worker.join()

    def __enter__(self) -> WorkerPool:
        self.start()
        return self

    def __exit__(self, ty: type[BaseException] | None, value: BaseException | None, tb: TracebackType | None) -> None:
        self.stop()
