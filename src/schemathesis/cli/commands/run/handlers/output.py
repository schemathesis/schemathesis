from __future__ import annotations

import textwrap
import time
from dataclasses import dataclass, field
from itertools import groupby
from json.decoder import JSONDecodeError
from typing import TYPE_CHECKING

import click

from schemathesis.cli.commands.run.context import PHASE_STATUS_PRIORITY
from schemathesis.cli.commands.run.handlers.base import BaseOutputHandler
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
from schemathesis.core.timing import Instant
from schemathesis.core.version import SCHEMATHESIS_VERSION
from schemathesis.engine import Status, events
from schemathesis.engine.run import ELASTIC_PHASES, PhaseName
from schemathesis.engine.run.probes import ProbeOutcome

if TYPE_CHECKING:
    from rich.console import Console, RenderableType
    from rich.live import Live
    from rich.progress import Progress, TaskID
    from rich.text import Text

    from schemathesis.cli.commands.run.context import ExecutionContext
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
    elapsed_ms: int

    # Progress components
    title_progress: Progress
    progress_bar: Progress
    operations_progress: Progress
    current_operations: dict[str, OperationProgress]
    stats: dict[Status, int]
    outcomes: dict[str, Status]
    stats_progress: Progress
    suffix: str
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
        self.elapsed_ms = 0

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
        self.outcomes = {}

        self.stats_progress = Progress(
            TextColumn("    "),
            TextColumn("{task.description}"),
            console=self.console,
        )
        self.stats_task_id = self.stats_progress.add_task("")
        self.suffix = ""
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

    def _get_stats_message(self, *, live: bool = True) -> str:
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
        if self.suffix and live:
            parts.append(self.suffix)
        return "  ".join(parts)

    def set_suffix(self, suffix: str) -> None:
        self.suffix = suffix
        self._update_stats_display()

    def _update_stats_display(self) -> None:
        """Update the statistics display."""
        self.stats_progress.update(self.stats_task_id, description=self._get_stats_message())

    def start(self, *, show_live: bool = True) -> None:
        """Start progress display."""
        from rich.console import Group
        from rich.live import Live
        from rich.text import Text

        if not show_live:
            self.title_task_id = self.title_progress.add_task(self.title, total=self.total)
            self.progress_task_id = self.progress_bar.add_task("", total=self.total)
            return

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

    def end_pass(self) -> None:
        """Fold this pass into the phase's running time."""
        self.elapsed_ms += self.started_at.elapsed_ms

    def restart_pass(self) -> None:
        """A repeat under a budget walks the operations again; the totals keep counting."""
        self.current = 0
        self.current_operations.clear()
        self.started_at = Instant()

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

    def update_stats(self, label: str, status: Status) -> None:
        """Record an operation's outcome; a repeat under a budget retests it rather than adding one."""
        previous = self.outcomes.get(label)
        if previous is not None:
            if PHASE_STATUS_PRIORITY[status] <= PHASE_STATUS_PRIORITY[previous]:
                return
            self.stats[previous] -= 1
        self.outcomes[label] = status
        self.stats[status] += 1
        self._update_stats_display()

    def interrupt(self) -> None:
        self.is_interrupted = True
        # A repeat walks the operations again, so what is left is what never reported at all.
        self.stats[Status.SKIP] += max(self.total - len(self.outcomes), 0)
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
        duration = format_duration(self.elapsed_ms)
        icon = self._get_status_icon(default_icon)

        message = self._get_stats_message(live=False) or "No tests were run"
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
    elapsed_ms: int

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
        self.elapsed_ms = 0

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

    def start(self, *, show_live: bool = True) -> None:
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
        if not show_live:
            return
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

    def end_pass(self) -> None:
        """Fold this pass into the phase's running time."""
        self.elapsed_ms += self.started_at.elapsed_ms

    def restart_pass(self) -> None:
        """A repeat under a budget draws new scenarios; the totals keep counting."""
        self.started_at = Instant()

    def get_completion_message(self, icon: str | None = None) -> tuple[str, str]:
        """Complete the phase and return status message."""
        duration = format_duration(self.elapsed_ms)
        icon = icon or self._get_status_icon()

        message = self._get_stats_message() or "No tests were run"
        if self.is_interrupted:
            duration_message = f"interrupted after {duration}"
        else:
            duration_message = f"in {duration}"

        return f"{icon}  {self.title} ({duration_message})", message


