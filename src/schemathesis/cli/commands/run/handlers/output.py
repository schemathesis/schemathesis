from __future__ import annotations

import textwrap
import time
from dataclasses import dataclass, field
from itertools import groupby
from json.decoder import JSONDecodeError
from typing import TYPE_CHECKING

import click

from schemathesis.cli.commands.run.handlers.base import BaseOutputHandler
from schemathesis.cli.commands.run.warnings import WarningCollector, WarningData
from schemathesis.cli.context import BaseExecutionContext
from schemathesis.cli.events import LoadingFinished, LoadingStarted
from schemathesis.cli.output import (
    BLOCK_PADDING,
    LoadingProgressManager,
    _style,
    display_api_operations,
    display_errors_summary,
    display_failures,
    display_failures_summary,
    display_fatal_error,
    display_final_line,
    display_header,
    display_section_name,
    display_seed,
    display_test_cases,
    format_duration,
    make_console,
    make_progress_bar,
    print_lines,
)
from schemathesis.config import ProjectConfig, ReportFormat
from schemathesis.core.output import decode_response_text, prepare_response_payload
from schemathesis.core.result import Ok
from schemathesis.core.statistic import ApiStatistic
from schemathesis.core.timing import Instant
from schemathesis.core.version import SCHEMATHESIS_VERSION
from schemathesis.engine import Status, StopReason, events
from schemathesis.engine.run import PhaseName, PhaseSkipReason
from schemathesis.engine.run.probes import ProbeOutcome

if TYPE_CHECKING:
    from rich.console import Console
    from rich.live import Live
    from rich.progress import Progress, TaskID
    from rich.text import Text

    from schemathesis.engine.run.cache import CacheReport
    from schemathesis.generation.stateful.state_machine import ExtractionFailure

DISCORD_LINK = "https://discord.gg/R9ASRAmHnA"


def _format_cache_row(report: CacheReport | None) -> Text | None:
    """Render the `Cache:` row, or `None` if there is nothing to show."""
    from rich.text import Text

    if report is None:
        return None
    if not report.available:
        return Text("unavailable, running without cache")
    parts = []
    if report.replayed:
        noun = "request" if report.replayed == 1 else "requests"
        parts.append(f"{report.replayed} {noun} replayed")
    if report.dropped:
        parts.append(f"{report.dropped} stale removed")
    if not parts:
        return None
    return Text(", ".join(parts))


def get_status_icon(stats: dict[Status, int], *, is_interrupted: bool, default: str = "🕛") -> str:
    if is_interrupted:
        return "⚡"
    if stats[Status.ERROR] > 0:
        return "🚫"
    if stats[Status.FAILURE] > 0:
        return "❌"
    if stats[Status.SUCCESS] > 0:
        return "✅"
    if stats[Status.SKIP] > 0:
        return "⏭ "
    return default


def bold(option: str) -> str:
    return click.style(option, bold=True)


TRUNCATION_PLACEHOLDER = "[...]"


@dataclass(slots=True)
class ProbingProgressManager:
    console: Console
    started_at: Instant
    progress: Progress
    progress_task_id: TaskID | None
    is_interrupted: bool

    def __init__(self, console: Console) -> None:
        from rich.progress import Progress, RenderableColumn, SpinnerColumn, TextColumn
        from rich.text import Text

        self.console = console
        self.started_at = Instant()
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

        duration = format_duration(self.started_at.elapsed_ms)
        if self.is_interrupted:
            return Text.assemble(
                ("⚡  ", Style(color="yellow")),
                (f"API probing interrupted after {duration}", Style(color="white")),
            )
        return Text.assemble(
            ("✅  ", Style(color="green")),
            ("API capabilities:", Style(color="white")),
        )


@dataclass(slots=True)
class OperationProgress:
    """Tracks individual operation progress."""

    label: str
    start_time: float
    task_id: TaskID


