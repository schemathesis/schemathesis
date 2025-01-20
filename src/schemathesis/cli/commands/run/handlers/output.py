from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from types import GeneratorType
from typing import TYPE_CHECKING, Any, Generator, Iterable

import click

from schemathesis.cli.commands.run.context import ExecutionContext, GroupedFailures
from schemathesis.cli.commands.run.events import LoadingFinished, LoadingStarted
from schemathesis.cli.commands.run.handlers.base import EventHandler
from schemathesis.cli.commands.run.handlers.cassettes import CassetteConfig
from schemathesis.cli.constants import ISSUE_TRACKER_URL
from schemathesis.cli.core import get_terminal_width
from schemathesis.core.errors import LoaderError, LoaderErrorKind, format_exception, split_traceback
from schemathesis.core.failures import MessageBlock, Severity, format_failures
from schemathesis.core.result import Err, Ok
from schemathesis.core.version import SCHEMATHESIS_VERSION
from schemathesis.engine import Status, events
from schemathesis.engine.errors import EngineErrorInfo
from schemathesis.engine.phases import PhaseName, PhaseSkipReason
from schemathesis.engine.phases.probes import ProbeOutcome
from schemathesis.engine.recorder import Interaction
from schemathesis.experimental import GLOBAL_EXPERIMENTS
from schemathesis.schemas import ApiStatistic

if TYPE_CHECKING:
    from rich.console import Console, Group
    from rich.live import Live
    from rich.progress import Progress, TaskID
    from rich.text import Text

IO_ENCODING = os.getenv("PYTHONIOENCODING", "utf-8")
DISCORD_LINK = "https://discord.gg/R9ASRAmHnA"


def display_section_name(title: str, separator: str = "=", **kwargs: Any) -> None:
    """Print section name with separators in terminal with the given title nicely centered."""
    message = f" {title} ".center(get_terminal_width(), separator)
    kwargs.setdefault("bold", True)
    click.secho(message, **kwargs)


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

    def _style(text: str, **kwargs: Any) -> str:
        text = text.encode(IO_ENCODING, errors="replace").decode("utf-8")
        return click.style(text, **kwargs)

else:

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
                config=ctx.output_config,
            )
        )
        click.echo()


VERIFY_URL_SUGGESTION = "Verify that the URL points directly to the Open API schema or GraphQL endpoint"
DISABLE_SSL_SUGGESTION = f"Bypass SSL verification with {bold('`--request-tls-verify=false`')}."
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
        click.secho(f"    {extra}")


