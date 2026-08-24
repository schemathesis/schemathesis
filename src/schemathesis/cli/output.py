"""Shared CLI output utilities used by multiple commands."""

from __future__ import annotations

import os
from collections.abc import Iterable
from dataclasses import dataclass
from types import GeneratorType
from typing import TYPE_CHECKING, Any

import click

from schemathesis.cli.constants import ISSUE_TRACKER_URL
from schemathesis.cli.core import get_terminal_width
from schemathesis.cli.summary import ErrorGroup, FailureGroup, OperationsSummary, TestCasesSummary
from schemathesis.core.errors import LoaderErrorKind
from schemathesis.core.failures import (
    MessageBlock,
    format_failures,
    is_reproducible_failure,
)
from schemathesis.core.timing import Instant

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

    banner = format_summary_banner(message, width=get_terminal_width())
    click.echo(_style(banner, bold=True, fg=color))


def display_header(version: str) -> None:
    prefix = "v" if version != "dev" else ""
    header = f"Schemathesis {prefix}{version}"
    click.echo(_style(header, bold=True))
    click.echo(_style(HEADER_SEPARATOR * len(header), bold=True))
    click.echo()


def format_summary_banner(message: str, *, width: int) -> str:
    return f" {message} ".center(width, "=")


def append_replay_command(code_sample: str | None, case_id: str | None) -> str | None:
    """Reproduce snippet with the `st replay <case_id>` line appended, when both parts are present."""
    if code_sample is None or not case_id:
        return code_sample
    return f"{code_sample}\n\nst replay {case_id}"


def format_duration(duration_ms: int) -> str:
    """Format duration in milliseconds to seconds with 2 decimal places."""
    return f"{duration_ms / 1000:.2f}s"


def make_progress_bar(console: Console, *, indent: str = "", transient: bool = True) -> Progress:
    from rich.progress import BarColumn, Progress, TextColumn, TimeElapsedColumn

    return Progress(
        TextColumn(indent),
        TimeElapsedColumn(),
        BarColumn(bar_width=None),
        TextColumn("{task.percentage:.0f}% ({task.completed}/{task.total})"),
        console=console,
        transient=transient,
    )


def make_console(**kwargs: Any) -> Console:
    """Create a Rich console, using a fixed width in test environments."""
    from rich.console import Console

    if "PYTEST_VERSION" in os.environ:
        kwargs.setdefault("width", 240)
        # Snapshots assume non-terminal rendering; force it even when CI sets FORCE_COLOR=1.
        kwargs.setdefault("force_terminal", False)
        kwargs.setdefault("color_system", None)
    return Console(**kwargs)


@dataclass(slots=True)
class LoadingProgressManager:
    """Manage the loading spinner and completion/error messages for schema loading."""

    console: Console
    location: str
    started_at: Instant
    progress: Progress
    progress_task_id: TaskID | None
    is_interrupted: bool

    def __init__(self, console: Console, location: str) -> None:
        from rich.progress import Progress, RenderableColumn, SpinnerColumn, TextColumn
        from rich.style import Style
        from rich.text import Text

        self.console = console
        self.location = location
        self.started_at = Instant()
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

        duration = format_duration(self.started_at.elapsed_ms)
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

        duration = format_duration(self.started_at.elapsed_ms)

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


def display_failures_for_single_test(
    config: OutputConfig, label: str, checks: Iterable[GroupedFailures], *, record_crashes: bool
) -> None:
    """Display failures for a single operation."""
    display_section_name(label, "_", fg="red")
    for idx, group in enumerate(checks, 1):
        case_id = f"{idx}. Test Case ID: {group.case_id}" if group.case_id is not None else None
        # Only hint `st replay` when a crash file was actually recorded for this case.
        if record_crashes and any(is_reproducible_failure(failure) for failure in group.failures):
            reproduce = append_replay_command(group.code_sample, group.case_id)
        else:
            reproduce = group.code_sample
        click.echo(
            format_failures(
                case_id=case_id,
                response=group.response,
                failures=group.failures,
                curl=reproduce,
                formatter=failure_formatter,
                config=config,
            )
        )
        click.echo()


def display_failures(statistic: Statistic, config: OutputConfig, *, record_crashes: bool) -> None:
    """Display all failures in the test run."""
    if not statistic.failures:
        return
    display_section_name("FAILURES")
    for label in sorted(statistic.failures):
        display_failures_for_single_test(
            config, label, statistic.failures[label].values(), record_crashes=record_crashes
        )


def display_api_operations(operations: OperationsSummary) -> None:
    click.echo(_style("API Operations:", bold=True))
    click.echo(
        _style(
            f"  Selected: {click.style(str(operations.selected), bold=True)}"
            f"/{click.style(str(operations.total), bold=True)}"
        )
    )
    click.echo(_style(f"  Tested: {click.style(str(operations.tested), bold=True)}"))
    if operations.errored:
        click.echo(_style(f"  Errored: {click.style(str(operations.errored), bold=True)}"))
    if operations.skipped:
        click.echo(_style(f"  Skipped: {click.style(str(operations.skipped), bold=True)}"))
        for reason in sorted(set(operations.skip_reasons)):
            click.echo(_style(f"    - {reason.rstrip('.')}"))
    click.echo()


def display_failures_summary(failures: list[FailureGroup]) -> None:
    click.echo(_style("Failures:", bold=True))
    for group in failures:
        click.echo(_style(f"  ❌ {group.title}: "), nl=False)
        click.echo(_style(str(group.count), bold=True))
    click.echo()


def display_test_cases(test_cases: TestCasesSummary) -> None:
    if test_cases.generated == 0:
        click.echo(_style("Test cases:", bold=True))
        click.echo("  No test cases were generated\n")
        return

    click.echo(_style("Test cases:", bold=True))
    parts = [f"  {click.style(str(test_cases.generated), bold=True)} generated"]

    if test_cases.without_checks == test_cases.generated:
        parts.append(f"{click.style(str(test_cases.without_checks), bold=True)} skipped")
    else:
        if test_cases.unique_failures > 0:
            parts.append(
                f"{click.style(str(test_cases.with_failures), bold=True)} found "
                f"{click.style(str(test_cases.unique_failures), bold=True)} unique failures"
            )
        else:
            parts.append(f"{click.style(str(test_cases.generated), bold=True)} passed")
        if test_cases.without_checks > 0:
            parts.append(f"{click.style(str(test_cases.without_checks), bold=True)} skipped")

    click.echo(_style(", ".join(parts) + "\n"))


def display_errors_summary(errors: list[ErrorGroup]) -> None:
    click.echo(_style("Errors:", bold=True))
    for group in errors:
        click.echo(_style(f"  🚫 {group.title}: "), nl=False)
        click.echo(_style(str(group.count), bold=True))
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
