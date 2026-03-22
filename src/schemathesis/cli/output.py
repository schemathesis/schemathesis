"""Shared CLI output utilities used by multiple commands."""

from __future__ import annotations

import os
import time
from collections.abc import Iterable
from dataclasses import dataclass
from types import GeneratorType
from typing import TYPE_CHECKING, Any

import click

from schemathesis.cli.constants import ISSUE_TRACKER_URL
from schemathesis.cli.core import get_terminal_width
from schemathesis.core.errors import LoaderErrorKind
from schemathesis.core.failures import MessageBlock, Severity, format_failures

if TYPE_CHECKING:
    from collections.abc import Generator

    from rich.console import Console, Group
    from rich.progress import Progress, TaskID
    from rich.text import Text

    from schemathesis.config import OutputConfig, ProjectConfig
    from schemathesis.core.errors import LoaderError
    from schemathesis.core.failures import MessageBlock
    from schemathesis.engine import events
    from schemathesis.engine.statistic import GroupedFailures, Statistic

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


def print_lines(lines: list[str | Generator[str, None, None]]) -> None:
    for entry in lines:
        if isinstance(entry, str):
            click.echo(entry)
        elif isinstance(entry, GeneratorType):
            for line in entry:
                click.echo(line)


def display_seed(config: ProjectConfig) -> None:
    click.echo(_style("Seed: ", bold=True), nl=False)
    # Deterministic mode can be applied to a subset of tests, but we only care if it is enabled everywhere.
    # If not everywhere, then the seed matters and should be displayed.
    if config.seed is None or config.generation.deterministic:
        click.echo("not used in the deterministic mode")
    else:
        click.echo(str(config.seed))
    click.echo()


def display_final_line(
    *,
    failures: int,
    errors: int,
    warnings: int = 0,
    running_time: float,
    total_cases: int,
) -> None:
    parts = []
    if failures:
        suffix = "s" if failures > 1 else ""
        parts.append(f"{failures} failure{suffix}")
    if errors:
        suffix = "s" if errors > 1 else ""
        parts.append(f"{errors} error{suffix}")
    if warnings:
        suffix = "s" if warnings > 1 else ""
        parts.append(f"{warnings} warning{suffix}")

    if parts:
        message = f"{', '.join(parts)} in {running_time:.2f}s"
        color = "red" if (failures or errors) else "yellow"
    elif total_cases == 0:
        message = "Empty test suite"
        color = "yellow"
    else:
        message = f"No issues found in {running_time:.2f}s"
        color = "green"

    display_section_name(message, fg=color)


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


def display_api_operations(
    *,
    selected: int,
    total: int,
    tested: int,
    errored: int = 0,
    skipped: int = 0,
    skip_reasons: list[str] | None = None,
) -> None:
    click.echo(_style("API Operations:", bold=True))
    click.echo(_style(f"  Selected: {click.style(str(selected), bold=True)}/{click.style(str(total), bold=True)}"))
    click.echo(_style(f"  Tested: {click.style(str(tested), bold=True)}"))
    if errored:
        click.echo(_style(f"  Errored: {click.style(str(errored), bold=True)}"))
    if skipped:
        click.echo(_style(f"  Skipped: {click.style(str(skipped), bold=True)}"))
        for reason in sorted(set(skip_reasons or [])):
            click.echo(_style(f"    - {reason.rstrip('.')}"))
    click.echo()


def display_failures_summary(statistic: Statistic) -> None:
    failure_counts: dict[str, tuple[Severity, int]] = {}
    for grouped in statistic.failures.values():
        for group in grouped.values():
            for failure in group.failures:
                data = failure_counts.get(failure.title, (failure.severity, 0))
                failure_counts[failure.title] = (failure.severity, data[1] + 1)
    click.echo(_style("Failures:", bold=True))
    sorted_failures = sorted(failure_counts.items(), key=lambda x: (x[1][0], x[0]))
    for title, (_, count) in sorted_failures:
        click.echo(_style(f"  ❌ {title}: "), nl=False)
        click.echo(_style(str(count), bold=True))
    click.echo()