@dataclass(slots=True)
class UnitTestProgressManager:
    """Manages progress display for unit tests."""

    console: Console
    title: str
    current: int
    total: int
    started_at: Instant

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

    def __init__(
        self,
        *,
        console: Console,
        title: str,
        total: int,
    ) -> None:
        from rich.progress import (
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
        self.started_at = Instant()

        # Initialize progress displays
        self.title_progress = Progress(
            TextColumn(""),
            SpinnerColumn("clock"),
            TextColumn("{task.description}", style=Style(color="white")),
            console=self.console,
        )
        self.title_task_id = None

        self.progress_bar = make_progress_bar(self.console, indent="    ", transient=False)
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
        return get_status_icon(self.stats, is_interrupted=self.is_interrupted, default=default_icon)

    def get_completion_message(self, default_icon: str = "🕛") -> str:
        """Complete the phase and return status message."""
        duration = format_duration(self.started_at.elapsed_ms)
        icon = self._get_status_icon(default_icon)

        message = self._get_stats_message() or "No tests were run"
        if self.is_interrupted:
            duration_message = f"interrupted after {duration}"
        else:
            duration_message = f"in {duration}"

        return f"{icon}  {self.title} ({duration_message})\n\n    {message}"


@dataclass(slots=True)
class StatefulProgressManager:
    """Manages progress display for stateful testing."""

    console: Console
    title: str
    links_selected: int
    links_inferred: int
    links_total: int
    started_at: Instant

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
        self.started_at = Instant()

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
        return get_status_icon(self.stats, is_interrupted=self.is_interrupted, default=default_icon)

    def interrupt(self) -> None:
        """Handle interruption."""
        self.is_interrupted = True
        if self.live:
            self.stop()

    def get_completion_message(self, icon: str | None = None) -> tuple[str, str]:
        """Complete the phase and return status message."""
        duration = format_duration(self.started_at.elapsed_ms)
        icon = icon or self._get_status_icon()

        message = self._get_stats_message() or "No tests were run"
        if self.is_interrupted:
            duration_message = f"interrupted after {duration}"
        else:
            duration_message = f"in {duration}"

        return f"{icon}  {self.title} ({duration_message})", message


@dataclass
class OutputHandler(BaseOutputHandler[BaseExecutionContext]):
    config: ProjectConfig

    loading_manager: LoadingProgressManager | None = None
    probing_manager: ProbingProgressManager | None = None
    unit_tests_manager: UnitTestProgressManager | None = None
    stateful_tests_manager: StatefulProgressManager | None = None

    statistic: ApiStatistic | None = None
    # Keyed by operation label - a reason only applies to the operation it came from.
    skip_reasons: dict[str, set[str]] = field(default_factory=dict)
    warning_collector: WarningCollector | None = None
    errors: set[events.NonFatalError] = field(default_factory=set)
    phases: dict[PhaseName, tuple[Status, PhaseSkipReason | None]] = field(
        default_factory=lambda: dict.fromkeys(PhaseName, (Status.SKIP, None))
    )
    console: Console = field(default_factory=make_console)

    @property
    def warnings(self) -> WarningData:
        assert self.warning_collector is not None
        return self.warning_collector.data

    def handle_event(self, ctx: BaseExecutionContext, event: events.EngineEvent) -> None:
        if isinstance(event, events.PhaseStarted):
            self._on_phase_started(event)
        elif isinstance(event, events.PhaseFinished):
            self._on_phase_finished(event)
        elif isinstance(event, events.ScenarioStarted):
            self._on_scenario_started(event)
        elif isinstance(event, events.ScenarioFinished):
            self._on_scenario_finished(ctx, event)
        elif isinstance(event, events.SchemaAnalysisWarnings):
            assert self.warning_collector is not None
            self.warning_collector.on_schema_warnings(ctx, event)
        if isinstance(event, events.EngineFinished):
            self._on_engine_finished(ctx, event)
        elif isinstance(event, events.Interrupted):
            self._on_interrupted(event)
        elif isinstance(event, events.FatalError):
            self._on_fatal_error(ctx, event)
        elif isinstance(event, events.NonFatalError):
            self.errors.add(event)
        elif isinstance(event, events.RateLimitRetry):
            self._on_rate_limit_retry(event)
        elif isinstance(event, LoadingStarted):
            self._on_loading_started(event)
        elif isinstance(event, LoadingFinished):
            self._on_loading_finished(ctx, event)

    def start(self, ctx: BaseExecutionContext) -> None:
        self.warning_collector = WarningCollector(config=self.config)
        display_header(SCHEMATHESIS_VERSION)

    def shutdown(self, ctx: BaseExecutionContext) -> None:
        if self.unit_tests_manager is not None:
            self.unit_tests_manager.stop()
        if self.stateful_tests_manager is not None:
            self.stateful_tests_manager.stop()
        if self.loading_manager is not None:
            self.loading_manager.stop()
        if self.probing_manager is not None:
            self.probing_manager.stop()

    def _on_loading_finished(self, ctx: BaseExecutionContext, event: LoadingFinished) -> None:
        from rich.padding import Padding
        from rich.style import Style
        from rich.table import Table

        self.config = event.config
        assert self.warning_collector is not None
        self.warning_collector.config = event.config

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
        table.add_column("Value", style="cyan", overflow="fold")

        table.add_row("Base URL:", event.base_url)
        table.add_row("Specification:", event.specification.name)
        statistic = event.statistic.operations
        table.add_row("Operations:", f"{statistic.selected} selected / {statistic.total} total")
        if event.config.config_path:
            table.add_row("Configuration:", event.config.config_path)
        dictionaries = event.config.dictionaries
        if dictionaries:
            total_values = sum(len(d.entries) for d in dictionaries.values())
            entry_word = "entry" if total_values == 1 else "entries"
            dict_word = "dictionary" if len(dictionaries) == 1 else "dictionaries"
            table.add_row("Dictionaries:", f"{len(dictionaries)} {dict_word} / {total_values} {entry_word}")

        message = Padding(table, BLOCK_PADDING)
        self.console.print(message)
        self.console.print()

        if ctx.initialization_lines:
            print_lines(ctx.initialization_lines)

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
        assert event.payload is not None
        self.stateful_tests_manager = StatefulProgressManager(
            console=self.console,
            title="Stateful",
            links_selected=event.payload.transitions_selected,
            links_inferred=event.payload.inferred_transitions,
            links_total=event.payload.transitions_total,
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
                table.add_column("Capability", style=Style(color="bright_white", bold=True), no_wrap=True)
                table.add_column("Status", style="cyan")
                for probe_run in payload.probes:
                    icon, style = {
                        ProbeOutcome.SUCCESS: ("✓", Style(color="green")),
                        ProbeOutcome.FAILURE: ("✘", Style(color="red")),
                        ProbeOutcome.SKIP: ("⊘", Style(color="yellow")),
                    }[probe_run.outcome]

                    table.add_row(f"{probe_run.probe.name}:", Text(icon, style=style))

                cache_row = _format_cache_row(payload.cache)
                if cache_row is not None:
                    table.add_row("Cache:", cache_row)

                self.console.print(Padding(table, BLOCK_PADDING))
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

    def _on_scenario_finished(self, ctx: BaseExecutionContext, event: events.ScenarioFinished) -> None:
        assert self.warning_collector is not None
        self.warning_collector.on_scenario_finished(ctx, event)
        if event.phase in [PhaseName.EXAMPLES, PhaseName.COVERAGE, PhaseName.FUZZING]:
            assert self.unit_tests_manager is not None
            if event.label:
                self.unit_tests_manager.finish_operation(event.label)
            self.unit_tests_manager.update_progress()
            self.unit_tests_manager.update_stats(event.status)
            if event.status == Status.SKIP and event.skip_reason is not None and event.label:
                self.skip_reasons.setdefault(event.label, set()).add(event.skip_reason)
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

    def _on_rate_limit_retry(self, event: events.RateLimitRetry) -> None:
        from rich.padding import Padding
        from rich.text import Text

        retry_word = "retry" if event.retries_left == 1 else "retries"
        message = Text.assemble(
            ("⏳  ", "yellow"),
            (
                f"Rate limited — waiting {event.delay:.1f}s before retrying "
                f"{event.operation} ({event.retries_left} {retry_word} left)",
                "white",
            ),
        )
        self.console.print(Padding(message, BLOCK_PADDING))

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

    def _on_fatal_error(self, ctx: BaseExecutionContext, event: events.FatalError) -> None:
        self.shutdown(ctx)
        display_fatal_error(
            self.console,
            self.loading_manager,
            event,
            wait_for_schema=self.config.wait_for_schema,
        )
        self.loading_manager = None

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

    def _print_items(self, items: set[str]) -> None:
        """Print all items."""
        for item in sorted(items):
            click.echo(_style(f"  - {item}", fg="yellow"))

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
                self._print_items(ops)
        else:
            # Simple set of operations
            self._print_warning_header(title, len(operations), entity_name, suffix_text)
            self._print_items(operations)

        self._print_warning_tips(tips)

    def _display_grouped_detail_block(
        self,
        title: str,
        warnings: dict[str, dict[str, set[str]]],
        entity_name: str,
        suffix_text: str,
        tips: list[str],
    ) -> None:
        """Display warnings grouped by a shared cause, with per-operation details."""
        total = len({label for operations in warnings.values() for label in operations})
        self._print_warning_header(title, total, entity_name, suffix_text)

        for group, operations in sorted(warnings.items()):
            count = len(operations)
            plural = "" if count == 1 else "s"
            click.echo(_style(f"{group} ({count} {entity_name}{plural}):", fg="yellow"))
            self._print_items({f"{label} ({', '.join(sorted(details))})" for label, details in operations.items()})

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

        for idx, (entity_label, messages) in enumerate(sorted(warnings.items())):
            if show_entity_label:
                click.echo(_style(f"  - {entity_label}", fg="yellow"))
                for message in sorted(messages):
                    click.echo(_style(f"    {message}", fg="yellow"))
            else:
                for message in sorted(messages):
                    click.echo(_style(f"  {message}", fg="yellow"))

            # Add spacing between entities (but not after the last one)
            if idx < len(warnings) - 1:
                click.echo()

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
            self._display_grouped_detail_block(
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

        if self.warnings.method_not_allowed:
            self._display_warning_block(
                title="Method Not Allowed",
                operations=self.warnings.method_not_allowed,
                suffix_text=" consistently returned `405 Method Not Allowed` — skipped from later phases",
                tips=[
                    "💡 Verify the server actually accepts these methods, or remove them from the schema if unsupported"
                ],
            )

        if self.warnings.unsupported_regex:
            self._display_detailed_warning_block(
                title="Unsupported regex patterns",
                warnings=self.warnings.unsupported_regex,
                entity_name="operation",
                suffix_text=" contain regex patterns not supported by Python and were removed",
                tips=["💡 Use Python-compatible regex syntax: https://docs.python.org/3/library/re.html"],
            )

        if self.warnings.constants_extraction:
            self._display_warning_block(
                title="Constant reuse skipped",
                operations=self.warnings.constants_extraction,
                suffix_text=" could not be scanned for constant reuse",
                tips=["💡 Check that each @schemathesis.python.constants source returns your app or modules"],
                entity_name="registered source",
            )

    def display_stateful_failures(self, ctx: BaseExecutionContext) -> None:
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
                    text = decode_response_text(response)
                    if text is None:
                        click.echo(f"\n{indent}<BINARY>")
                    else:
                        payload = prepare_response_payload(text, config=ctx.config.output)
                        click.echo(textwrap.indent(f"\n{payload}", prefix=indent))

        click.echo()

    def display_api_operations(self, ctx: BaseExecutionContext, stop_reason: StopReason) -> None:
        assert self.statistic is not None
        errored = len(
            {
                err.label
                for err in self.errors
                # Some API operations may have some tests before they have an error
                if err.phase in [PhaseName.EXAMPLES, PhaseName.COVERAGE, PhaseName.FUZZING]
                and err.label not in ctx.statistic.tested_operations
                and err.related_to_operation
            }
        )
        # API operations that are skipped due to fail-fast are counted here as well
        skipped = self.statistic.operations.selected - len(ctx.statistic.tested_operations) - errored
        # An operation tested in one phase may have been skipped in another; its reason does not explain
        # the operations counted above, which were never tested at all.
        explained = {
            label: reasons
            for label, reasons in self.skip_reasons.items()
            if label not in ctx.statistic.tested_operations
        }
        reasons = {reason for values in explained.values() for reason in values}
        # Cases ran, but no selected check applied to them. Operations tested elsewhere are not in the count above.
        without_checks = ctx.statistic.operations_without_checks - ctx.statistic.tested_operations
        if without_checks:
            reasons.add("No checks ran")
        if skipped > len(explained.keys() | without_checks):
            if stop_reason.skip_explanation is not None:
                reasons.add(stop_reason.skip_explanation)
            elif any(
                self.phases[phase][0] == Status.ERROR
                for phase in (PhaseName.EXAMPLES, PhaseName.COVERAGE, PhaseName.FUZZING)
            ):
                reasons.add("Phase errored")
        skip_reasons = sorted(reasons)
        display_api_operations(
            selected=self.statistic.operations.selected,
            total=self.statistic.operations.total,
            tested=len(ctx.statistic.tested_operations),
            errored=errored,
            skipped=skipped,
            skip_reasons=skip_reasons,
        )

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

    def display_test_cases(self, ctx: BaseExecutionContext) -> None:
        display_test_cases(ctx.statistic)

    def display_failures_summary(self, ctx: BaseExecutionContext) -> None:
        display_failures_summary(ctx.statistic)

    def display_errors_summary(self) -> None:
        display_errors_summary(self.errors)

    def display_final_line(self, ctx: BaseExecutionContext, event: events.EngineFinished) -> None:
        unique_failures = sum(
            len(group.failures) for grouped in ctx.statistic.failures.values() for group in grouped.values()
        )
        display_final_line(
            failures=unique_failures,
            errors=len(self.errors),
            warnings=self.warnings.kind_count,
            running_time=event.running_time,
            total_cases=ctx.statistic.total_cases,
        )

    def display_reports(self) -> None:
        reports = self.config.reports
        if reports.vcr.enabled or reports.har.enabled or reports.junit.enabled or reports.ndjson.enabled:
            click.echo(_style("Reports:", bold=True))
            for format, report in (
                (ReportFormat.JUNIT, reports.junit),
                (ReportFormat.VCR, reports.vcr),
                (ReportFormat.HAR, reports.har),
                (ReportFormat.NDJSON, reports.ndjson),
            ):
                if report.enabled:
                    path = reports.get_path(format)
                    click.echo(_style(f"  - {format.value.upper()}: {path}"))
            click.echo()

    def display_seed(self) -> None:
        display_seed(self.config)

    def _on_engine_finished(self, ctx: BaseExecutionContext, event: events.EngineFinished) -> None:
        assert self.loading_manager is None
        assert self.probing_manager is None
        assert self.unit_tests_manager is None
        assert self.stateful_tests_manager is None
        if self.errors:
            display_section_name("ERRORS")
            errors = sorted(
                self.errors, key=lambda r: (r.phase.value if r.phase is not None else "", r.label, r.info.title)
            )
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
        display_failures(ctx.statistic, ctx.config.output, record_crashes=ctx.config.cache.enabled)
        if not self.warnings.is_empty:
            self.display_warnings()
        if ctx.statistic.extraction_failures:
            self.display_stateful_failures(ctx)
        display_section_name("SUMMARY")
        click.echo()

        if self.statistic:
            self.display_api_operations(ctx, event.stop_reason)

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
                count = len(
                    {label for operations in self.warnings.missing_deserializer.values() for label in operations}
                )
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

            if self.warnings.method_not_allowed:
                count = len(self.warnings.method_not_allowed)
                suffix = "" if count == 1 else "s"
                click.echo(
                    _style(
                        f"  ⚠️ Method Not Allowed: {bold(str(count))} operation{suffix} skipped after consistent 405 responses",
                        fg="yellow",
                    )
                )

            if self.warnings.constants_extraction:
                count = len(self.warnings.constants_extraction)
                suffix = "" if count == 1 else "s"
                click.echo(
                    _style(
                        f"  ⚠️ Constant reuse skipped: {bold(str(count))} registered source{suffix} could not be scanned",
                        fg="yellow",
                    )
                )

            click.echo()

        if event.payload is not None:
            if event.payload.reauth_count > 0:
                suffix = "" if event.payload.reauth_count == 1 else "s"
                ctx.add_summary_line(f"  Re-authenticated {event.payload.reauth_count} time{suffix}")
            if event.payload.reauth_broke:
                ctx.add_summary_line(
                    _style(
                        "  ⚠️ Authentication stopped working mid-run - credentials likely invalidated",
                        fg="yellow",
                    )
                )

        if ctx.summary_lines:
            print_lines(ctx.summary_lines)
            click.echo()

        self.display_test_cases(ctx)
        self.display_reports()
        self.display_seed()
        self.display_final_line(ctx, event)
