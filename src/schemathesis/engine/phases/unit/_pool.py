from __future__ import annotations

import threading
import uuid
from collections.abc import Callable
from queue import Queue
from types import TracebackType
from typing import TYPE_CHECKING

from schemathesis.core.errors import InvalidSchema
from schemathesis.core.result import Result
from schemathesis.engine.phases import PhaseName
from schemathesis.schemas import APIOperation

if TYPE_CHECKING:
    from schemathesis.engine.context import EngineContext
    from schemathesis.engine.phases.unit._layered_scheduler import LayeredScheduler
    from schemathesis.generation.hypothesis.builder import HypothesisTestMode


class DefaultScheduler:
    """Default scheduler that processes operations in schema iteration order."""

    def __init__(self, operations: list[Result[APIOperation, InvalidSchema]]) -> None:
        self.operations = iter(operations)
        self.lock = threading.Lock()

    def next_operation(self) -> Result[APIOperation, InvalidSchema] | None:
        """Get next API operation in a thread-safe manner."""
        with self.lock:
            return next(self.operations, None)


class WorkerPool:
    """Manages a pool of worker threads."""

    def __init__(
        self,
        workers_num: int,
        scheduler: DefaultScheduler | LayeredScheduler,
        worker_factory: Callable,
        ctx: EngineContext,
        mode: HypothesisTestMode,
        phase: PhaseName,
        suite_id: uuid.UUID,
    ) -> None:
        self.workers_num = workers_num
        self.scheduler = scheduler
        self.worker_factory = worker_factory
        self.ctx = ctx
        self.mode = mode
        self.phase = phase
        self.suite_id = suite_id
        self.workers: list[threading.Thread] = []
        self.events_queue: Queue = Queue()

    def start(self) -> None:
        """Start all worker threads."""
        for i in range(self.workers_num):
            worker = threading.Thread(
                target=self.worker_factory,
                kwargs={
                    "ctx": self.ctx,
                    "mode": self.mode,
                    "phase": self.phase,
                    "events_queue": self.events_queue,
                    "scheduler": self.scheduler,
                    "suite_id": self.suite_id,
                },
                name=f"schemathesis_unit_tests_{i}",
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