def display_test_cases(statistic: Statistic) -> None:
    if statistic.total_cases == 0:
        click.echo(_style("Test cases:", bold=True))
        click.echo("  No test cases were generated\n")
        return

    unique_failures = sum(len(group.failures) for grouped in statistic.failures.values() for group in grouped.values())
    click.echo(_style("Test cases:", bold=True))
    parts = [f"  {click.style(str(statistic.total_cases), bold=True)} generated"]

    if statistic.cases_without_checks == statistic.total_cases:
        parts.append(f"{click.style(str(statistic.cases_without_checks), bold=True)} skipped")
    else:
        if unique_failures > 0:
            parts.append(
                f"{click.style(str(statistic.cases_with_failures), bold=True)} found "
                f"{click.style(str(unique_failures), bold=True)} unique failures"
            )
        else:
            parts.append(f"{click.style(str(statistic.total_cases), bold=True)} passed")
        if statistic.cases_without_checks > 0:
            parts.append(f"{click.style(str(statistic.cases_without_checks), bold=True)} skipped")

    click.echo(_style(", ".join(parts) + "\n"))


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


def _bold(text: str) -> str:
    return click.style(text, bold=True)


LOADER_ERROR_SUGGESTIONS: dict[LoaderErrorKind, str] = {
    LoaderErrorKind.CONNECTION_SSL: f"Bypass SSL verification with {_bold('`--tls-verify=false`')}.",
    LoaderErrorKind.CONNECTION_OTHER: f"Use {_bold('`--wait-for-schema=NUM`')} to wait up to NUM seconds for schema availability.",
    LoaderErrorKind.UNEXPECTED_CONTENT_TYPE: "Verify that the URL points directly to the Open API schema or GraphQL endpoint",
    LoaderErrorKind.HTTP_FORBIDDEN: "Verify your API keys or authentication headers.",
    LoaderErrorKind.HTTP_NOT_FOUND: "Verify that the URL points directly to the Open API schema or GraphQL endpoint",
    LoaderErrorKind.OPEN_API_UNSPECIFIED_VERSION: "Include the version in the schema.",
    LoaderErrorKind.YAML_NUMERIC_STATUS_CODES: "Convert numeric status codes to strings.",
    LoaderErrorKind.YAML_NON_STRING_KEYS: "Convert non-string keys to strings.",
    LoaderErrorKind.UNCLASSIFIED: f"If you suspect this is a Schemathesis issue and the schema is valid, please report it and include the schema if you can:\n\n  {ISSUE_TRACKER_URL}",
}

DEFAULT_INTERNAL_ERROR_MESSAGE = "An internal error occurred during the test run"


def _display_extras(extras: list[str]) -> None:
    if extras:
        click.echo()
    for extra in extras:
        click.echo(_style(f"    {extra}"))


def display_fatal_error(
    console: Console,
    loading_manager: LoadingProgressManager | None,
    event: events.FatalError,
    *,
    wait_for_schema: float | int | None = None,
) -> None:
    """Display a fatal error and raise click.Abort.

    Handles both loader errors (schema loading failures) and internal execution errors.
    """
    from rich.padding import Padding
    from rich.text import Text

    from schemathesis.core.errors import LoaderError, format_exception, split_traceback

    if isinstance(event.exception, LoaderError):
        assert loading_manager is not None
        message = Padding(loading_manager.get_error_message(event.exception), BLOCK_PADDING)
        console.print(message)
        console.print()

        if event.exception.extras:
            for extra in event.exception.extras:
                console.print(Padding(Text(extra), (0, 0, 0, 5)))
            console.print()

        if not (event.exception.kind == LoaderErrorKind.CONNECTION_OTHER and wait_for_schema is not None):
            suggestion = LOADER_ERROR_SUGGESTIONS.get(event.exception.kind)
            if suggestion is not None:
                click.echo(_style(f"{click.style('Tip:', bold=True, fg='green')} {suggestion}"))

        raise click.Abort

    traceback = format_exception(event.exception, with_traceback=True)
    extras = split_traceback(traceback)
    suggestion = f"Please consider reporting the traceback above to our issue tracker:\n\n  {ISSUE_TRACKER_URL}."
    click.echo(_style("Test Execution Error", fg="red", bold=True))
    click.echo()
    click.echo(DEFAULT_INTERNAL_ERROR_MESSAGE)
    _display_extras(extras)
    click.echo(_style(f"\n{click.style('Tip:', bold=True, fg='green')} {suggestion}"))
    raise click.Abort