def display_header(version: str) -> None:
    prefix = "v" if version != "dev" else ""
    header = f"Schemathesis {prefix}{version}"
    click.secho(header, bold=True)
    click.secho("━" * len(header), bold=True)
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
    missing_auth: dict[int, list[str]] = field(default_factory=dict)


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
            parts.append(f"🚫 {self.stats[Status.ERROR]:{width}d} errors")
        if self.stats[Status.SKIP]:
            parts.append(f"⏭️ {self.stats[Status.SKIP]:{width}d} skipped")
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
                self.title_progress.update(self.title_task_id)
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
            icon = "⏭️"
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
    links_seen: set[str]
    stats: dict[Status, int]
    is_interrupted: bool

    __slots__ = (
        "console",
        "title",
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
        "links_seen",
        "stats",
        "is_interrupted",
    )

    def __init__(self, console: Console, title: str, links_total: int) -> None:
        from rich.progress import Progress, SpinnerColumn, TextColumn, TimeElapsedColumn
        from rich.style import Style

        self.console = console
        self.title = title
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
        self.links_seen = set()
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
        self.title_task_id = self.title_progress.add_task("Stateful tests")
        self.progress_task_id = self.progress_bar.add_task("", scenarios=0, links=f"0/{self.links_total} links")

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

    def update(self, links_seen: set[str], status: Status | None = None) -> None:
        """Update progress and stats."""
        self.scenarios += 1
        self.links_seen.update(links_seen)

        if status is not None:
            self.stats[status] += 1

        self._update_progress_display()
        self._update_stats_display()

    def _update_progress_display(self) -> None:
        """Update the progress display."""
        assert self.progress_task_id is not None
        self.progress_bar.update(
            self.progress_task_id,
            scenarios=self.scenarios,
            links=f"{len(self.links_seen)}/{self.links_total} links",
        )

    def _get_stats_message(self) -> str:
        """Get formatted stats message."""
        parts = []
        if self.stats[Status.SUCCESS]:
            parts.append(f"✅ {self.stats[Status.SUCCESS]} passed")
        if self.stats[Status.FAILURE]:
            parts.append(f"❌ {self.stats[Status.FAILURE]} failed")
        if self.stats[Status.ERROR]:
            parts.append(f"🚫 {self.stats[Status.ERROR]} errors")
        if self.stats[Status.SKIP]:
            parts.append(f"⏭️ {self.stats[Status.SKIP]} skipped")
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
            icon = "⏭️"
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
    """Format duration in milliseconds to human readable string."""
    parts = []

    # Convert to components
    ms = duration_ms % 1000
    seconds = (duration_ms // 1000) % 60
    minutes = (duration_ms // (1000 * 60)) % 60
    hours = duration_ms // (1000 * 60 * 60)

    # Add non-empty components
    if hours > 0:
        parts.append(f"{hours} h")
    if minutes > 0:
        parts.append(f"{minutes} m")
    if seconds > 0:
        parts.append(f"{seconds} s")
    if ms > 0:
        parts.append(f"{ms} ms")

    # Handle zero duration
    if not parts:
        return "0 ms"

    return " ".join(parts)


@dataclass
class OutputHandler(EventHandler):
    workers_num: int
    rate_limit: str | None
    wait_for_schema: float | None

    loading_manager: LoadingProgressManager | None = None
    probing_manager: ProbingProgressManager | None = None
    unit_tests_manager: UnitTestProgressManager | None = None
    stateful_tests_manager: StatefulProgressManager | None = None

    statistic: ApiStatistic | None = None
    skip_reasons: list[str] = field(default_factory=list)
    cassette_config: CassetteConfig | None = None
    junit_xml_file: str | None = None
    warnings: WarningData = field(default_factory=WarningData)
    errors: list[events.NonFatalError] = field(default_factory=list)
    phases: dict[PhaseName, tuple[Status, PhaseSkipReason | None]] = field(
        default_factory=lambda: {phase: (Status.SKIP, None) for phase in PhaseName}
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
            self._on_scenario_finished(event)
        if isinstance(event, events.EngineFinished):
            self._on_engine_finished(ctx, event)
        elif isinstance(event, events.Interrupted):
            self._on_interrupted(event)
        elif isinstance(event, events.FatalError):
            self._on_fatal_error(ctx, event)
        elif isinstance(event, events.NonFatalError):
            self.errors.append(event)
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
        table.add_row("Operations:", str(event.statistic.operations.total))

        message = Padding(table, BLOCK_PADDING)
        self.console.print(message)
        self.console.print()

        if ctx.initialization_lines:
            _print_lines(ctx.initialization_lines)

    def _on_phase_started(self, event: events.PhaseStarted) -> None:
        phase = event.phase
        if phase.name == PhaseName.PROBING and phase.is_enabled:
            self._start_probing()
        elif phase.name == PhaseName.UNIT_TESTING and phase.is_enabled:
            self._start_unit_tests()
        elif phase.name == PhaseName.STATEFUL_TESTING and phase.is_enabled and phase.skip_reason is None:
            self._start_stateful_tests()

    def _start_probing(self) -> None:
        self.probing_manager = ProbingProgressManager(console=self.console)
        self.probing_manager.start()

    def _start_unit_tests(self) -> None:
        assert self.statistic is not None
        self.unit_tests_manager = UnitTestProgressManager(
            console=self.console,
            title="Unit tests",
            total=self.statistic.operations.total,
        )
        self.unit_tests_manager.start()

    def _start_stateful_tests(self) -> None:
        assert self.statistic is not None
        self.stateful_tests_manager = StatefulProgressManager(
            console=self.console,
            title="Stateful tests",
            links_total=self.statistic.links.total,
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
                        ProbeOutcome.ERROR: ("⚠", Style(color="yellow")),
                    }[probe_run.outcome]

                    table.add_row(f"{probe_run.probe.name}:", Text(icon, style=style))

                message = Padding(table, BLOCK_PADDING)
            elif event.status == Status.SKIP:
                message = Padding(
                    Text.assemble(
                        ("⏭️  ", ""),
                        ("API probing skipped", Style(color="yellow")),
                    ),
                    BLOCK_PADDING,
                )
            else:
                assert event.status == Status.ERROR
                assert isinstance(event.payload, Err)
                error = EngineErrorInfo(event.payload.err())
                message = Padding(
                    Text.assemble(
                        ("🚫  ", ""),
                        (f"API probing failed: {error.message}", Style(color="red")),
                    ),
                    BLOCK_PADDING,
                )
            self.console.print(message)
            self.console.print()
        elif phase.name == PhaseName.STATEFUL_TESTING and phase.is_enabled:
            assert self.stateful_tests_manager is not None
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
            table.add_row(
                "API Links:", f"{len(self.stateful_tests_manager.links_seen)}/{self.stateful_tests_manager.links_total}"
            )

            self.console.print()
            self.console.print(Padding(table, BLOCK_PADDING))
            self.console.print()
            self.console.print(Padding(Text(summary, style="bright_white"), (0, 0, 0, 5)))
            self.console.print()
            self.stateful_tests_manager = None
        elif phase.name == PhaseName.UNIT_TESTING and phase.is_enabled:
            assert self.unit_tests_manager is not None
            self.unit_tests_manager.stop()
            if event.status == Status.ERROR:
                message = self.unit_tests_manager.get_completion_message("🚫")
            else:
                message = self.unit_tests_manager.get_completion_message()
            self.console.print(Padding(Text(message, style="white"), BLOCK_PADDING))
            if event.status != Status.INTERRUPTED:
                self.console.print()
            self.unit_tests_manager = None

    def _on_scenario_started(self, event: events.ScenarioStarted) -> None:
        if event.phase == PhaseName.UNIT_TESTING:
            # We should display execution result + percentage in the end. For example:
            assert event.label is not None
            assert self.unit_tests_manager is not None
            self.unit_tests_manager.start_operation(event.label)

    def _on_scenario_finished(self, event: events.ScenarioFinished) -> None:
        if event.phase == PhaseName.UNIT_TESTING:
            assert self.unit_tests_manager is not None
            if event.label:
                self.unit_tests_manager.finish_operation(event.label)
            self.unit_tests_manager.update_progress()
            self.unit_tests_manager.update_stats(event.status)
            if event.status == Status.SKIP and event.skip_reason is not None:
                self.skip_reasons.append(event.skip_reason)
            self._check_warnings(event)
        elif (
            event.phase == PhaseName.STATEFUL_TESTING
            and not event.is_final
            and event.status not in (Status.INTERRUPTED, Status.SKIP, None)
        ):
            assert self.stateful_tests_manager is not None
            links_seen = {case.transition.id for case in event.recorder.cases.values() if case.transition is not None}
            self.stateful_tests_manager.update(links_seen, event.status)

    def _check_warnings(self, event: events.ScenarioFinished) -> None:
        for status_code in (401, 403):
            if has_too_many_responses_with_status(event.recorder.interactions.values(), status_code):
                self.warnings.missing_auth.setdefault(status_code, []).append(event.recorder.label)

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

            if not (event.exception.kind == LoaderErrorKind.CONNECTION_OTHER and self.wait_for_schema is not None):
                suggestion = LOADER_ERROR_SUGGESTIONS.get(event.exception.kind)
                if suggestion is not None:
                    click.secho(f"{click.style('Tip:', bold=True, fg='green')} {suggestion}")

            raise click.Abort
        title = "Test Execution Error"
        message = DEFAULT_INTERNAL_ERROR_MESSAGE
        traceback = format_exception(event.exception, with_traceback=True)
        extras = split_traceback(traceback)
        suggestion = f"Please consider reporting the traceback above to our issue tracker:\n\n  {ISSUE_TRACKER_URL}."
        click.secho(title, fg="red", bold=True)
        click.echo()
        click.secho(message)
        _display_extras(extras)
        if not (
            isinstance(event.exception, LoaderError)
            and event.exception.kind == LoaderErrorKind.CONNECTION_OTHER
            and self.wait_for_schema is not None
        ):
            click.secho(f"\n{click.style('Tip:', bold=True, fg='green')} {suggestion}")

        raise click.Abort

    def display_warnings(self) -> None:
        display_section_name("WARNINGS")
        total = sum(len(endpoints) for endpoints in self.warnings.missing_auth.values())
        suffix = "" if total == 1 else "s"
        click.secho(
            f"\nMissing or invalid API credentials: {total} API operation{suffix} returned authentication errors\n",
            fg="yellow",
        )

        for status_code, operations in self.warnings.missing_auth.items():
            status_text = "Unauthorized" if status_code == 401 else "Forbidden"
            count = len(operations)
            suffix = "" if count == 1 else "s"
            click.secho(
                f"{status_code} {status_text} ({count} operation{suffix}):",
                fg="yellow",
            )
            # Show first few API operations
            for endpoint in operations[:3]:
                click.secho(f"  • {endpoint}", fg="yellow")
            if len(operations) > 3:
                click.secho(f"  + {len(operations) - 3} more", fg="yellow")
            click.echo()
        click.secho("Tip: ", bold=True, fg="yellow", nl=False)
        click.secho(f"Use {bold('--auth')} ", fg="yellow", nl=False)
        click.secho(f"or {bold('-H')} ", fg="yellow", nl=False)
        click.secho("to provide authentication credentials", fg="yellow")
        click.echo()

    def display_experiments(self) -> None:
        display_section_name("EXPERIMENTS")

        click.echo()
        for experiment in sorted(GLOBAL_EXPERIMENTS.enabled, key=lambda e: e.name):
            click.secho(f"🧪 {experiment.name}: ", bold=True, nl=False)
            click.secho(experiment.description)
            click.secho(f"   Feedback: {experiment.discussion_url}")
            click.echo()

        click.secho(
            "Your feedback is crucial for experimental features. "
            "Please visit the provided URL(s) to share your thoughts.",
            dim=True,
        )
        click.echo()

    def display_api_operations(self, ctx: ExecutionContext) -> None:
        assert self.statistic is not None
        click.secho("API Operations:", bold=True)
        click.secho(
            f"  Selected: {click.style(str(self.statistic.operations.selected), bold=True)}/"
            f"{click.style(str(self.statistic.operations.total), bold=True)}"
        )
        click.secho(f"  Tested: {click.style(str(len(ctx.statistic.tested_operations)), bold=True)}")
        errors = len(
            {
                err.label
                for err in self.errors
                # Some API operations may have some tests before they have an error
                if err.phase == PhaseName.UNIT_TESTING
                and err.label not in ctx.statistic.tested_operations
                and err.related_to_operation
            }
        )
        if errors:
            click.secho(f"  Errored: {click.style(str(errors), bold=True)}")

        # API operations that are skipped due to fail-fast are counted here as well
        total_skips = self.statistic.operations.selected - len(ctx.statistic.tested_operations) - errors
        if total_skips:
            click.secho(f"  Skipped: {click.style(str(total_skips), bold=True)}")
            for reason in sorted(set(self.skip_reasons)):
                click.secho(f"    - {reason.rstrip('.')}")
        click.echo()

    def display_phases(self) -> None:
        click.secho("Test Phases:", bold=True)

        for phase in PhaseName:
            status, skip_reason = self.phases[phase]

            if status == Status.SKIP:
                click.secho(f"  ⏭️ {phase.value}", fg="yellow", nl=False)
                if skip_reason:
                    click.secho(f" ({skip_reason.value})", fg="yellow")
                else:
                    click.echo()
            elif status == Status.SUCCESS:
                click.secho(f"  ✅ {phase.value}", fg="green")
            elif status == Status.FAILURE:
                click.secho(f"  ❌ {phase.value}", fg="red")
            elif status == Status.ERROR:
                click.secho(f"  🚫 {phase.value}", fg="red")
            elif status == Status.INTERRUPTED:
                click.secho(f"  ⚡ {phase.value}", fg="yellow")
        click.echo()

    def display_test_cases(self, ctx: ExecutionContext) -> None:
        if ctx.statistic.total_cases == 0:
            click.secho("Test cases:", bold=True)
            click.secho("  No test cases were generated\n")
            return

        unique_failures = sum(
            len(group.failures) for grouped in ctx.statistic.failures.values() for group in grouped.values()
        )
        click.secho("Test cases:", bold=True)

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

        click.secho(", ".join(parts) + "\n")

    def display_failures_summary(self, ctx: ExecutionContext) -> None:
        # Collect all unique failures and their counts by title
        failure_counts: dict[str, tuple[Severity, int]] = {}
        for grouped in ctx.statistic.failures.values():
            for group in grouped.values():
                for failure in group.failures:
                    data = failure_counts.get(failure.title, (failure.severity, 0))
                    failure_counts[failure.title] = (failure.severity, data[1] + 1)

        click.secho("Failures:", bold=True)

        # Sort by severity first, then by title
        sorted_failures = sorted(failure_counts.items(), key=lambda x: (x[1][0], x[0]))

        for title, (_, count) in sorted_failures:
            click.secho(f"  ❌ {title}: ", nl=False)
            click.secho(str(count), bold=True)
        click.echo()

    def display_errors_summary(self) -> None:
        # Group errors by title and count occurrences
        error_counts: dict[str, int] = {}
        for error in self.errors:
            title = error.info.title
            error_counts[title] = error_counts.get(title, 0) + 1

        click.secho("Errors:", bold=True)

        for title in sorted(error_counts):
            click.secho(f"  🚫 {title}: ", nl=False)
            click.secho(str(error_counts[title]), bold=True)
        click.echo()

    def display_final_line(self, ctx: ExecutionContext, event: events.EngineFinished) -> None:
        parts = []

        unique_failures = sum(
            len(group.failures) for grouped in ctx.statistic.failures.values() for group in grouped.values()
        )
        if unique_failures:
            parts.append(f"{unique_failures} failures")

        if self.errors:
            parts.append(f"{len(self.errors)} errors")

        total_warnings = sum(len(endpoints) for endpoints in self.warnings.missing_auth.values())
        if total_warnings:
            parts.append(f"{total_warnings} warnings")

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
        reports = []
        if self.cassette_config is not None:
            format_name = self.cassette_config.format.name.upper()
            reports.append((format_name, self.cassette_config.path.name))
        if self.junit_xml_file is not None:
            reports.append(("JUnit XML", self.junit_xml_file))

        if reports:
            click.secho("Reports:", bold=True)
            for report_type, path in reports:
                click.secho(f"  • {report_type}: {path}")
            click.echo()

    def _on_engine_finished(self, ctx: ExecutionContext, event: events.EngineFinished) -> None:
        if self.errors:
            display_section_name("ERRORS")
            errors = sorted(self.errors, key=lambda r: (r.phase.value, r.label))
            for error in errors:
                display_section_name(error.label, "_", fg="red")
                click.echo(error.info.format(bold=lambda x: click.style(x, bold=True)))
            click.secho(
                f"\nNeed more help?\n    Join our Discord server: {DISCORD_LINK}",
                fg="red",
            )
        display_failures(ctx)
        if self.warnings.missing_auth:
            self.display_warnings()
        if GLOBAL_EXPERIMENTS.enabled:
            self.display_experiments()
        display_section_name("SUMMARY")
        click.echo()

        if self.statistic:
            self.display_api_operations(ctx)

        self.display_phases()

        if ctx.statistic.failures:
            self.display_failures_summary(ctx)

        if self.errors:
            self.display_errors_summary()

        if self.warnings.missing_auth:
            affected = sum(len(operations) for operations in self.warnings.missing_auth.values())
            click.secho("Warnings:", bold=True)
            click.secho(f"  ⚠️ Missing authentication: {bold(str(affected))}", fg="yellow")
            click.echo()

        if ctx.summary_lines:
            _print_lines(ctx.summary_lines)
            click.echo()

        self.display_test_cases(ctx)
        self.display_reports()
        self.display_final_line(ctx, event)


TOO_MANY_RESPONSES_WARNING_TEMPLATE = (
    "Most of the responses from {} have a {} status code. Did you specify proper API credentials?"
)
TOO_MANY_RESPONSES_THRESHOLD = 0.9


def has_too_many_responses_with_status(interactions: Iterable[Interaction], status_code: int) -> bool:
    matched = 0
    total = 0
    for interaction in interactions:
        if interaction.response is not None:
            if interaction.response.status_code == status_code:
                matched += 1
            total += 1
    if not total:
        return False
    return matched / total >= TOO_MANY_RESPONSES_THRESHOLD
