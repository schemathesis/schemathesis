"""Output written by an in-process application under test."""

from __future__ import annotations

import sys
from collections.abc import Generator, Iterable
from contextlib import contextmanager
from contextvars import ContextVar
from typing import IO, Any

_INSIDE_APPLICATION: ContextVar[bool] = ContextVar("inside_application", default=False)


@contextmanager
def inside_application() -> Generator[None, None, None]:
    """Mark the code that runs here, also on threads it is handed to, as the application's."""
    token = _INSIDE_APPLICATION.set(True)
    try:
        yield
    finally:
        _INSIDE_APPLICATION.reset(token)


class _WithoutApplicationOutput:
    """A stream that drops what the application writes and passes everything else through."""

    def __init__(self, stream: IO[str]) -> None:
        self._stream = stream

    def write(self, text: str) -> int:
        if _INSIDE_APPLICATION.get():
            return len(text)
        return self._stream.write(text)

    def writelines(self, lines: Iterable[str]) -> None:
        for line in lines:
            self.write(line)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._stream, name)


@contextmanager
def hidden_application_output() -> Generator[None, None, None]:
    """Keep what an in-process application prints or logs out of the terminal."""
    stdout, stderr = sys.stdout, sys.stderr
    sys.stdout = _WithoutApplicationOutput(stdout)
    sys.stderr = _WithoutApplicationOutput(stderr)
    try:
        yield
    finally:
        sys.stdout, sys.stderr = stdout, stderr
