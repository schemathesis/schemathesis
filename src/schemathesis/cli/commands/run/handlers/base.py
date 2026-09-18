from __future__ import annotations

import threading
from collections.abc import Callable
from io import StringIO
from pathlib import Path
from typing import IO, TYPE_CHECKING, Any, Generic, TypeVar

from schemathesis.cli.events import LoadingStarted
from schemathesis.cli.output import LoadingProgressManager, display_fatal_error

if TYPE_CHECKING:
    from rich.console import Console

    from schemathesis.cli.context import BaseExecutionContext
    from schemathesis.engine import events

T = TypeVar("T", bound="BaseExecutionContext")


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

    def _on_loading_started(self, event: LoadingStarted) -> None:
        self.loading_manager = LoadingProgressManager(console=self.console, location=event.location)
        self.loading_manager.start()

    def _on_fatal_error(self, ctx: T, event: events.FatalError) -> None:
        self.shutdown(ctx)
        display_fatal_error(self.console, self.loading_manager, event)
        self.loading_manager = None


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
