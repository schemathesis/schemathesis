from __future__ import annotations

import time
from itertools import groupby
from typing import TYPE_CHECKING

import click

from schemathesis.cli.commands.run.handlers.base import BaseOutputHandler
from schemathesis.cli.events import LoadingFinished, LoadingStarted
from schemathesis.cli.output import (
    BLOCK_PADDING,
    display_api_operations,
    display_errors_summary,
    display_failures,
    display_failures_summary,
    display_final_line,
    display_header,
    display_section_name,
    display_seed,
    display_test_cases,
    format_duration,
    make_console,
    print_lines,
)
from schemathesis.core.version import SCHEMATHESIS_VERSION
from schemathesis.engine import Status, StopReason
from schemathesis.engine.events import (
    EngineFinished,
    EngineStarted,
    FatalError,
    FuzzScenarioFinished,
    NonFatalError,
)

if TYPE_CHECKING:
    from rich.console import Console, ConsoleOptions, RenderResult
    from rich.live import Live
    from rich.progress import Progress, TaskID

    from schemathesis.cli.commands.fuzz.context import FuzzExecutionContext
    from schemathesis.cli.output import LoadingProgressManager
    from schemathesis.engine import events

SEPARATOR = " * "
_INDENT = "    "

_STOP_REASON_LABELS = {
    StopReason.MAX_TIME: "Time limit reached",
    StopReason.FAILURE_LIMIT: "Failure limit reached",
    StopReason.COMPLETED: "Completed",
    StopReason.INTERRUPTED: "Interrupted",
}


class _ThroughputRenderable:
    """Renders elapsed time and scenario rate; recomputed on every Rich refresh."""

    __slots__ = ("_manager",)

    def __init__(self, manager: FuzzProgressManager) -> None:
        self._manager = manager

    def __rich_console__(self, console: Console, options: ConsoleOptions) -> RenderResult:
        from rich.text import Text

        m = self._manager
        elapsed_secs = time.monotonic() - m.start_time
        h, rem = divmod(int(elapsed_secs), 3600)
        mins, secs = divmod(rem, 60)
        elapsed_str = f"{h}:{mins:02d}:{secs:02d}"
        rate = m.total_scenarios / elapsed_secs if elapsed_secs > 0 else 0.0
        yield Text(f"{_INDENT}{elapsed_str}{SEPARATOR}{rate:.1f}/s{SEPARATOR}{m.total_scenarios} scenarios")


class _CountersRenderable:
    """Renders scenario counters; recomputed on every Rich refresh."""

    __slots__ = ("_manager",)

    def __init__(self, manager: FuzzProgressManager) -> None:
        self._manager = manager

    def __rich_console__(self, console: Console, options: ConsoleOptions) -> RenderResult:
        from rich.text import Text

        m = self._manager
        errors = m.stats[Status.ERROR]
        parts = []
        if m.unique_failures:
            parts.append(f"❌ {m.unique_failures} unique failures")
        if errors:
            parts.append(f"🚫 {errors} errors")
        text = SEPARATOR.join(parts) if parts else "No issues found yet"
        yield Text(f"{_INDENT}{text}")


class _LastFailureRenderable:
    """Renders time since last new unique failure; recomputed on every Rich refresh."""

    __slots__ = ("_manager",)

    def __init__(self, manager: FuzzProgressManager) -> None:
        self._manager = manager

    def __rich_console__(self, console: Console, options: ConsoleOptions) -> RenderResult:
        from rich.text import Text

        m = self._manager
        if m.last_failure_time is None:
            text = "Last new failure: none yet"
        else:
            secs = time.monotonic() - m.last_failure_time
            text = f"Last new failure: {secs:.2f}s ago"
        yield Text(f"{_INDENT}{text}")


class FuzzProgressManager:
    """Live progress display for st fuzz."""

    console: Console
    start_time: float
    title_progress: Progress
    title_task_id: TaskID
    live: Live | None
    total_scenarios: int
    stats: dict[Status, int]
    last_failure_time: float | None
    unique_failures: int

    __slots__ = (
        "console",
        "start_time",
        "title_progress",
        "title_task_id",
        "live",
        "total_scenarios",
        "stats",
        "last_failure_time",
        "unique_failures",
    )

    def __init__(self, *, console: Console) -> None:
        from rich.progress import Progress, SpinnerColumn, TextColumn
        from rich.style import Style

        self.console = console
        self.start_time = time.monotonic()
        self.total_scenarios = 0
        self.stats: dict[Status, int] = {Status.ERROR: 0}
        self.last_failure_time = None
        self.unique_failures = 0
        self.live = None

        self.title_progress = Progress(
            TextColumn(""),
            SpinnerColumn("dots"),
            TextColumn("{task.description}", style=Style(color="bright_white")),
            console=self.console,
        )
        self.title_task_id = self.title_progress.add_task("  Fuzzing")

    def start(self) -> None:
        from rich.console import Group
        from rich.live import Live
        from rich.text import Text

        group = Group(
            self.title_progress,
            Text(),
            _ThroughputRenderable(self),
            Text(),
            _CountersRenderable(self),
            _LastFailureRenderable(self),
        )
        self.live = Live(group, refresh_per_second=10, console=self.console, transient=True)
        self.live.start()

    def stop(self) -> None:
        if self.live is not None:
            self.live.stop()

    def get_completion_message(self) -> str:
        duration = format_duration(int((time.monotonic() - self.start_time) * 1000))
        errors = self.stats[Status.ERROR]

        if self.unique_failures > 0 or errors > 0:
            icon = "🚫" if errors > 0 else "❌"
        elif self.total_scenarios > 0:
            icon = "✅"
        else:
            icon = "🕛"

        parts = []
        if self.total_scenarios:
            parts.append(f"✅ {self.total_scenarios} scenarios")
        if self.unique_failures:
            parts.append(f"❌ {self.unique_failures} unique failures")
        if errors:
            suffix = "s" if errors > 1 else ""
            parts.append(f"🚫 {errors} error{suffix}")
        stats_line = "  ".join(parts) if parts else "No scenarios were run"

        return f"{icon}  Fuzzing (in {duration})\n\n    {stats_line}"

    def update(self, event: FuzzScenarioFinished, unique_failures: int) -> None:
        self.total_scenarios += 1
        if unique_failures > self.unique_failures:
            self.last_failure_time = time.monotonic()
        self.unique_failures = unique_failures

    def update_error_count(self, error_count: int) -> None:
        self.stats[Status.ERROR] = error_count


