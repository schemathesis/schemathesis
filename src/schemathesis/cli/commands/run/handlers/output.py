from __future__ import annotations

import os
import textwrap
import time
from collections.abc import Callable, Generator, Iterable
from dataclasses import dataclass, field
from itertools import groupby
from json.decoder import JSONDecodeError
from types import GeneratorType
from typing import TYPE_CHECKING, Any

import click

from schemathesis.cli.commands.run.context import ExecutionContext, GroupedFailures
from schemathesis.cli.commands.run.events import LoadingFinished, LoadingStarted
from schemathesis.cli.commands.run.handlers.base import EventHandler
from schemathesis.cli.constants import ISSUE_TRACKER_URL
from schemathesis.cli.core import get_terminal_width
from schemathesis.config import ProjectConfig, ReportFormat, SchemathesisWarning
from schemathesis.core.errors import LoaderError, LoaderErrorKind, format_exception, split_traceback
from schemathesis.core.failures import MessageBlock, Severity, format_failures
from schemathesis.core.output import prepare_response_payload
from schemathesis.core.parameters import ParameterLocation
from schemathesis.core.result import Ok
from schemathesis.core.version import SCHEMATHESIS_VERSION
from schemathesis.engine import Status, events
from schemathesis.engine.phases import PhaseName, PhaseSkipReason
from schemathesis.engine.phases.probes import ProbeOutcome
from schemathesis.engine.recorder import Interaction, ScenarioRecorder
from schemathesis.generation.meta import CoveragePhaseData, CoverageScenario
from schemathesis.generation.modes import GenerationMode
from schemathesis.schemas import ApiStatistic

if TYPE_CHECKING:
    from rich.console import Console, Group
    from rich.live import Live
    from rich.progress import Progress, TaskID
    from rich.text import Text

    from schemathesis.generation.stateful.state_machine import ExtractionFailure

IO_ENCODING = os.getenv("PYTHONIOENCODING", "utf-8")
DISCORD_LINK = "https://discord.gg/R9ASRAmHnA"


def display_section_name(title: str, separator: str = "=", **kwargs: Any) -> None:
    """Print section name with separators in terminal with the given title nicely centered."""
    message = f" {title} ".center(get_terminal_width(), separator)
    kwargs.setdefault("bold", True)
    click.echo(_style(message, **kwargs))


def bold(option: str) -> str:
    return click.style(option, bold=True)


def display_failures(ctx: ExecutionContext) -> None:
    """Display all failures in the test run."""
    if not ctx.statistic.failures:
        return

    display_section_name("FAILURES")
    for label, failures in ctx.statistic.failures.items():
        display_failures_for_single_test(ctx, label, failures.values())


if IO_ENCODING != "utf-8":
    HEADER_SEPARATOR = "-"

    def _style(text: str, **kwargs: Any) -> str:
        text = text.encode(IO_ENCODING, errors="replace").decode("utf-8")
        return click.style(text, **kwargs)

else:
    HEADER_SEPARATOR = "━"

    def _style(text: str, **kwargs: Any) -> str:
        return click.style(text, **kwargs)


def failure_formatter(block: MessageBlock, content: str) -> str:
    if block == MessageBlock.CASE_ID:
        return _style(content, bold=True)
    if block == MessageBlock.FAILURE:
        return _style(content, fg="red", bold=True)
    if block == MessageBlock.STATUS:
        return _style(content, bold=True)
    assert block == MessageBlock.CURL
    return _style(content.replace("Reproduce with", bold("Reproduce with")))


def display_failures_for_single_test(ctx: ExecutionContext, label: str, checks: Iterable[GroupedFailures]) -> None:
    """Display a failure for a single method / path."""
    display_section_name(label, "_", fg="red")
    for idx, group in enumerate(checks, 1):
        click.echo(
            format_failures(
                case_id=f"{idx}. Test Case ID: {group.case_id}",
                response=group.response,
                failures=group.failures,
                curl=group.code_sample,
                formatter=failure_formatter,
                config=ctx.config.output,
            )
        )
        click.echo()


VERIFY_URL_SUGGESTION = "Verify that the URL points directly to the Open API schema or GraphQL endpoint"
DISABLE_SSL_SUGGESTION = f"Bypass SSL verification with {bold('`--tls-verify=false`')}."
LOADER_ERROR_SUGGESTIONS = {
    # SSL-specific connection issue
    LoaderErrorKind.CONNECTION_SSL: DISABLE_SSL_SUGGESTION,
    # Other connection problems
    LoaderErrorKind.CONNECTION_OTHER: f"Use {bold('`--wait-for-schema=NUM`')} to wait up to NUM seconds for schema availability.",
    # Response issues
    LoaderErrorKind.UNEXPECTED_CONTENT_TYPE: VERIFY_URL_SUGGESTION,
    LoaderErrorKind.HTTP_FORBIDDEN: "Verify your API keys or authentication headers.",
    LoaderErrorKind.HTTP_NOT_FOUND: VERIFY_URL_SUGGESTION,
    # OpenAPI specification issues
    LoaderErrorKind.OPEN_API_UNSPECIFIED_VERSION: "Include the version in the schema.",
    # YAML specific issues
    LoaderErrorKind.YAML_NUMERIC_STATUS_CODES: "Convert numeric status codes to strings.",
    LoaderErrorKind.YAML_NON_STRING_KEYS: "Convert non-string keys to strings.",
    # Unclassified
    LoaderErrorKind.UNCLASSIFIED: f"If you suspect this is a Schemathesis issue and the schema is valid, please report it and include the schema if you can:\n\n  {ISSUE_TRACKER_URL}",
}


def _display_extras(extras: list[str]) -> None:
    if extras:
        click.echo()
    for extra in extras:
        click.echo(_style(f"    {extra}"))


def display_header(version: str) -> None:
    prefix = "v" if version != "dev" else ""
    header = f"Schemathesis {prefix}{version}"
    click.echo(_style(header, bold=True))
    click.echo(_style(HEADER_SEPARATOR * len(header), bold=True))
    click.echo()


DEFAULT_INTERNAL_ERROR_MESSAGE = "An internal error occurred during the test run"
TRUNCATION_PLACEHOLDER = "[...]"


def _print_lines(lines: list[str | Generator[str, None, None]]) -> None:
    for entry in lines:
        if isinstance(entry, str):
            click.echo(entry)
        elif isinstance(entry, GeneratorType):
            for line in entry:
                click.echo(line)


def _default_console() -> Console:
    from rich.console import Console

    kwargs = {}
    # For stdout recording in tests
    if "PYTEST_VERSION" in os.environ:
        kwargs["width"] = 240
    return Console(**kwargs)


BLOCK_PADDING = (0, 1, 0, 1)


@dataclass
class LoadingProgressManager:
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

        # Show what was attempted
        attempted = Text.assemble(
            ("❌  ", Style(color="red")),
            ("Failed to load specification from ", Style(color="white")),
            (self.location, Style(color="cyan")),
            (f" after {duration}", Style(color="white")),
        )

        # Show error details
        error_title = Text("Schema Loading Error", style=Style(color="red", bold=True))
        error_message = Text(error.message)

        return Group(
            attempted,
            Text(),
            error_title,
            Text(),
            error_message,
        )


