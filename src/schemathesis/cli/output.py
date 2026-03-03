"""Shared CLI output utilities used by multiple commands."""

from __future__ import annotations

import os
import time
from collections.abc import Generator
from dataclasses import dataclass
from types import GeneratorType
from typing import TYPE_CHECKING, Any

import click

if TYPE_CHECKING:
    from rich.console import Console, Group
    from rich.progress import Progress, TaskID
    from rich.text import Text

    from schemathesis.core.errors import LoaderError

IO_ENCODING = os.getenv("PYTHONIOENCODING", "utf-8")

if IO_ENCODING != "utf-8":
    HEADER_SEPARATOR = "-"

    def _style(text: str, **kwargs: Any) -> str:
        text = text.encode(IO_ENCODING, errors="replace").decode("utf-8")
        return click.style(text, **kwargs)

else:
    HEADER_SEPARATOR = "━"

    def _style(text: str, **kwargs: Any) -> str:
        return click.style(text, **kwargs)


BLOCK_PADDING = (0, 1, 0, 1)


def bold(option: str) -> str:
    return click.style(option, bold=True)


def display_section_name(title: str, separator: str = "=", **kwargs: Any) -> None:
    """Print section name with separators in terminal with the given title nicely centered."""
    from schemathesis.cli.core import get_terminal_width

    message = f" {title} ".center(get_terminal_width(), separator)
    kwargs.setdefault("bold", True)
    click.echo(_style(message, **kwargs))


def _print_lines(lines: list[str | Generator[str, None, None]]) -> None:
    for entry in lines:
        if isinstance(entry, str):
            click.echo(entry)
        elif isinstance(entry, GeneratorType):
            for line in entry:
                click.echo(line)


def display_header(version: str) -> None:
    prefix = "v" if version != "dev" else ""
    header = f"Schemathesis {prefix}{version}"
    click.echo(_style(header, bold=True))
    click.echo(_style(HEADER_SEPARATOR * len(header), bold=True))
    click.echo()


def format_duration(duration_ms: int) -> str:
    """Format duration in milliseconds to seconds with 2 decimal places."""
    return f"{duration_ms / 1000:.2f}s"


def make_console(**kwargs: Any) -> Console:
    """Create a Rich console, using a fixed width in test environments."""
    from rich.console import Console

    if "PYTEST_VERSION" in os.environ:
        kwargs.setdefault("width", 240)
    return Console(**kwargs)


@dataclass
class LoadingProgressManager:
    """Manage the loading spinner and completion/error messages for schema loading."""

    console: Console
    location: str
    start_time: float
    progress: Progress
    progress_task_id: TaskID | None
    is_interrupted: bool

    __slots__ = ("console", "location", "start_time", "progress", "progress_task_id", "is_interrupted")

    def __init__(self, console: Console, location: str) -> None:
        from rich.progress import Progress, RenderableColumn, SpinnerColumn, TextColumn
        from rich.style import Style
        from rich.text import Text

        self.console = console
        self.location = location
        self.start_time = time.monotonic()
        progress_message = Text.assemble(
            ("Loading specification from ", Style(color="white")),
            (location, Style(color="cyan")),
        )
        self.progress = Progress(
            TextColumn(""),
            SpinnerColumn("clock"),
            RenderableColumn(progress_message),
            console=console,
            transient=True,
        )
        self.progress_task_id = None
        self.is_interrupted = False

    def start(self) -> None:
        """Start loading progress display."""
        self.progress_task_id = self.progress.add_task("Loading", total=None)
        self.progress.start()

    def stop(self) -> None:
        """Stop loading progress display."""
        assert self.progress_task_id is not None
        self.progress.stop_task(self.progress_task_id)
        self.progress.stop()

    def interrupt(self) -> None:
        """Handle interruption during loading."""
        self.is_interrupted = True
        self.stop()

    def get_completion_message(self) -> Text:
        """Generate completion message including duration."""
        from rich.style import Style
        from rich.text import Text

        duration = format_duration(int((time.monotonic() - self.start_time) * 1000))
        if self.is_interrupted:
            return Text.assemble(
                ("⚡  ", Style(color="yellow")),
                (f"Loading interrupted after {duration} while loading from ", Style(color="white")),
                (self.location, Style(color="cyan")),
            )
        return Text.assemble(
            ("✅  ", Style(color="green")),
            ("Loaded specification from ", Style(color="bright_white")),
            (self.location, Style(color="cyan")),
            (f" (in {duration})", Style(color="bright_white")),
        )

    def get_error_message(self, error: LoaderError) -> Group:
        from rich.console import Group
        from rich.style import Style
        from rich.text import Text

        duration = format_duration(int((time.monotonic() - self.start_time) * 1000))

        attempted = Text.assemble(
            ("❌  ", Style(color="red")),
            ("Failed to load specification from ", Style(color="white")),
            (self.location, Style(color="cyan")),
            (f" after {duration}", Style(color="white")),
        )

        error_title = Text("Schema Loading Error", style=Style(color="red", bold=True))
        error_message = Text(error.message)

        return Group(
            attempted,
            Text(),
            error_title,
            Text(),
            error_message,
        )