class FuzzOutputHandler(BaseOutputHandler["FuzzExecutionContext"]):
    """Output handler for st fuzz: composes loading manager + live progress display."""

    console: Console
    loading_manager: LoadingProgressManager | None
    progress_manager: FuzzProgressManager

    __slots__ = ("console", "loading_manager", "progress_manager")

    def __init__(self) -> None:
        self.console = make_console()
        self.loading_manager = None
        self.progress_manager = FuzzProgressManager(console=self.console)

    def start(self, ctx: FuzzExecutionContext) -> None:
        display_header(SCHEMATHESIS_VERSION)

    def handle_event(self, ctx: FuzzExecutionContext, event: events.EngineEvent) -> None:
        if isinstance(event, LoadingStarted):
            self._on_loading_started(event)
        elif isinstance(event, LoadingFinished):
            from rich.padding import Padding

            assert self.loading_manager is not None
            self.loading_manager.stop()
            self.console.print(Padding(self.loading_manager.get_completion_message(), BLOCK_PADDING))
            self.console.print()
            self.loading_manager = None
            if ctx.initialization_lines:
                print_lines(ctx.initialization_lines)
        elif isinstance(event, EngineStarted):
            self.progress_manager.start()
        elif isinstance(event, FuzzScenarioFinished):
            self.progress_manager.update(event, unique_failures=len(ctx.statistic.unique_failures_map))
        elif isinstance(event, NonFatalError):
            self.progress_manager.update_error_count(len(ctx.errors))
        elif isinstance(event, FatalError):
            self._on_fatal_error(ctx, event)
        elif isinstance(event, EngineFinished):
            self.progress_manager.stop()
            self._render_report(ctx, event)

    def shutdown(self, ctx: FuzzExecutionContext) -> None:
        self.progress_manager.stop()
        if self.loading_manager is not None:
            self.loading_manager.stop()

    def _render_report(self, ctx: FuzzExecutionContext, event: EngineFinished) -> None:
        from rich.padding import Padding
        from rich.text import Text

        self.console.print(Padding(Text(self.progress_manager.get_completion_message(), style="white"), BLOCK_PADDING))
        self.console.print()

        if ctx.errors:
            display_section_name("ERRORS")
            sorted_errors = sorted(ctx.errors, key=lambda r: (r.label, r.info.title))
            for label, group_errors in groupby(sorted_errors, key=lambda r: r.label):
                display_section_name(label, "_", fg="red")
                _errors = list(group_errors)
                for idx, error in enumerate(_errors, 1):
                    click.echo(error.info.format(bold=lambda x: click.style(x, bold=True)))
                    if idx < len(_errors):
                        click.echo()

        display_failures(ctx.statistic, ctx.config.output)

        display_section_name("SUMMARY")
        click.echo()

        if ctx.api_statistic is not None:
            _display_api_operations(ctx)

        click.echo(f"Stop reason: {_STOP_REASON_LABELS.get(event.stop_reason, str(event.stop_reason))}")
        click.echo()

        if ctx.statistic.failures:
            display_failures_summary(ctx.statistic)

        if ctx.errors:
            display_errors_summary(ctx.errors)

        if ctx.summary_lines:
            print_lines(ctx.summary_lines)
            click.echo()

        display_test_cases(ctx.statistic)
        display_seed(ctx.config)
        unique_failures = sum(
            len(group.failures) for grouped in ctx.statistic.failures.values() for group in grouped.values()
        )
        display_final_line(
            failures=unique_failures,
            errors=len(ctx.errors),
            running_time=event.running_time,
            total_cases=ctx.statistic.total_cases,
        )


def _display_api_operations(ctx: FuzzExecutionContext) -> None:
    assert ctx.api_statistic is not None
    errored = len(
        {
            err.label
            for err in ctx.errors
            if err.related_to_operation and err.label not in ctx.statistic.tested_operations
        }
    )
    display_api_operations(
        selected=ctx.api_statistic.operations.selected,
        total=ctx.api_statistic.operations.total,
        tested=len(ctx.statistic.tested_operations),
        errored=errored,
    )