@dataclass
class ProbingProgressManager:
    console: Console
    start_time: float
    progress: Progress
    progress_task_id: TaskID | None
    is_interrupted: bool

    __slots__ = ("console", "start_time", "progress", "progress_task_id", "is_interrupted")

    def __init__(self, console: Console) -> None:
        from rich.progress import Progress, RenderableColumn, SpinnerColumn, TextColumn
        from rich.text import Text

        self.console = console
        self.start_time = time.monotonic()
        self.progress = Progress(
            TextColumn(""),
            SpinnerColumn("clock"),
            RenderableColumn(Text("Probing API capabilities", style="bright_white")),
            transient=True,
            console=console,
        )
        self.progress_task_id = None
        self.is_interrupted = False

    def start(self) -> None:
        """Start probing progress display."""
        self.progress_task_id = self.progress.add_task("Probing", total=None)
        self.progress.start()

    def stop(self) -> None:
        """Stop probing progress display."""
        assert self.progress_task_id is not None
        self.progress.stop_task(self.progress_task_id)
        self.progress.stop()

    def interrupt(self) -> None:
        """Handle interruption during probing."""
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
                (f"API probing interrupted after {duration}", Style(color="white")),
            )
        return Text.assemble(
            ("✅  ", Style(color="green")),
            ("API capabilities:", Style(color="white")),
        )


@dataclass
class WarningData:
    missing_auth: dict[int, set[str]]
    missing_test_data: set[str]
    validation_mismatch: set[str]
    missing_deserializer: dict[str, set[str]]
    unused_openapi_auth: set[str]
    unsupported_regex: dict[str, set[str]]

    __slots__ = (
        "missing_auth",
        "missing_test_data",
        "validation_mismatch",
        "missing_deserializer",
        "unused_openapi_auth",
        "unsupported_regex",
    )

    def __init__(
        self,
        missing_auth: dict[int, set[str]] | None = None,
        missing_test_data: set[str] | None = None,
        validation_mismatch: set[str] | None = None,
        missing_deserializer: dict[str, set[str]] | None = None,
        unused_openapi_auth: set[str] | None = None,
        unsupported_regex: dict[str, set[str]] | None = None,
    ) -> None:
        self.missing_auth = missing_auth or {}
        self.missing_test_data = missing_test_data or set()
        self.validation_mismatch = validation_mismatch or set()
        self.missing_deserializer = missing_deserializer or {}
        self.unused_openapi_auth = unused_openapi_auth or set()
        self.unsupported_regex = unsupported_regex or {}

    @property
    def is_empty(self) -> bool:
        return not bool(
            self.missing_auth
            or self.missing_test_data
            or self.validation_mismatch
            or self.missing_deserializer
            or self.unused_openapi_auth
            or self.unsupported_regex
        )


@dataclass
class OperationProgress:
    """Tracks individual operation progress."""

    label: str
    start_time: float
    task_id: TaskID

    __slots__ = ("label", "start_time", "task_id")


@dataclass
class UnitTestProgressManager:
    """Manages progress display for unit tests."""

    console: Console
    title: str
    current: int
    total: int
    start_time: float

    # Progress components
    title_progress: Progress
    progress_bar: Progress
    operations_progress: Progress
    current_operations: dict[str, OperationProgress]
    stats: dict[Status, int]
    stats_progress: Progress
    live: Live | None

    # Task IDs
    title_task_id: TaskID | None
    progress_task_id: TaskID | None
    stats_task_id: TaskID

    is_interrupted: bool

    __slots__ = (
        "console",
        "title",
        "current",
        "total",
        "start_time",
        "title_progress",
        "progress_bar",
        "operations_progress",
        "current_operations",
        "stats",
        "stats_progress",
        "live",
        "title_task_id",
        "progress_task_id",
        "stats_task_id",
        "is_interrupted",
    )

    def __init__(
        self,
        *,
        console: Console,
        title: str,
        total: int,
    ) -> None:
        from rich.progress import (
            BarColumn,
            Progress,
            SpinnerColumn,
            TextColumn,
            TimeElapsedColumn,
        )
        from rich.style import Style

        self.console = console
        self.title = title
        self.current = 0
        self.total = total
        self.start_time = time.monotonic()

        # Initialize progress displays
        self.title_progress = Progress(
            TextColumn(""),
            SpinnerColumn("clock"),
            TextColumn("{task.description}", style=Style(color="white")),
            console=self.console,
        )
        self.title_task_id = None

        self.progress_bar = Progress(
            TextColumn("    "),
            TimeElapsedColumn(),
            BarColumn(bar_width=None),
            TextColumn("{task.percentage:.0f}% ({task.completed}/{task.total})"),
            console=self.console,
        )
        self.progress_task_id = None

        self.operations_progress = Progress(
            TextColumn("  "),
            SpinnerColumn("dots"),
            TimeElapsedColumn(),
            TextColumn(" {task.fields[label]}"),
            console=self.console,
        )

        self.current_operations = {}

        self.stats_progress = Progress(
            TextColumn("    "),
            TextColumn("{task.description}"),
            console=self.console,
        )
        self.stats_task_id = self.stats_progress.add_task("")
        self.stats = {
            Status.SUCCESS: 0,
            Status.FAILURE: 0,
            Status.SKIP: 0,
            Status.ERROR: 0,
            Status.INTERRUPTED: 0,
        }
        self._update_stats_display()

        self.live = None
        self.is_interrupted = False

    def _get_stats_message(self) -> str:
        width = len(str(self.total))

        parts = []
        if self.stats[Status.SUCCESS]:
            parts.append(f"✅ {self.stats[Status.SUCCESS]:{width}d} passed")
        if self.stats[Status.FAILURE]:
            parts.append(f"❌ {self.stats[Status.FAILURE]:{width}d} failed")
        if self.stats[Status.ERROR]:
            suffix = "s" if self.stats[Status.ERROR] > 1 else ""
            parts.append(f"🚫 {self.stats[Status.ERROR]:{width}d} error{suffix}")
        if self.stats[Status.SKIP] or self.stats[Status.INTERRUPTED]:
            parts.append(f"⏭  {self.stats[Status.SKIP] + self.stats[Status.INTERRUPTED]:{width}d} skipped")
        return "  ".join(parts)

    def _update_stats_display(self) -> None:
        """Update the statistics display."""
        self.stats_progress.update(self.stats_task_id, description=self._get_stats_message())

    def start(self) -> None:
        """Start progress display."""
        from rich.console import Group
        from rich.live import Live
        from rich.text import Text

        group = Group(
            self.title_progress,
            Text(),
            self.progress_bar,
            Text(),
            self.operations_progress,
            Text(),
            self.stats_progress,
        )

        self.live = Live(group, refresh_per_second=10, console=self.console, transient=True)
        self.live.start()

        # Initialize both progress displays
        self.title_task_id = self.title_progress.add_task(self.title, total=self.total)
        self.progress_task_id = self.progress_bar.add_task(
            "",  # Empty description as it's shown in title
            total=self.total,
        )

    def update_progress(self) -> None:
        """Update progress in both displays."""
        assert self.title_task_id is not None
        assert self.progress_task_id is not None

        self.current += 1
        self.title_progress.update(self.title_task_id, completed=self.current)
        self.progress_bar.update(self.progress_task_id, completed=self.current)

    def start_operation(self, label: str) -> None:
        """Start tracking new operation."""
        task_id = self.operations_progress.add_task("", label=label, start_time=time.monotonic())
        self.current_operations[label] = OperationProgress(label=label, start_time=time.monotonic(), task_id=task_id)

    def finish_operation(self, label: str) -> None:
        """Finish tracking operation."""
        if operation := self.current_operations.pop(label, None):
            if not self.current_operations:
                assert self.title_task_id is not None
                if self.current == self.total - 1:
                    description = f"  {self.title}"
                else:
                    description = self.title
                self.title_progress.update(self.title_task_id, description=description)
            self.operations_progress.update(operation.task_id, visible=False)

    def update_stats(self, status: Status) -> None:
        """Update statistics for a finished scenario."""
        self.stats[status] += 1
        self._update_stats_display()

    def interrupt(self) -> None:
        self.is_interrupted = True
        self.stats[Status.SKIP] += self.total - self.current
        if self.live:
            self.stop()

    def stop(self) -> None:
        """Stop all progress displays."""
        if self.live:
            self.live.stop()

    def _get_status_icon(self, default_icon: str = "🕛") -> str:
        if self.is_interrupted:
            icon = "⚡"
        elif self.stats[Status.ERROR] > 0:
            icon = "🚫"
        elif self.stats[Status.FAILURE] > 0:
            icon = "❌"
        elif self.stats[Status.SUCCESS] > 0:
            icon = "✅"
        elif self.stats[Status.SKIP] > 0:
            icon = "⏭ "
        else:
            icon = default_icon
        return icon

    def get_completion_message(self, default_icon: str = "🕛") -> str:
        """Complete the phase and return status message."""
        duration = format_duration(int((time.monotonic() - self.start_time) * 1000))
        icon = self._get_status_icon(default_icon)

        message = self._get_stats_message() or "No tests were run"
        if self.is_interrupted:
            duration_message = f"interrupted after {duration}"
        else:
            duration_message = f"in {duration}"

        return f"{icon}  {self.title} ({duration_message})\n\n    {message}"


