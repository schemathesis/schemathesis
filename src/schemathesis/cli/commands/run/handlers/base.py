from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from io import StringIO
from pathlib import Path
from typing import IO, TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from schemathesis.cli.commands.run.context import ExecutionContext
    from schemathesis.engine import events


class EventHandler:
    def __init__(self, *args: Any, **params: Any) -> None: ...

    def handle_event(self, ctx: ExecutionContext, event: events.EngineEvent) -> None:
        raise NotImplementedError

    def start(self, ctx: ExecutionContext) -> None: ...

    def shutdown(self, ctx: ExecutionContext) -> None: ...


class WritableText(Protocol):
    """Protocol for text-writable file-like objects."""

    def write(self, s: str) -> int: ...  # pragma: no cover
    def flush(self) -> None: ...  # pragma: no cover


TextOutput = IO[str] | StringIO | Path


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
