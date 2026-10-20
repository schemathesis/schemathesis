from __future__ import annotations

import threading
from collections.abc import Callable
from contextlib import AbstractContextManager
from io import StringIO
from pathlib import Path
from queue import Queue
from typing import IO, TYPE_CHECKING, Any, Generic, TypeVar

from schemathesis.cli.events import LoadingStarted
from schemathesis.cli.output import LoadingProgressManager, display_fatal_error
from schemathesis.core.output.sanitization import sanitized

if TYPE_CHECKING:
    from rich.console import Console

    from schemathesis.cli.context import BaseExecutionContext
    from schemathesis.engine import events

T = TypeVar("T", bound="BaseExecutionContext")
ItemT = TypeVar("ItemT")


class EventHandler(Generic[T]):
    def __init__(self, *args: Any, **params: Any) -> None: ...

    def handle_event(self, ctx: T, event: events.EngineEvent) -> None:
        raise NotImplementedError

    def start(self, ctx: T) -> None: ...

    def shutdown(self, ctx: T) -> None: ...


class BaseOutputHandler(EventHandler[T]):
    """Shared loading-spinner and fatal-error logic for CLI output handlers."""

    console: Console
    loading_manager: LoadingProgressManager | None

    def _on_loading_started(self, ctx: T, event: LoadingStarted) -> None:
        location = sanitized(event.location, ctx.config.output.sanitization)
        assert location is not None
        self.loading_manager = LoadingProgressManager(console=self.console, location=location)
        self.loading_manager.start()

    def _on_fatal_error(self, ctx: T, event: events.FatalError) -> None:
        self.shutdown(ctx)
        display_fatal_error(self.console, self.loading_manager, event, wait_for_schema=ctx.config.wait_for_schema)


TextOutput = IO[str] | StringIO | Path

WRITER_WORKER_JOIN_TIMEOUT = 10


class WriterWorker(threading.Thread):
    """A background thread that writes a report.

    Keeps whatever ended the thread so `join` can re-raise it, instead of silently truncating the report.
    """

    def __init__(self, *, name: str, target: Callable[..., None], kwargs: dict[str, Any]) -> None:
        super().__init__(name=name, target=target, kwargs=kwargs)
        self.error: Exception | None = None

    def run(self) -> None:
        try:
            super().run()
        except Exception as exc:
            self.error = exc

    def join(self, timeout: float | None = WRITER_WORKER_JOIN_TIMEOUT) -> None:
        super().join(timeout)
        if self.error is not None:
            raise self.error


class BackgroundWriterHandler(EventHandler, Generic[ItemT]):
    """Hands report items to a writer thread; `None` on the queue tells the thread to finish."""

    __slots__ = ("queue", "worker")

    def __init__(self, queue: Queue[ItemT | None] | None = None) -> None:
        self.queue: Queue[ItemT | None] = queue or Queue()
        self.worker: WriterWorker | None = None

    def _start_writer(self, name: str, target: Callable[..., None], **kwargs: object) -> None:
        self.worker = WriterWorker(name=name, target=target, kwargs={**kwargs, "queue": self.queue})
        self.worker.start()

    def shutdown(self, ctx: BaseExecutionContext) -> None:
        self.queue.put(None)
        if self.worker is not None:
            self.worker.join()


def write_until_finished(
    writer: AbstractContextManager[object],
    open: Callable[[], None],
    write: Callable[[ItemT], None],
    queue: Queue[ItemT | None],
) -> None:
    with writer:
        open()
        while True:
            item = queue.get()
            if item is None:
                break
            write(item)