@dataclass
class StatefulProgressManager:
    """Manages progress display for stateful testing."""

    console: Console
    title: str
    links_selected: int
    links_inferred: int
    links_total: int
    start_time: float

    # Progress components
    title_progress: Progress
    progress_bar: Progress
    stats_progress: Progress
    live: Live | None

    # Task IDs
    title_task_id: TaskID | None
    progress_task_id: TaskID | None
    stats_task_id: TaskID

    # State
    scenarios: int
    links_covered: set[str]
    stats: dict[Status, int]
    is_interrupted: bool

    __slots__ = (
        "console",
        "title",
        "links_selected",
        "links_inferred",
        "links_total",
        "start_time",
        "title_progress",
        "progress_bar",
        "stats_progress",
        "live",
        "title_task_id",
        "progress_task_id",
        "stats_task_id",
        "scenarios",
        "links_covered",
        "stats",
        "is_interrupted",
    )

    def __init__(
        self, *, console: Console, title: str, links_selected: int, links_inferred: int, links_total: int
    ) -> None:
        from rich.progress import Progress, SpinnerColumn, TextColumn, TimeElapsedColumn
        from rich.style import Style

        self.console = console
        self.title = title
        self.links_selected = links_selected
        self.links_inferred = links_inferred
        self.links_total = links_total
        self.start_time = time.monotonic()

        self.title_progress = Progress(
            TextColumn(""),
            SpinnerColumn("clock"),
            TextColumn("{task.description}", style=Style(color="bright_white")),
            console=self.console,
        )
        self.title_task_id = None

        self.progress_bar = Progress(
            TextColumn("    "),
            TimeElapsedColumn(),
            TextColumn("{task.fields[scenarios]:3d} scenarios  •  {task.fields[links]}"),
            console=self.console,
        )
        self.progress_task_id = None

        # Initialize stats progress
        self.stats_progress = Progress(
            TextColumn("    "),
            TextColumn("{task.description}"),
            console=self.console,
        )
        self.stats_task_id = self.stats_progress.add_task("")

        self.live = None

        # Initialize state
        self.scenarios = 0
        self.links_covered = set()
        self.stats = {
            Status.SUCCESS: 0,
            Status.FAILURE: 0,
            Status.ERROR: 0,
            Status.SKIP: 0,
        }
        self.is_interrupted = False

    def start(self) -> None:
        """Start progress display."""
        from rich.console import Group
        from rich.live import Live
        from rich.text import Text

        # Initialize progress displays
        self.title_task_id = self.title_progress.add_task("Stateful")
        links = f"0 covered / {self.links_selected} selected / {self.links_total} total"
        if self.links_inferred:
            links += f" ({self.links_inferred} inferred)"
        self.progress_task_id = self.progress_bar.add_task("", scenarios=0, links=links)

        # Create live display
        group = Group(
            self.title_progress,
            Text(),
            self.progress_bar,
            Text(),
            self.stats_progress,
        )
        self.live = Live(group, refresh_per_second=10, console=self.console, transient=True)
        self.live.start()

    def stop(self) -> None:
        """Stop progress display."""
        if self.live:
            self.live.stop()

    def update(self, links_covered: set[str], status: Status | None = None) -> None:
        """Update progress and stats."""
        self.scenarios += 1
        self.links_covered.update(links_covered)

        if status is not None:
            self.stats[status] += 1

        self._update_progress_display()
        self._update_stats_display()

    def _update_progress_display(self) -> None:
        """Update the progress display."""
        assert self.progress_task_id is not None
        links = f"{len(self.links_covered)} covered / {self.links_selected} selected / {self.links_total} total"
        if self.links_inferred:
            links += f" ({self.links_inferred} inferred)"
        self.progress_bar.update(self.progress_task_id, scenarios=self.scenarios, links=links)

    def _get_stats_message(self) -> str:
        """Get formatted stats message."""
        parts = []
        if self.stats[Status.SUCCESS]:
            parts.append(f"✅ {self.stats[Status.SUCCESS]} passed")
        if self.stats[Status.FAILURE]:
            parts.append(f"❌ {self.stats[Status.FAILURE]} failed")
        if self.stats[Status.ERROR]:
            suffix = "s" if self.stats[Status.ERROR] > 1 else ""
            parts.append(f"🚫 {self.stats[Status.ERROR]} error{suffix}")
        if self.stats[Status.SKIP]:
            parts.append(f"⏭  {self.stats[Status.SKIP]} skipped")
        return "  ".join(parts)

    def _update_stats_display(self) -> None:
        """Update the statistics display."""
        self.stats_progress.update(self.stats_task_id, description=self._get_stats_message())

    def _get_status_icon(self, default_icon: str = "🕛") -> str:
        if self.is_interrupted:
            icon = "⚡"
        elif self.stats[Status.ERROR] > 0:
            icon = "🚫"
        elif self.stats[Status.FAILURE] > 0:
            icon = "❌"
        elif self.stats[Status.SUCCESS] > 0:
            icon = "✅"
        elif self.stats[Status.SKIP] > 0:
            icon = "⏭ "
        else:
            icon = default_icon
        return icon

    def interrupt(self) -> None:
        """Handle interruption."""
        self.is_interrupted = True
        if self.live:
            self.stop()

    def get_completion_message(self, icon: str | None = None) -> tuple[str, str]:
        """Complete the phase and return status message."""
        duration = format_duration(int((time.monotonic() - self.start_time) * 1000))
        icon = icon or self._get_status_icon()

        message = self._get_stats_message() or "No tests were run"
        if self.is_interrupted:
            duration_message = f"interrupted after {duration}"
        else:
            duration_message = f"in {duration}"

        return f"{icon}  {self.title} ({duration_message})", message


def format_duration(duration_ms: int) -> str:
    """Format duration in milliseconds to seconds with 2 decimal places."""
    return f"{duration_ms / 1000:.2f}s"


