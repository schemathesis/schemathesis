"""Shared CLI output utilities used by multiple commands."""

from __future__ import annotations

import os
import time
from collections.abc import Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import click

from schemathesis.cli.core import get_terminal_width
from schemathesis.core.failures import MessageBlock, format_failures

if TYPE_CHECKING:
    from rich.console import Console, Group
    from rich.progress import Progress, TaskID
    from rich.text import Text

    from schemathesis.cli.statistics import GroupedFailures, Statistic
    from schemathesis.config import OutputConfig
    from schemathesis.core.errors import LoaderError
    from schemathesis.core.failures import MessageBlock
    from schemathesis.engine import events

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


def display_section_name(title: str, separator: str = "=", **kwargs: object) -> None:
    """Print section name centered with separators."""
    message = f" {title} ".center(get_terminal_width(), separator)
    kwargs.setdefault("bold", True)
    click.echo(_style(message, **kwargs))


def failure_formatter(block: MessageBlock, content: str) -> str:
    if block == MessageBlock.CASE_ID:
        return _style(content, bold=True)
    if block == MessageBlock.FAILURE:
        return _style(content, fg="red", bold=True)
    if block == MessageBlock.STATUS:
        return _style(content, bold=True)
    assert block == MessageBlock.CURL
    return _style(content.replace("Reproduce with", click.style("Reproduce with", bold=True)))


def display_failures_for_single_test(config: OutputConfig, label: str, checks: Iterable[GroupedFailures]) -> None:
    """Display failures for a single operation."""
    display_section_name(label, "_", fg="red")
    for idx, group in enumerate(checks, 1):
        click.echo(
            format_failures(
                case_id=f"{idx}. Test Case ID: {group.case_id}",
                response=group.response,
                failures=group.failures,
                curl=group.code_sample,
                formatter=failure_formatter,
                config=config,
            )
        )
        click.echo()


def display_failures(statistic: Statistic, config: OutputConfig) -> None:
    """Display all failures in the test run."""
    if not statistic.failures:
        return
    display_section_name("FAILURES")
    for label, failures in statistic.failures.items():
        display_failures_for_single_test(config, label, failures.values())


def display_errors_summary(errors: set[events.NonFatalError]) -> None:
    """Display a summary of non-fatal errors grouped by title."""
    error_counts: dict[str, int] = {}
    for error in errors:
        title = error.info.title
        error_counts[title] = error_counts.get(title, 0) + 1
    click.echo(_style("Errors:", bold=True))
    for title in sorted(error_counts):
        click.echo(_style(f"  🚫 {title}: "), nl=False)
        click.echo(_style(str(error_counts[title]), bold=True))
    click.echo()