@dataclass
class OutputHandler(BaseOutputHandler["ExecutionContext"]):
    config: ProjectConfig

    loading_manager: LoadingProgressManager | None = None
    probing_manager: ProbingProgressManager | None = None
    unit_tests_manager: UnitTestProgressManager | None = None
    stateful_tests_manager: StatefulProgressManager | None = None
    # How many times each phase has started; under a time limit they repeat.
    started_at: Instant = field(default_factory=Instant)
    cycle_live: Live | None = None
    cycle_phase: PhaseName = PhaseName.FUZZING
    cycle_bar: Progress | None = None
    cycle_bar_task_id: TaskID | None = None
    cycle_links: str = ""
    # A phase that repeats under a budget keeps one manager, so its blocks stay a single block.
    repeat_managers: dict[PhaseName, UnitTestProgressManager | StatefulProgressManager] = field(default_factory=dict)
    repeat_statuses: dict[PhaseName, Status] = field(default_factory=dict)
    cycle_context: ExecutionContext | None = None
    # When the run last found a failure it had not seen before.
    last_failure_at: Instant | None = None
    seen_failures: int = 0

    console: Console = field(default_factory=make_console)

    def handle_event(self, ctx: ExecutionContext, event: events.EngineEvent) -> None:
        if isinstance(event, events.EngineStarted):
            # The budget runs from here, not from whatever schema loading took before it.
            self.started_at = Instant()
        if isinstance(event, events.PhaseStarted):
            self._on_phase_started(ctx, event)
        elif isinstance(event, events.PhaseFinished):
            self._on_phase_finished(ctx, event)
        elif isinstance(event, events.ScenarioStarted):
            self._on_scenario_started(event)
        elif isinstance(event, events.ScenarioFinished):
            self._on_scenario_finished(ctx, event)
        if isinstance(event, events.EngineFinished):
            self._on_engine_finished(ctx, event)
        elif isinstance(event, events.Interrupted):
            self._on_interrupted(event)
        elif isinstance(event, events.FatalError):
            self._on_fatal_error(ctx, event)
        elif isinstance(event, events.RateLimitRetry):
            self._on_rate_limit_retry(event)
        elif isinstance(event, LoadingStarted):
            self._on_loading_started(event)
        elif isinstance(event, LoadingFinished):
            self._on_loading_finished(ctx, event)

    def start(self, ctx: ExecutionContext) -> None:
        display_header(SCHEMATHESIS_VERSION)

    def shutdown(self, ctx: ExecutionContext) -> None:
        if self.cycle_live is not None:
            self.cycle_live.stop()
            self.cycle_live = None
        if self.unit_tests_manager is not None:
            self.unit_tests_manager.stop()
        if self.stateful_tests_manager is not None:
            self.stateful_tests_manager.stop()
        if self.loading_manager is not None:
            self.loading_manager.stop()
        if self.probing_manager is not None:
            self.probing_manager.stop()

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

    def _on_phase_started(self, ctx: ExecutionContext, event: events.PhaseStarted) -> None:
        phase = event.phase
        if phase.name == PhaseName.PROBING and phase.is_enabled:
            self._start_probing()
        elif phase.name in [PhaseName.EXAMPLES, PhaseName.COVERAGE, PhaseName.FUZZING] and phase.is_enabled:
            self._start_unit_tests(ctx, event)
        elif phase.name == PhaseName.STATEFUL_TESTING and phase.is_enabled and phase.skip_reason is None:
            self._start_stateful_tests(event)

    def _in_cycle(self, phase: PhaseName) -> bool:
        return self.config.max_time is not None and phase in ELASTIC_PHASES

    def _start_cycle_line(self) -> None:
        from rich.live import Live
        from rich.progress import BarColumn, Progress, TextColumn

        if self.cycle_live is not None:
            return
        # The line exists only under a budget, which is what the bar counts down.
        max_time = self.config.max_time
        assert max_time is not None
        self.cycle_bar = Progress(
            TextColumn("    "),
            BarColumn(bar_width=None),
            TextColumn("{task.percentage:.0f}%"),
            console=self.console,
        )
        self.cycle_bar_task_id = self.cycle_bar.add_task("", total=max_time * 1000)
        # Rendered on every refresh tick so the clocks keep moving between scenarios.
        self.cycle_live = Live(
            get_renderable=self._cycle_renderable, refresh_per_second=10, console=self.console, transient=True
        )
        self.cycle_live.start()

    def _cycle_metrics(self, ctx: ExecutionContext) -> str:
        # Per-cycle pass/fail counts swing as operations flip between passes; these only grow.
        parts = [f"{ctx.statistic.total_cases} cases"]
        unique = len(ctx.statistic.unique_failures_map)
        if unique:
            parts.append(f"❌ {unique} unique failure{'' if unique == 1 else 's'}")
        errors = len(ctx.errors)
        if errors:
            parts.append(f"🚫 {errors} error{'' if errors == 1 else 's'}")
        if self.cycle_links:
            parts.append(self.cycle_links)
        extra = self._progress_suffix(ctx)
        if extra:
            parts.append(extra)
        return "  ".join(parts)

    def _cycle_renderable(self) -> RenderableType:
        from rich.console import Group
        from rich.text import Text

        metrics = self._cycle_metrics(self.cycle_context) if self.cycle_context is not None else ""
        body: list = [Text(f" 🕛  {self.cycle_phase.display}"), Text("")]
        if self.cycle_bar is not None and self.cycle_bar_task_id is not None:
            self.cycle_bar.update(self.cycle_bar_task_id, completed=self.started_at.elapsed_ms)
            body += [self.cycle_bar, Text("")]
        body.append(Text(f"     {metrics}"))
        return Group(*body)

    def _stop_cycle_line(self) -> None:
        if self.cycle_live is not None:
            self.cycle_live.stop()
            self.cycle_live = None
            self.cycle_bar = None
            self.cycle_bar_task_id = None
        # Every repeat of a phase folds into the one block it reports at the end of the run.
        for phase in (PhaseName.FUZZING, PhaseName.STATEFUL_TESTING):
            manager = self.repeat_managers.pop(phase, None)
            status = self.repeat_statuses.pop(phase, Status.SUCCESS)
            if isinstance(manager, StatefulProgressManager):
                self._print_stateful_completion(manager, status)
            elif manager is not None:
                self._print_unit_completion(manager, status)

    def _start_probing(self) -> None:
        self.probing_manager = ProbingProgressManager(console=self.console)
        self.probing_manager.start()

    def _start_unit_tests(self, ctx: ExecutionContext, event: events.PhaseStarted) -> None:
        assert ctx.api_statistic is not None
        assert self.unit_tests_manager is None
        phase = event.phase.name
        retained = self.repeat_managers.get(phase)
        if isinstance(retained, UnitTestProgressManager):
            self.unit_tests_manager = retained
            retained.restart_pass()
            return
        self.unit_tests_manager = UnitTestProgressManager(
            console=self.console,
            title=phase.display,
            total=ctx.api_statistic.operations.selected,
        )
        in_cycle = self._in_cycle(phase)
        if in_cycle:
            self.repeat_managers[phase] = self.unit_tests_manager
            self._start_cycle_line()
        self.unit_tests_manager.start(show_live=not in_cycle and self.cycle_live is None)

    def _start_stateful_tests(self, event: events.PhaseStarted) -> None:
        payload = event.payload
        assert isinstance(payload, events.StatefulPhasePayload)
        retained = self.repeat_managers.get(PhaseName.STATEFUL_TESTING)
        if isinstance(retained, StatefulProgressManager):
            self.stateful_tests_manager = retained
            retained.restart_pass()
            # A repeat re-runs inference, which reports zero once links are already injected.
            retained.links_selected = max(retained.links_selected, payload.transitions_selected)
            retained.links_inferred = max(retained.links_inferred, payload.inferred_transitions)
            retained.links_total = max(retained.links_total, payload.transitions_total)
            return
        self.stateful_tests_manager = StatefulProgressManager(
            console=self.console,
            title=PhaseName.STATEFUL_TESTING.display,
            links_selected=payload.transitions_selected,
            links_inferred=payload.inferred_transitions,
            links_total=payload.transitions_total,
        )
        in_cycle = self._in_cycle(PhaseName.STATEFUL_TESTING)
        if in_cycle:
            self.repeat_managers[PhaseName.STATEFUL_TESTING] = self.stateful_tests_manager
            self._start_cycle_line()
        self.stateful_tests_manager.start(show_live=not in_cycle and self.cycle_live is None)

    def _on_phase_finished(self, ctx: ExecutionContext, event: events.PhaseFinished) -> None:
        from rich.padding import Padding
        from rich.style import Style
        from rich.table import Table
        from rich.text import Text

        phase = event.phase

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
            manager = self.stateful_tests_manager
            self.stateful_tests_manager = None
            if self._retain(phase.name, event.status):
                self.cycle_context = ctx
                return
            manager.stop()
            manager.end_pass()
            self._print_stateful_completion(manager, event.status)
        elif (
            phase.name in [PhaseName.EXAMPLES, PhaseName.COVERAGE, PhaseName.FUZZING]
            and phase.is_enabled
            and self.unit_tests_manager is not None
        ):
            unit_manager = self.unit_tests_manager
            self.unit_tests_manager = None
            if self._retain(phase.name, event.status):
                self.cycle_context = ctx
                return
            unit_manager.stop()
            unit_manager.end_pass()
            self._print_unit_completion(unit_manager, event.status)

    def _retain(self, phase: PhaseName, status: Status) -> bool:
        """Hold this phase's result back while it can still repeat."""
        if phase not in self.repeat_managers:
            return False
        self.repeat_managers[phase].end_pass()
        current = self.repeat_statuses.get(phase)
        if current is None or PHASE_STATUS_PRIORITY[status] >= PHASE_STATUS_PRIORITY[current]:
            self.repeat_statuses[phase] = status
        return True

    def _print_unit_completion(self, manager: UnitTestProgressManager, status: Status) -> None:
        from rich.padding import Padding
        from rich.text import Text

        icon = "🚫" if status == Status.ERROR else "🕛"
        self.console.print(Padding(Text(manager.get_completion_message(icon), style="white"), BLOCK_PADDING))
        self.console.print()

    def _print_stateful_completion(self, manager: StatefulProgressManager, status: Status) -> None:
        from rich.padding import Padding
        from rich.style import Style
        from rich.table import Table
        from rich.text import Text

        title, summary = manager.get_completion_message("🚫" if status == Status.ERROR else None)
        self.console.print(Padding(Text(title, style="bright_white"), BLOCK_PADDING))

        table = Table(show_header=False, box=None, padding=(0, 4), collapse_padding=True)
        table.add_column("Field", style=Style(color="bright_white", bold=True))
        table.add_column("Value", style="cyan")
        table.add_row("Scenarios:", f"{manager.scenarios}")
        message = (
            f"{len(manager.links_covered)} covered / {manager.links_selected} selected / {manager.links_total} total"
        )
        if manager.links_inferred:
            message += f" ({manager.links_inferred} inferred)"
        table.add_row("API Links:", message)

        self.console.print()
        self.console.print(Padding(table, BLOCK_PADDING))
        self.console.print()
        self.console.print(Padding(Text(summary, style="bright_white"), (0, 0, 0, 5)))
        self.console.print()

    def _on_scenario_started(self, event: events.ScenarioStarted) -> None:
        if event.phase in [PhaseName.EXAMPLES, PhaseName.COVERAGE, PhaseName.FUZZING]:
            # We should display execution result + percentage in the end. For example:
            assert event.label is not None
            assert self.unit_tests_manager is not None
            self.unit_tests_manager.start_operation(event.label)

    def _note_new_failures(self, ctx: ExecutionContext) -> None:
        unique = len(ctx.statistic.unique_failures_map)
        if unique > self.seen_failures:
            self.seen_failures = unique
            self.last_failure_at = Instant()

    def _progress_suffix(self, ctx: ExecutionContext) -> str:
        max_time = self.config.max_time
        # Both clocks read against the budget; without one there is nothing to count down to.
        if max_time is None:
            return ""
        parts = [f"⏳ {format_duration(max(max_time * 1000 - self.started_at.elapsed_ms, 0))} left"]
        if self.last_failure_at is not None:
            parts.append(f"🕑 {format_duration(self.last_failure_at.elapsed_ms)} since last new failure")
        return "  ".join(parts)

    def _on_scenario_finished(self, ctx: ExecutionContext, event: events.ScenarioFinished) -> None:
        self._note_new_failures(ctx)
        if event.phase in [PhaseName.EXAMPLES, PhaseName.COVERAGE, PhaseName.FUZZING]:
            assert self.unit_tests_manager is not None
            self.unit_tests_manager.set_suffix(self._progress_suffix(ctx))
            if event.phase == PhaseName.FUZZING:
                self.cycle_phase = event.phase
            self.cycle_context = ctx
            if event.label:
                self.unit_tests_manager.finish_operation(event.label)
            self.unit_tests_manager.update_progress()
            self.unit_tests_manager.update_stats(event.label or event.recorder.label, event.status)
        elif (
            event.phase == PhaseName.STATEFUL_TESTING
            and not event.is_final
            and event.status not in (Status.INTERRUPTED, Status.SKIP, None)
        ):
            assert self.stateful_tests_manager is not None
            manager = self.stateful_tests_manager
            self.cycle_phase = PhaseName.STATEFUL_TESTING
            links_seen = {
                case.transition.id
                for case in event.recorder.cases.values()
                if case.transition is not None and case.is_transition_applied
            }
            manager.update(links_seen, event.status)
            self.cycle_links = f"🔗 {len(manager.links_covered)}/{manager.links_total} links"
            self.cycle_context = ctx

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

    def _on_fatal_error(self, ctx: ExecutionContext, event: events.FatalError) -> None:
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

    def display_warnings(self, ctx: ExecutionContext) -> None:
        display_section_name("WARNINGS")
        click.echo()
        if ctx.warnings.missing_auth:
            self._display_warning_block(
                title="Authentication failed",
                operations=ctx.warnings.missing_auth,
                suffix_text=" returned authentication errors",
                tips=["💡 Ensure valid authentication credentials are set via --auth or -H"],
            )

        if ctx.warnings.missing_test_data:
            self._display_warning_block(
                title="Missing test data",
                operations=ctx.warnings.missing_test_data,
                suffix_text=" repeatedly returned 404 Not Found, preventing tests from reaching your API's core logic",
                tips=[
                    "💡 Provide realistic parameter values in your config file so tests can access existing resources",
                ],
            )

        if ctx.warnings.validation_mismatch:
            self._display_warning_block(
                title="Schema validation mismatch",
                operations=ctx.warnings.validation_mismatch,
                suffix_text=" mostly rejected generated data due to validation errors, indicating schema constraints don't match API validation",
                tips=["💡 Check your schema constraints - API validation may be stricter than documented"],
            )

        if ctx.warnings.missing_deserializer:
            self._display_grouped_detail_block(
                title="Schema validation skipped",
                warnings=ctx.warnings.missing_deserializer,
                entity_name="operation",
                suffix_text=" cannot validate responses due to missing deserializers",
                tips=["💡 Register a deserializer with @schemathesis.deserializer() to enable validation"],
            )

        if ctx.warnings.unused_openapi_auth:
            self._display_warning_block(
                title="Unused OpenAPI auth",
                operations=ctx.warnings.unused_openapi_auth,
                suffix_text=" not defined in the schema",
                tips=[],
                entity_name="configured auth scheme",
            )

        if ctx.warnings.unmatched_filter:
            self._display_warning_block(
                title="Unmatched filters",
                operations=ctx.warnings.unmatched_filter,
                suffix_text=" matched no API operations",
                tips=["💡 Check the filter for a typo, or update it if the operation was renamed"],
                entity_name="filter",
            )

        if ctx.warnings.method_not_allowed:
            self._display_warning_block(
                title="Method Not Allowed",
                operations=ctx.warnings.method_not_allowed,
                suffix_text=" consistently returned `405 Method Not Allowed` — skipped from later phases",
                tips=[
                    "💡 Verify the server actually accepts these methods, or remove them from the schema if unsupported"
                ],
            )

        if ctx.warnings.unsupported_regex:
            self._display_detailed_warning_block(
                title="Unsupported regex patterns",
                warnings=ctx.warnings.unsupported_regex,
                entity_name="operation",
                suffix_text=" contain regex patterns no value can be generated for",
                tips=["💡 Supply examples for these operations, or narrow the pattern"],
            )

        if ctx.warnings.constants_extraction:
            self._display_warning_block(
                title="Constant reuse skipped",
                operations=ctx.warnings.constants_extraction,
                suffix_text=" could not be scanned for constant reuse",
                tips=["💡 Check that each @schemathesis.python.constants source returns your app or modules"],
                entity_name="registered source",
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
                    text = decode_response_text(response)
                    if text is None:
                        click.echo(f"\n{indent}<BINARY>")
                    else:
                        payload = prepare_response_payload(text, config=ctx.config.output)
                        click.echo(textwrap.indent(f"\n{payload}", prefix=indent))

        click.echo()

    def display_phases(self, ctx: ExecutionContext) -> None:
        click.echo(_style("Test Phases:", bold=True))

        for phase in PhaseName:
            if phase in (PhaseName.PROBING, PhaseName.SCHEMA_ANALYSIS):
                # Internal phases are not part of the test phase summary
                continue
            status, skip_reason = ctx.phases[phase]

            if status == Status.SKIP:
                click.echo(_style(f"  ⏭  {phase.display}", fg="yellow"), nl=False)
                if skip_reason:
                    click.echo(_style(f" ({skip_reason.display})", fg="yellow"))
                else:
                    click.echo()
            elif status == Status.SUCCESS:
                click.echo(_style(f"  ✅ {phase.display}", fg="green"))
            elif status == Status.FAILURE:
                click.echo(_style(f"  ❌ {phase.display}", fg="red"))
            elif status == Status.ERROR:
                click.echo(_style(f"  🚫 {phase.display}", fg="red"))
            elif status == Status.INTERRUPTED:
                click.echo(_style(f"  ⚡ {phase.display}", fg="yellow"))
        click.echo()

    def display_warnings_summary(self, ctx: ExecutionContext) -> None:
        click.echo(_style("Warnings:", bold=True))
        missing_deserializer = {
            label for operations in ctx.warnings.missing_deserializer.values() for label in operations
        }
        entries = (
            (
                sum(len(operations) for operations in ctx.warnings.missing_auth.values()),
                "Missing authentication",
                "operation",
                "returned only 401/403 responses",
            ),
            (
                len(ctx.warnings.missing_test_data),
                "Missing valid test data",
                "operation",
                "repeatedly returned 404 responses",
            ),
            (
                len(ctx.warnings.validation_mismatch),
                "Schema validation mismatch",
                "operation",
                "mostly rejected generated data",
            ),
            (len(missing_deserializer), "Schema validation skipped", "operation", "cannot validate responses"),
            (
                len(ctx.warnings.unused_openapi_auth),
                "Unused OpenAPI auth",
                "configured auth scheme",
                "not used in the schema",
            ),
            (
                len(ctx.warnings.unsupported_regex),
                "Unsupported regex",
                "operation",
                "had ungeneratable regex patterns",
            ),
            (
                len(ctx.warnings.unmatched_filter),
                "Unmatched filters",
                "filter",
                "matched no API operations",
            ),
            (
                len(ctx.warnings.method_not_allowed),
                "Method Not Allowed",
                "operation",
                "skipped after consistent 405 responses",
            ),
            (
                len(ctx.warnings.constants_extraction),
                "Constant reuse skipped",
                "registered source",
                "could not be scanned",
            ),
        )
        for count, title, entity_name, suffix_text in entries:
            if not count:
                continue
            plural = "" if count == 1 else "s"
            click.echo(_style(f"  ⚠️ {title}: {bold(str(count))} {entity_name}{plural} {suffix_text}", fg="yellow"))
        click.echo()

    def display_final_line(self, ctx: ExecutionContext, event: events.EngineFinished) -> None:
        unique_failures = sum(
            len(group.failures) for grouped in ctx.statistic.failures.values() for group in grouped.values()
        )
        display_final_line(
            failures=unique_failures,
            errors=len(ctx.errors),
            warnings=ctx.warnings.kind_count,
            running_time=event.running_time,
            total_cases=ctx.statistic.total_cases,
        )

    def display_reports(self) -> None:
        reports = self.config.reports
        enabled = [
            (format, report)
            for format, report in (
                (ReportFormat.JUNIT, reports.junit),
                (ReportFormat.VCR, reports.vcr),
                (ReportFormat.HAR, reports.har),
                (ReportFormat.NDJSON, reports.ndjson),
                (ReportFormat.ALLURE, reports.allure),
            )
            if report.enabled
        ]
        if enabled:
            click.echo(_style("Reports:", bold=True))
            for format, _ in enabled:
                click.echo(_style(f"  - {format.value.upper()}: {reports.get_path(format)}"))
            click.echo()

    def display_seed(self) -> None:
        display_seed(self.config)

    def _on_engine_finished(self, ctx: ExecutionContext, event: events.EngineFinished) -> None:
        self._stop_cycle_line()
        assert self.loading_manager is None
        assert self.probing_manager is None
        assert self.unit_tests_manager is None
        assert self.stateful_tests_manager is None
        if ctx.errors:
            display_section_name("ERRORS")
            errors = sorted(
                ctx.errors, key=lambda r: (r.phase.value if r.phase is not None else "", r.label, r.info.title)
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
        if not ctx.warnings.is_empty:
            self.display_warnings(ctx)
        if ctx.statistic.extraction_failures:
            self.display_stateful_failures(ctx)
        display_section_name("SUMMARY")
        click.echo()

        summary = ctx.summary()

        if summary.operations is not None:
            display_api_operations(summary.operations)

        self.display_phases(ctx)

        if summary.failures:
            display_failures_summary(summary.failures)

        if summary.errors:
            display_errors_summary(summary.errors)

        if not ctx.warnings.is_empty:
            self.display_warnings_summary(ctx)

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

        display_test_cases(summary.test_cases)
        self.display_reports()
        self.display_seed()
        self.display_final_line(ctx, event)