@dataclass
class OutputHandler(EventHandler):
    config: ProjectConfig

    loading_manager: LoadingProgressManager | None = None
    probing_manager: ProbingProgressManager | None = None
    unit_tests_manager: UnitTestProgressManager | None = None
    stateful_tests_manager: StatefulProgressManager | None = None

    statistic: ApiStatistic | None = None
    skip_reasons: list[str] = field(default_factory=list)
    warnings: WarningData = field(default_factory=WarningData)
    errors: set[events.NonFatalError] = field(default_factory=set)
    phases: dict[PhaseName, tuple[Status, PhaseSkipReason | None]] = field(
        default_factory=lambda: dict.fromkeys(PhaseName, (Status.SKIP, None))
    )
    console: Console = field(default_factory=_default_console)

    def handle_event(self, ctx: ExecutionContext, event: events.EngineEvent) -> None:
        if isinstance(event, events.PhaseStarted):
            self._on_phase_started(event)
        elif isinstance(event, events.PhaseFinished):
            self._on_phase_finished(event)
        elif isinstance(event, events.ScenarioStarted):
            self._on_scenario_started(event)
        elif isinstance(event, events.ScenarioFinished):
            self._on_scenario_finished(ctx, event)
        elif isinstance(event, events.SchemaAnalysisWarnings):
            self._on_schema_warnings(ctx, event)
        if isinstance(event, events.EngineFinished):
            self._on_engine_finished(ctx, event)
        elif isinstance(event, events.Interrupted):
            self._on_interrupted(event)
        elif isinstance(event, events.FatalError):
            self._on_fatal_error(ctx, event)
        elif isinstance(event, events.NonFatalError):
            self.errors.add(event)
        elif isinstance(event, LoadingStarted):
            self._on_loading_started(event)
        elif isinstance(event, LoadingFinished):
            self._on_loading_finished(ctx, event)

    def start(self, ctx: ExecutionContext) -> None:
        display_header(SCHEMATHESIS_VERSION)

    def shutdown(self, ctx: ExecutionContext) -> None:
        if self.unit_tests_manager is not None:
            self.unit_tests_manager.stop()
        if self.stateful_tests_manager is not None:
            self.stateful_tests_manager.stop()
        if self.loading_manager is not None:
            self.loading_manager.stop()
        if self.probing_manager is not None:
            self.probing_manager.stop()

    def _on_loading_started(self, event: LoadingStarted) -> None:
        self.loading_manager = LoadingProgressManager(console=self.console, location=event.location)
        self.loading_manager.start()

    def _on_loading_finished(self, ctx: ExecutionContext, event: LoadingFinished) -> None:
        from rich.padding import Padding
        from rich.style import Style
        from rich.table import Table

        self.config = event.config

        assert self.loading_manager is not None
        self.loading_manager.stop()

        message = Padding(
            self.loading_manager.get_completion_message(),
            BLOCK_PADDING,
        )
        self.console.print(message)
        self.console.print()
        self.loading_manager = None
        self.statistic = event.statistic

        table = Table(
            show_header=False,
            box=None,
            padding=(0, 4),
            collapse_padding=True,
        )
        table.add_column("Field", style=Style(color="bright_white", bold=True))
        table.add_column("Value", style="cyan")

        table.add_row("Base URL:", event.base_url)
        table.add_row("Specification:", event.specification.name)
        statistic = event.statistic.operations
        table.add_row("Operations:", f"{statistic.selected} selected / {statistic.total} total")
        if event.config.config_path:
            table.add_row("Configuration:", event.config.config_path)

        message = Padding(table, BLOCK_PADDING)
        self.console.print(message)
        self.console.print()

        if ctx.initialization_lines:
            _print_lines(ctx.initialization_lines)

    def _on_phase_started(self, event: events.PhaseStarted) -> None:
        phase = event.phase
        if phase.name == PhaseName.PROBING and phase.is_enabled:
            self._start_probing()
        elif phase.name in [PhaseName.EXAMPLES, PhaseName.COVERAGE, PhaseName.FUZZING] and phase.is_enabled:
            self._start_unit_tests(phase.name)
        elif phase.name == PhaseName.STATEFUL_TESTING and phase.is_enabled and phase.skip_reason is None:
            self._start_stateful_tests(event)

    def _start_probing(self) -> None:
        self.probing_manager = ProbingProgressManager(console=self.console)
        self.probing_manager.start()

    def _start_unit_tests(self, phase: PhaseName) -> None:
        assert self.statistic is not None
        assert self.unit_tests_manager is None
        self.unit_tests_manager = UnitTestProgressManager(
            console=self.console,
            title=phase.value,
            total=self.statistic.operations.selected,
        )
        self.unit_tests_manager.start()

    def _start_stateful_tests(self, event: events.PhaseStarted) -> None:
        assert self.statistic is not None
        assert event.payload is not None
        # Total number of links - original ones + inferred during tests
        links_selected = self.statistic.links.selected + event.payload.inferred_links
        links_total = self.statistic.links.total + event.payload.inferred_links
        self.stateful_tests_manager = StatefulProgressManager(
            console=self.console,
            title="Stateful",
            links_selected=links_selected,
            links_inferred=event.payload.inferred_links,
            links_total=links_total,
        )
        self.stateful_tests_manager.start()

    def _on_phase_finished(self, event: events.PhaseFinished) -> None:
        from rich.padding import Padding
        from rich.style import Style
        from rich.table import Table
        from rich.text import Text

        phase = event.phase
        self.phases[phase.name] = (event.status, phase.skip_reason)

        if phase.name == PhaseName.PROBING:
            assert self.probing_manager is not None
            self.probing_manager.stop()
            self.probing_manager = None

            if event.status == Status.SUCCESS:
                assert isinstance(event.payload, Ok)
                payload = event.payload.ok()
                self.console.print(
                    Padding(
                        Text.assemble(
                            ("✅  ", Style(color="green")),
                            ("API capabilities:", Style(color="bright_white")),
                        ),
                        BLOCK_PADDING,
                    )
                )
                self.console.print()

                table = Table(
                    show_header=False,
                    box=None,
                    padding=(0, 4),
                    collapse_padding=True,
                )
                table.add_column("Capability", style=Style(color="bright_white", bold=True))
                table.add_column("Status", style="cyan")
                for probe_run in payload.probes:
                    icon, style = {
                        ProbeOutcome.SUCCESS: ("✓", Style(color="green")),
                        ProbeOutcome.FAILURE: ("✘", Style(color="red")),
                        ProbeOutcome.SKIP: ("⊘", Style(color="yellow")),
                    }[probe_run.outcome]

                    table.add_row(f"{probe_run.probe.name}:", Text(icon, style=style))

                message = Padding(table, BLOCK_PADDING)
            else:
                assert event.status == Status.SKIP
                assert isinstance(event.payload, Ok)
                message = Padding(
                    Text.assemble(
                        ("⏭   ", ""),
                        ("API probing skipped", Style(color="yellow")),
                    ),
                    BLOCK_PADDING,
                )
            self.console.print(message)
            self.console.print()
        elif phase.name == PhaseName.STATEFUL_TESTING and phase.is_enabled and self.stateful_tests_manager is not None:
            self.stateful_tests_manager.stop()
            if event.status == Status.ERROR:
                title, summary = self.stateful_tests_manager.get_completion_message("🚫")
            else:
                title, summary = self.stateful_tests_manager.get_completion_message()

            self.console.print(Padding(Text(title, style="bright_white"), BLOCK_PADDING))

            table = Table(
                show_header=False,
                box=None,
                padding=(0, 4),
                collapse_padding=True,
            )
            table.add_column("Field", style=Style(color="bright_white", bold=True))
            table.add_column("Value", style="cyan")
            table.add_row("Scenarios:", f"{self.stateful_tests_manager.scenarios}")
            message = f"{len(self.stateful_tests_manager.links_covered)} covered / {self.stateful_tests_manager.links_selected} selected / {self.stateful_tests_manager.links_total} total"
            if self.stateful_tests_manager.links_inferred:
                message += f" ({self.stateful_tests_manager.links_inferred} inferred)"
            table.add_row("API Links:", message)

            self.console.print()
            self.console.print(Padding(table, BLOCK_PADDING))
            self.console.print()
            self.console.print(Padding(Text(summary, style="bright_white"), (0, 0, 0, 5)))
            self.console.print()
            self.stateful_tests_manager = None
        elif (
            phase.name in [PhaseName.EXAMPLES, PhaseName.COVERAGE, PhaseName.FUZZING]
            and phase.is_enabled
            and self.unit_tests_manager is not None
        ):
            self.unit_tests_manager.stop()
            if event.status == Status.ERROR:
                message = self.unit_tests_manager.get_completion_message("🚫")
            else:
                message = self.unit_tests_manager.get_completion_message()
            self.console.print(Padding(Text(message, style="white"), BLOCK_PADDING))
            self.console.print()
            self.unit_tests_manager = None

    def _on_scenario_started(self, event: events.ScenarioStarted) -> None:
        if event.phase in [PhaseName.EXAMPLES, PhaseName.COVERAGE, PhaseName.FUZZING]:
            # We should display execution result + percentage in the end. For example:
            assert event.label is not None
            assert self.unit_tests_manager is not None
            self.unit_tests_manager.start_operation(event.label)

    def _on_scenario_finished(self, ctx: ExecutionContext, event: events.ScenarioFinished) -> None:
        if event.phase in [PhaseName.EXAMPLES, PhaseName.COVERAGE, PhaseName.FUZZING]:
            assert self.unit_tests_manager is not None
            if event.label:
                self.unit_tests_manager.finish_operation(event.label)
            self.unit_tests_manager.update_progress()
            self.unit_tests_manager.update_stats(event.status)
            if event.status == Status.SKIP and event.skip_reason is not None:
                self.skip_reasons.append(event.skip_reason)
            self._check_warnings(ctx, event)
        elif (
            event.phase == PhaseName.STATEFUL_TESTING
            and not event.is_final
            and event.status not in (Status.INTERRUPTED, Status.SKIP, None)
        ):
            assert self.stateful_tests_manager is not None
            links_seen = {
                case.transition.id
                for case in event.recorder.cases.values()
                if case.transition is not None and case.is_transition_applied
            }
            self.stateful_tests_manager.update(links_seen, event.status)
            self._check_stateful_warnings(ctx, event)

    def _check_warnings(self, ctx: ExecutionContext, event: events.ScenarioFinished) -> None:
        from schemathesis.core.compat import RefResolutionError

        statistic = aggregate_status_codes(event.recorder.interactions.values())

        if statistic.total == 0:
            return

        assert ctx.find_operation_by_label is not None
        assert event.label is not None
        try:
            operation = ctx.find_operation_by_label(event.label)
        except RefResolutionError:
            # This error will be reported elsewhere anyway
            return None

        warnings = self.config.warnings_for(operation=operation)

        def has_only_missing_auth_case() -> bool:
            case = list(event.recorder.cases.values())[0].value
            return bool(
                case.meta
                and isinstance(case.meta.phase.data, CoveragePhaseData)
                and case.meta.phase.data.scenario == CoverageScenario.MISSING_PARAMETER
                and case.meta.phase.data.parameter == "Authorization"
                and case.meta.phase.data.parameter_location == ParameterLocation.HEADER
            )

        if warnings.should_display(SchemathesisWarning.MISSING_AUTH):
            if not (len(event.recorder.cases) == 1 and has_only_missing_auth_case()):
                for status_code in (401, 403):
                    if statistic.ratio_for(status_code) >= AUTH_ERRORS_THRESHOLD:
                        self.warnings.missing_auth.setdefault(status_code, set()).add(event.recorder.label)
                        # Check if this warning should cause test failure
                        if warnings.should_fail(SchemathesisWarning.MISSING_AUTH):
                            ctx.exit_code = 1

        # Warn if all positive test cases got 4xx in return and no failure was found
        def all_positive_are_rejected(recorder: ScenarioRecorder) -> bool:
            seen_positive = False
            for case in recorder.cases.values():
                if not (case.value.meta is not None and case.value.meta.generation.mode == GenerationMode.POSITIVE):
                    continue
                seen_positive = True
                interaction = recorder.interactions.get(case.value.id)
                if not (interaction is not None and interaction.response is not None):
                    continue
                # At least one positive response for positive test case
                if 200 <= interaction.response.status_code < 300:
                    return False
            # If there are positive test cases, and we ended up here, then there are no 2xx responses for them
            # Otherwise, there are no positive test cases at all and this check should pass
            return seen_positive

        if (
            event.status == Status.SUCCESS
            and (
                warnings.should_display(SchemathesisWarning.MISSING_TEST_DATA)
                or warnings.should_display(SchemathesisWarning.VALIDATION_MISMATCH)
            )
            and GenerationMode.POSITIVE in self.config.generation_for(operation=operation, phase=event.phase.name).modes
            and all_positive_are_rejected(event.recorder)
        ):
            if statistic.should_warn_about_missing_test_data():
                self._handle_warning(
                    ctx,
                    SchemathesisWarning.MISSING_TEST_DATA,
                    lambda: self.warnings.missing_test_data.add(event.recorder.label),
                )
            if statistic.should_warn_about_validation_mismatch():
                self._handle_warning(
                    ctx,
                    SchemathesisWarning.VALIDATION_MISMATCH,
                    lambda: self.warnings.validation_mismatch.add(event.recorder.label),
                )

    def _handle_warning(
        self, ctx: ExecutionContext, kind: SchemathesisWarning, record_callback: Callable[[], None]
    ) -> None:
        """Handle a warning by checking display/fail config and recording it."""
        if not self.config.warnings.should_display(kind):
            return
        record_callback()
        if self.config.warnings.should_fail(kind):
            ctx.exit_code = 1

    def _record_missing_deserializer_warning(self, operation_label: str, message: str) -> Callable[[], None]:
        """Create a callback that records a missing deserializer warning."""

        def record() -> None:
            self.warnings.missing_deserializer.setdefault(operation_label, set()).add(message)

        return record

    def _record_unused_openapi_auth_warning(self, message: str) -> Callable[[], None]:
        """Create a callback that records an unused OpenAPI auth warning."""

        def record() -> None:
            self.warnings.unused_openapi_auth.add(message)

        return record

    def _record_unsupported_regex_warning(self, operation_label: str, message: str) -> Callable[[], None]:
        """Create a callback that records an unsupported regex warning."""

        def record() -> None:
            self.warnings.unsupported_regex.setdefault(operation_label, set()).add(message)

        return record

    def _check_stateful_warnings(self, ctx: ExecutionContext, event: events.ScenarioFinished) -> None:
        # If stateful testing had successful responses for API operations that were marked with "missing_test_data"
        # warnings, then remove them from warnings
        for key, node in event.recorder.cases.items():
            if not self.warnings.missing_test_data:
                break
            if node.value.operation.label in self.warnings.missing_test_data and key in event.recorder.interactions:
                response = event.recorder.interactions[key].response
                if response is not None and response.status_code < 300:
                    self.warnings.missing_test_data.remove(node.value.operation.label)
                    continue

    def _on_schema_warnings(self, ctx: ExecutionContext, event: events.SchemaAnalysisWarnings) -> None:
        """Process schema-level warnings emitted outside of scenarios."""
        for warning in event.warnings:
            if warning.kind is SchemathesisWarning.MISSING_DESERIALIZER:
                # MissingDeserializerWarning always has operation_label
                assert warning.operation_label is not None
                self._handle_warning(
                    ctx,
                    warning.kind,
                    self._record_missing_deserializer_warning(warning.operation_label, warning.message),
                )
            elif warning.kind is SchemathesisWarning.UNUSED_OPENAPI_AUTH:
                # UnusedOpenAPIAuthWarning has no operation_label (schema-level)
                self._handle_warning(
                    ctx,
                    warning.kind,
                    self._record_unused_openapi_auth_warning(warning.message),
                )
            elif warning.kind is SchemathesisWarning.UNSUPPORTED_REGEX:
                assert warning.operation_label is not None
                self._handle_warning(
                    ctx,
                    warning.kind,
                    self._record_unsupported_regex_warning(warning.operation_label, warning.message),
                )

    def _on_interrupted(self, event: events.Interrupted) -> None:
        from rich.padding import Padding

        if self.unit_tests_manager is not None:
            self.unit_tests_manager.interrupt()
        elif self.stateful_tests_manager is not None:
            self.stateful_tests_manager.interrupt()
        elif self.loading_manager is not None:
            self.loading_manager.interrupt()
            message = Padding(
                self.loading_manager.get_completion_message(),
                BLOCK_PADDING,
            )
            self.console.print(message)
            self.console.print()
        elif self.probing_manager is not None:
            self.probing_manager.interrupt()
            message = Padding(
                self.probing_manager.get_completion_message(),
                BLOCK_PADDING,
            )
            self.console.print(message)
            self.console.print()
            self.probing_manager = None

    def _on_fatal_error(self, ctx: ExecutionContext, event: events.FatalError) -> None:
        from rich.padding import Padding
        from rich.text import Text

        self.shutdown(ctx)

        if isinstance(event.exception, LoaderError):
            assert self.loading_manager is not None
            message = Padding(self.loading_manager.get_error_message(event.exception), BLOCK_PADDING)
            self.console.print(message)
            self.console.print()
            self.loading_manager = None

            if event.exception.extras:
                for extra in event.exception.extras:
                    self.console.print(Padding(Text(extra), (0, 0, 0, 5)))
                self.console.print()

            if not (
                event.exception.kind == LoaderErrorKind.CONNECTION_OTHER and self.config.wait_for_schema is not None
            ):
                suggestion = LOADER_ERROR_SUGGESTIONS.get(event.exception.kind)
                if suggestion is not None:
                    click.echo(_style(f"{click.style('Tip:', bold=True, fg='green')} {suggestion}"))

            raise click.Abort
        title = "Test Execution Error"
        message = DEFAULT_INTERNAL_ERROR_MESSAGE
        traceback = format_exception(event.exception, with_traceback=True)
        extras = split_traceback(traceback)
        suggestion = f"Please consider reporting the traceback above to our issue tracker:\n\n  {ISSUE_TRACKER_URL}."
        click.echo(_style(title, fg="red", bold=True))
        click.echo()
        click.echo(message)
        _display_extras(extras)
        if not (
            isinstance(event.exception, LoaderError)
            and event.exception.kind == LoaderErrorKind.CONNECTION_OTHER
            and self.config.wait_for_schema is not None
        ):
            click.echo(_style(f"\n{click.style('Tip:', bold=True, fg='green')} {suggestion}"))

        raise click.Abort

    def _print_warning_header(self, title: str, count: int, entity_name: str, suffix_text: str) -> None:
        """Print warning block header."""
        plural = "" if count == 1 else "s"
        click.echo(_style(f"{title}: {count} {entity_name}{plural}{suffix_text}\n", fg="yellow"))

    def _print_warning_tips(self, tips: list[str]) -> None:
        """Print warning tips and footer."""
        click.echo()
        for tip in tips:
            click.echo(_style(tip, fg="yellow"))
        if tips:
            click.echo()

    def _print_items_truncated(self, items: set[str], max_items: int = 3) -> None:
        """Print up to max_items, then show +N more."""
        for item in sorted(items)[:max_items]:
            click.echo(_style(f"  - {item}", fg="yellow"))
        extra = len(items) - max_items
        if extra > 0:
            click.echo(_style(f"  + {extra} more", fg="yellow"))

    def _display_warning_block(
        self,
        title: str,
        operations: set[str] | dict[int, set[str]],
        tips: list[str],
        suffix_text: str = "",
        entity_name: str = "operation",
    ) -> None:
        """Display warnings for operations (simple list or grouped by status code)."""
        if isinstance(operations, dict):
            # Status code grouped: dict[int, set[str]]
            total = sum(len(ops) for ops in operations.values())
            self._print_warning_header(title, total, entity_name, suffix_text)

            for status_code, ops in operations.items():
                status_text = "Unauthorized" if status_code == 401 else "Forbidden"
                count = len(ops)
                plural = "" if count == 1 else "s"
                click.echo(_style(f"{status_code} {status_text} ({count} {entity_name}{plural}):", fg="yellow"))
                self._print_items_truncated(ops)
        else:
            # Simple set of operations
            self._print_warning_header(title, len(operations), entity_name, suffix_text)
            self._print_items_truncated(operations)

        self._print_warning_tips(tips)

    def _display_detailed_warning_block(
        self,
        title: str,
        warnings: dict[str, set[str]],
        entity_name: str,
        suffix_text: str,
        tips: list[str],
        show_entity_label: bool = True,
    ) -> None:
        """Display warnings with detailed messages per entity."""
        self._print_warning_header(title, len(warnings), entity_name, suffix_text)

        # Show up to 3 entities with their messages
        for idx, (entity_label, messages) in enumerate(sorted(warnings.items())[:3]):
            if show_entity_label:
                click.echo(_style(f"  - {entity_label}", fg="yellow"))
                for message in sorted(messages):
                    click.echo(_style(f"    {message}", fg="yellow"))
            else:
                for message in sorted(messages):
                    click.echo(_style(f"  {message}", fg="yellow"))

            # Add spacing between entities (but not after the last one)
            if idx < min(len(warnings), 3) - 1:
                click.echo()

        extra = len(warnings) - 3
        if extra > 0:
            click.echo(_style(f"  + {extra} more", fg="yellow"))

        self._print_warning_tips(tips)

    def display_warnings(self) -> None:
        display_section_name("WARNINGS")
        click.echo()
        if self.warnings.missing_auth:
            self._display_warning_block(
                title="Authentication failed",
                operations=self.warnings.missing_auth,
                suffix_text=" returned authentication errors",
                tips=["💡 Ensure valid authentication credentials are set via --auth or -H"],
            )

        if self.warnings.missing_test_data:
            self._display_warning_block(
                title="Missing test data",
                operations=self.warnings.missing_test_data,
                suffix_text=" repeatedly returned 404 Not Found, preventing tests from reaching your API's core logic",
                tips=[
                    "💡 Provide realistic parameter values in your config file so tests can access existing resources",
                ],
            )

        if self.warnings.validation_mismatch:
            self._display_warning_block(
                title="Schema validation mismatch",
                operations=self.warnings.validation_mismatch,
                suffix_text=" mostly rejected generated data due to validation errors, indicating schema constraints don't match API validation",
                tips=["💡 Check your schema constraints - API validation may be stricter than documented"],
            )

        if self.warnings.missing_deserializer:
            self._display_detailed_warning_block(
                title="Schema validation skipped",
                warnings=self.warnings.missing_deserializer,
                entity_name="operation",
                suffix_text=" cannot validate responses due to missing deserializers",
                tips=["💡 Register a deserializer with @schemathesis.deserializer() to enable validation"],
            )

        if self.warnings.unused_openapi_auth:
            self._display_warning_block(
                title="Unused OpenAPI auth",
                operations=self.warnings.unused_openapi_auth,
                suffix_text=" not defined in the schema",
                tips=[],
                entity_name="configured auth scheme",
            )

        if self.warnings.unsupported_regex:
            self._display_detailed_warning_block(
                title="Unsupported regex patterns",
                warnings=self.warnings.unsupported_regex,
                entity_name="operation",
                suffix_text=" contain regex patterns not supported by Python and were removed",
                tips=["💡 Use Python-compatible regex syntax: https://docs.python.org/3/library/re.html"],
            )

    def display_stateful_failures(self, ctx: ExecutionContext) -> None:
        display_section_name("Stateful tests")

        click.echo("\nFailed to extract data from response:")

        grouped: dict[str, list[ExtractionFailure]] = {}
        for failure in ctx.statistic.extraction_failures:
            grouped.setdefault(failure.id, []).append(failure)

        for idx, (transition_id, failures) in enumerate(grouped.items(), 1):
            for failure in failures:
                click.echo(f"\n    {idx}. Test Case ID: {failure.case_id}\n")
                click.echo(f"    {transition_id}")

                indent = "        "
                if failure.error:
                    if isinstance(failure.error, JSONDecodeError):
                        click.echo(f"\n{indent}Failed to parse JSON from response")
                    else:
                        click.echo(f"\n{indent}{failure.error.__class__.__name__}: {failure.error}")
                else:
                    if failure.parameter_name == "body":
                        description = f"\n{indent}Could not resolve request body via {failure.expression}"
                    else:
                        description = f"\n{indent}Could not resolve parameter `{failure.parameter_name}` via `{failure.expression}`"
                    prefix = "$response.body"
                    if failure.expression.startswith(prefix):
                        description += f"\n{indent}Path `{failure.expression[len(prefix) :]}` not found in response"
                    click.echo(description)

                click.echo()

                for case, response in reversed(failure.history):
                    curl = case.as_curl_command(headers=dict(response.request.headers), verify=response.verify)
                    click.echo(f"{indent}[{response.status_code}] {curl}")

                response = failure.response

                if response.content is None or not response.content:
                    click.echo(f"\n{indent}<EMPTY>")
                else:
                    try:
                        payload = prepare_response_payload(response.text, config=ctx.config.output)
                        click.echo(textwrap.indent(f"\n{payload}", prefix=indent))
                    except UnicodeDecodeError:
                        click.echo(f"\n{indent}<BINARY>")

        click.echo()

    def display_api_operations(self, ctx: ExecutionContext) -> None:
        assert self.statistic is not None
        click.echo(_style("API Operations:", bold=True))
        click.echo(
            _style(
                f"  Selected: {click.style(str(self.statistic.operations.selected), bold=True)}/"
                f"{click.style(str(self.statistic.operations.total), bold=True)}"
            )
        )
        click.echo(_style(f"  Tested: {click.style(str(len(ctx.statistic.tested_operations)), bold=True)}"))
        errors = len(
            {
                err.label
                for err in self.errors
                # Some API operations may have some tests before they have an error
                if err.phase in [PhaseName.EXAMPLES, PhaseName.COVERAGE, PhaseName.FUZZING]
                and err.label not in ctx.statistic.tested_operations
                and err.related_to_operation
            }
        )
        if errors:
            click.echo(_style(f"  Errored: {click.style(str(errors), bold=True)}"))

        # API operations that are skipped due to fail-fast are counted here as well
        total_skips = self.statistic.operations.selected - len(ctx.statistic.tested_operations) - errors
        if total_skips:
            click.echo(_style(f"  Skipped: {click.style(str(total_skips), bold=True)}"))
            for reason in sorted(set(self.skip_reasons)):
                click.echo(_style(f"    - {reason.rstrip('.')}"))
        click.echo()

    def display_phases(self) -> None:
        click.echo(_style("Test Phases:", bold=True))

        for phase in PhaseName:
            if phase in (PhaseName.PROBING, PhaseName.SCHEMA_ANALYSIS):
                # Internal phases are not part of the test phase summary
                continue
            status, skip_reason = self.phases[phase]

            if status == Status.SKIP:
                click.echo(_style(f"  ⏭  {phase.value}", fg="yellow"), nl=False)
                if skip_reason:
                    click.echo(_style(f" ({skip_reason.value})", fg="yellow"))
                else:
                    click.echo()
            elif status == Status.SUCCESS:
                click.echo(_style(f"  ✅ {phase.value}", fg="green"))
            elif status == Status.FAILURE:
                click.echo(_style(f"  ❌ {phase.value}", fg="red"))
            elif status == Status.ERROR:
                click.echo(_style(f"  🚫 {phase.value}", fg="red"))
            elif status == Status.INTERRUPTED:
                click.echo(_style(f"  ⚡ {phase.value}", fg="yellow"))
        click.echo()

    def display_test_cases(self, ctx: ExecutionContext) -> None:
        if ctx.statistic.total_cases == 0:
            click.echo(_style("Test cases:", bold=True))
            click.echo("  No test cases were generated\n")
            return

        unique_failures = sum(
            len(group.failures) for grouped in ctx.statistic.failures.values() for group in grouped.values()
        )
        click.echo(_style("Test cases:", bold=True))

        parts = [f"  {click.style(str(ctx.statistic.total_cases), bold=True)} generated"]

        # Don't show pass/fail status if all cases were skipped
        if ctx.statistic.cases_without_checks == ctx.statistic.total_cases:
            parts.append(f"{click.style(str(ctx.statistic.cases_without_checks), bold=True)} skipped")
        else:
            if unique_failures > 0:
                parts.append(
                    f"{click.style(str(ctx.statistic.cases_with_failures), bold=True)} found "
                    f"{click.style(str(unique_failures), bold=True)} unique failures"
                )
            else:
                parts.append(f"{click.style(str(ctx.statistic.total_cases), bold=True)} passed")

            if ctx.statistic.cases_without_checks > 0:
                parts.append(f"{click.style(str(ctx.statistic.cases_without_checks), bold=True)} skipped")

        click.echo(_style(", ".join(parts) + "\n"))

    def display_failures_summary(self, ctx: ExecutionContext) -> None:
        # Collect all unique failures and their counts by title
        failure_counts: dict[str, tuple[Severity, int]] = {}
        for grouped in ctx.statistic.failures.values():
            for group in grouped.values():
                for failure in group.failures:
                    data = failure_counts.get(failure.title, (failure.severity, 0))
                    failure_counts[failure.title] = (failure.severity, data[1] + 1)

        click.echo(_style("Failures:", bold=True))

        # Sort by severity first, then by title
        sorted_failures = sorted(failure_counts.items(), key=lambda x: (x[1][0], x[0]))

        for title, (_, count) in sorted_failures:
            click.echo(_style(f"  ❌ {title}: "), nl=False)
            click.echo(_style(str(count), bold=True))
        click.echo()

    def display_errors_summary(self) -> None:
        # Group errors by title and count occurrences
        error_counts: dict[str, int] = {}
        for error in self.errors:
            title = error.info.title
            error_counts[title] = error_counts.get(title, 0) + 1

        click.echo(_style("Errors:", bold=True))

        for title in sorted(error_counts):
            click.echo(_style(f"  🚫 {title}: "), nl=False)
            click.echo(_style(str(error_counts[title]), bold=True))
        click.echo()

    def display_final_line(self, ctx: ExecutionContext, event: events.EngineFinished) -> None:
        parts = []

        unique_failures = sum(
            len(group.failures) for grouped in ctx.statistic.failures.values() for group in grouped.values()
        )
        if unique_failures:
            suffix = "s" if unique_failures > 1 else ""
            parts.append(f"{unique_failures} failure{suffix}")

        if self.errors:
            suffix = "s" if len(self.errors) > 1 else ""
            parts.append(f"{len(self.errors)} error{suffix}")

        total_warnings = sum(len(endpoints) for endpoints in self.warnings.missing_auth.values())
        if total_warnings:
            suffix = "s" if total_warnings > 1 else ""
            parts.append(f"{total_warnings} warning{suffix}")

        if parts:
            message = f"{', '.join(parts)} in {event.running_time:.2f}s"
            color = "red" if (unique_failures or self.errors) else "yellow"
        elif ctx.statistic.total_cases == 0:
            message = "Empty test suite"
            color = "yellow"
        else:
            message = f"No issues found in {event.running_time:.2f}s"
            color = "green"

        display_section_name(message, fg=color)

    def display_reports(self) -> None:
        reports = self.config.reports
        if reports.vcr.enabled or reports.har.enabled or reports.junit.enabled:
            click.echo(_style("Reports:", bold=True))
            for format, report in (
                (ReportFormat.JUNIT, reports.junit),
                (ReportFormat.VCR, reports.vcr),
                (ReportFormat.HAR, reports.har),
            ):
                if report.enabled:
                    path = reports.get_path(format)
                    click.echo(_style(f"  - {format.value.upper()}: {path}"))
            click.echo()

    def display_seed(self) -> None:
        click.echo(_style("Seed: ", bold=True), nl=False)
        # Deterministic mode can be applied to a subset of tests, but we only care if it is enabled everywhere
        # If not everywhere, then the seed matter and should be displayed
        if self.config.seed is None or self.config.generation.deterministic:
            click.echo("not used in the deterministic mode")
        else:
            click.echo(str(self.config.seed))
        click.echo()

    def _on_engine_finished(self, ctx: ExecutionContext, event: events.EngineFinished) -> None:
        assert self.loading_manager is None
        assert self.probing_manager is None
        assert self.unit_tests_manager is None
        assert self.stateful_tests_manager is None
        if self.errors:
            display_section_name("ERRORS")
            errors = sorted(self.errors, key=lambda r: (r.phase.value, r.label, r.info.title))
            for label, group_errors in groupby(errors, key=lambda r: r.label):
                display_section_name(label, "_", fg="red")
                _errors = list(group_errors)
                for idx, error in enumerate(_errors, 1):
                    click.echo(error.info.format(bold=lambda x: click.style(x, bold=True)))
                    if idx < len(_errors):
                        click.echo()
            click.echo(
                _style(
                    f"\nNeed more help?\n    Join our Discord server: {DISCORD_LINK}",
                    fg="red",
                )
            )
        display_failures(ctx)
        if not self.warnings.is_empty:
            self.display_warnings()
        if ctx.statistic.extraction_failures:
            self.display_stateful_failures(ctx)
        display_section_name("SUMMARY")
        click.echo()

        if self.statistic:
            self.display_api_operations(ctx)

        self.display_phases()

        if ctx.statistic.failures:
            self.display_failures_summary(ctx)

        if self.errors:
            self.display_errors_summary()

        if not self.warnings.is_empty:
            click.echo(_style("Warnings:", bold=True))

            if self.warnings.missing_auth:
                affected = sum(len(operations) for operations in self.warnings.missing_auth.values())
                suffix = "" if affected == 1 else "s"
                click.echo(
                    _style(
                        f"  ⚠️ Missing authentication: {bold(str(affected))} operation{suffix} returned only 401/403 responses",
                        fg="yellow",
                    )
                )

            if self.warnings.missing_test_data:
                count = len(self.warnings.missing_test_data)
                suffix = "" if count == 1 else "s"
                click.echo(
                    _style(
                        f"  ⚠️ Missing valid test data: {bold(str(count))} operation{suffix} repeatedly returned 404 responses",
                        fg="yellow",
                    )
                )

            if self.warnings.validation_mismatch:
                count = len(self.warnings.validation_mismatch)
                suffix = "" if count == 1 else "s"
                click.echo(
                    _style(
                        f"  ⚠️ Schema validation mismatch: {bold(str(count))} operation{suffix} mostly rejected generated data",
                        fg="yellow",
                    )
                )

            if self.warnings.missing_deserializer:
                count = len(self.warnings.missing_deserializer)
                suffix = "" if count == 1 else "s"
                click.echo(
                    _style(
                        f"  ⚠️ Schema validation skipped: {bold(str(count))} operation{suffix} cannot validate responses",
                        fg="yellow",
                    )
                )

            if self.warnings.unused_openapi_auth:
                count = len(self.warnings.unused_openapi_auth)
                suffix = "" if count == 1 else "s"
                click.echo(
                    _style(
                        f"  ⚠️ Unused OpenAPI auth: {bold(str(count))} configured auth scheme{suffix} not used in the schema",
                        fg="yellow",
                    )
                )

            if self.warnings.unsupported_regex:
                count = len(self.warnings.unsupported_regex)
                suffix = "" if count == 1 else "s"
                click.echo(
                    _style(
                        f"  ⚠️ Unsupported regex: {bold(str(count))} operation{suffix} had regex patterns removed",
                        fg="yellow",
                    )
                )

            click.echo()

        if ctx.summary_lines:
            _print_lines(ctx.summary_lines)
            click.echo()

        self.display_test_cases(ctx)
        self.display_reports()
        self.display_seed()
        self.display_final_line(ctx, event)


