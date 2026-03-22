from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from io import StringIO
from pathlib import Path
from typing import IO, TYPE_CHECKING, Any, Generic, Protocol, TypeVar

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


class WritableText(Protocol):
    """Protocol for text-writable file-like objects."""

    def write(self, s: str) -> int: ...  # pragma: no cover
    def flush(self) -> None: ...  # pragma: no cover


TextOutput = IO[str] | StringIO | Path

WRITER_WORKER_JOIN_TIMEOUT = 1


@contextmanager
def open_text_output(output: TextOutput) -> Generator[IO[str]]:
    """Open a text output, handling both Path and file-like objects."""
    if isinstance(output, Path):
        f = open(output, "w", encoding="utf-8")
        try:
            yield f
        finally:
            f.close()
    else:
        # Assume it's already a file-like object
        yield output