@dataclass
class StatusCodeStatistic:
    """Statistics about HTTP status codes in a scenario."""

    counts: dict[int, int]
    total: int

    __slots__ = ("counts", "total")

    def ratio_for(self, status_code: int) -> float:
        """Calculate the ratio of responses with the given status code."""
        if self.total == 0:
            return 0.0
        return self.counts.get(status_code, 0) / self.total

    def _get_4xx_breakdown(self) -> tuple[int, int, int]:
        """Get breakdown of 4xx responses: (404_count, other_4xx_count, total_4xx_count)."""
        count_404 = self.counts.get(404, 0)
        count_other_4xx = sum(
            count for code, count in self.counts.items() if 400 <= code < 500 and code not in {401, 403, 404}
        )
        total_4xx = count_404 + count_other_4xx
        return count_404, count_other_4xx, total_4xx

    def _is_only_4xx_responses(self) -> bool:
        """Check if all responses are 4xx (excluding 5xx)."""
        return all(400 <= code < 500 for code in self.counts.keys() if code not in {500})

    def _can_warn_about_4xx(self) -> bool:
        """Check basic conditions for 4xx warnings."""
        if self.total == 0:
            return False
        # Skip if only auth errors
        if set(self.counts.keys()) <= {401, 403, 500}:
            return False
        return self._is_only_4xx_responses()

    def should_warn_about_missing_test_data(self) -> bool:
        """Check if an operation should be warned about missing test data (significant 404 responses)."""
        if not self._can_warn_about_4xx():
            return False

        count_404, _, total_4xx = self._get_4xx_breakdown()

        if total_4xx == 0:
            return False

        return (count_404 / total_4xx) >= OTHER_CLIENT_ERRORS_THRESHOLD

    def should_warn_about_validation_mismatch(self) -> bool:
        """Check if an operation should be warned about validation mismatch (significant 400/422 responses)."""
        if not self._can_warn_about_4xx():
            return False

        _, count_other_4xx, total_4xx = self._get_4xx_breakdown()

        if total_4xx == 0:
            return False

        return (count_other_4xx / total_4xx) >= OTHER_CLIENT_ERRORS_THRESHOLD


AUTH_ERRORS_THRESHOLD = 0.9
OTHER_CLIENT_ERRORS_THRESHOLD = 0.1


def aggregate_status_codes(interactions: Iterable[Interaction]) -> StatusCodeStatistic:
    """Analyze status codes from interactions."""
    counts: dict[int, int] = {}
    total = 0

    for interaction in interactions:
        if interaction.response is not None:
            status = interaction.response.status_code
            counts[status] = counts.get(status, 0) + 1
            total += 1

    return StatusCodeStatistic(counts=counts, total=total)
