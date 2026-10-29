from __future__ import annotations

import time
from collections.abc import Callable
from typing import TYPE_CHECKING

import click

from schemathesis.cli.commands.run.handlers.base import BaseOutputHandler
from schemathesis.cli.events import LoadingFinished, LoadingStarted
from schemathesis.cli.output import (
    Indent,
    display_api_operations,
    display_errors,
    display_failures,
    display_final_line,
    display_header,
    display_section_name,
    display_summary_counts,
    display_summary_footer,
    display_warning_blocks,
    duration_phrase,
    get_status_icon,
    issue_parts,
    make_console,
    print_lines,
    unmatched_filter_block,
)
from schemathesis.core.output import pluralize
from schemathesis.core.timing import Instant
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


class _LiveText:
    """Renderable recomputed from a callback on every Rich refresh."""

    __slots__ = ("_render",)

    def __init__(self, render: Callable[[], str]) -> None:
        self._render = render

    def __rich_console__(self, console: Console, options: ConsoleOptions) -> RenderResult:
        from rich.text import Text

        yield Text(self._render())


class FuzzProgressManager:
    """Live progress display for st fuzz."""

    console: Console
    started_at: Instant
    title_progress: Progress
    title_task_id: TaskID
    live: Live | None
    total_scenarios: int
    passed_scenarios: int
    stats: dict[Status, int]
    last_failure_time: float | None
    unique_failures: int

    __slots__ = (
        "console",
        "started_at",
        "title_progress",
        "title_task_id",
        "live",
        "total_scenarios",
        "passed_scenarios",
        "stats",
        "last_failure_time",
        "unique_failures",
    )

    def __init__(self, *, console: Console) -> None:
        from rich.progress import Progress, SpinnerColumn, TextColumn
        from rich.style import Style

        self.console = console
        self.started_at = Instant()
        self.total_scenarios = 0
        self.passed_scenarios = 0
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

    def _throughput_line(self) -> str:
        elapsed = self.started_at.elapsed
        hours, rem = divmod(int(elapsed), 3600)
        minutes, seconds = divmod(rem, 60)
        rate = self.total_scenarios / elapsed if elapsed > 0 else 0.0
        return (
            f"{_INDENT}{hours}:{minutes:02d}:{seconds:02d}"
            f"{SEPARATOR}{rate:.1f}/s{SEPARATOR}{pluralize(self.total_scenarios, 'scenario')}"
        )

    def _counters_line(self) -> str:
        parts = issue_parts(unique_failures=self.unique_failures, errors=self.stats[Status.ERROR])
        text = SEPARATOR.join(parts) if parts else "No issues found yet"
        return f"{_INDENT}{text}"

    def _last_failure_line(self) -> str:
        if self.last_failure_time is None:
            text = "Last new failure: none yet"
        else:
            seconds = time.monotonic() - self.last_failure_time
            text = f"Last new failure: {seconds:.2f}s ago"
        return f"{_INDENT}{text}"

    def start(self) -> None:
        from rich.console import Group
        from rich.live import Live
        from rich.text import Text

        group = Group(
            self.title_progress,
            Text(),
            _LiveText(self._throughput_line),
            Text(),
            _LiveText(self._counters_line),
            _LiveText(self._last_failure_line),
        )
        self.live = Live(group, refresh_per_second=10, console=self.console, transient=True)
        self.live.start()

    def stop(self) -> None:
        if self.live is not None:
            self.live.stop()

    def get_completion_message(self, *, is_interrupted: bool, without_checks: bool = False) -> str:
        errors = self.stats[Status.ERROR]
        stats = {
            Status.ERROR: errors,
            Status.FAILURE: self.unique_failures,
            Status.SUCCESS: 0 if without_checks else self.passed_scenarios,
            Status.SKIP: self.passed_scenarios if without_checks else 0,
        }
        icon = get_status_icon(stats, is_interrupted=is_interrupted)

        parts = []
        if self.passed_scenarios:
            if without_checks:
                parts.append(f"⏭  {self.passed_scenarios} without checks")
            else:
                parts.append(f"✅ {pluralize(self.passed_scenarios, 'scenario')}")
        parts.extend(issue_parts(unique_failures=self.unique_failures, errors=errors))
        stats_line = "  ".join(parts) if parts else "No scenarios were run"

        duration_message = duration_phrase(self.started_at.elapsed_ms, interrupted=is_interrupted)
        return f"{icon}  Fuzzing ({duration_message})\n\n    {stats_line}"

    def update(self, unique_failures: int, *, passed: bool) -> None:
        self.total_scenarios += 1
        if passed:
            self.passed_scenarios += 1
        if unique_failures > self.unique_failures:
            self.last_failure_time = time.monotonic()
        self.unique_failures = unique_failures

    def update_error_count(self, error_count: int) -> None:
        self.stats[Status.ERROR] = error_count


def _has_passed(event: FuzzScenarioFinished) -> bool:
    # Transport errors leave steps unanswered without changing the scenario status.
    if event.status != Status.SUCCESS:
        return False
    recorder = event.recorder
    return all(recorder.find_response(case_id=case_id) is not None for case_id in recorder.cases)


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
            self._on_loading_started(ctx, event)
        elif isinstance(event, LoadingFinished):
            self._finish_loading()
            if ctx.initialization_lines:
                print_lines(ctx.initialization_lines)
        elif isinstance(event, EngineStarted):
            self.progress_manager.start()
        elif isinstance(event, FuzzScenarioFinished):
            self.progress_manager.update(
                unique_failures=len(ctx.statistic.unique_failures_map), passed=_has_passed(event)
            )
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
        from rich.text import Text

        is_interrupted = event.stop_reason is StopReason.INTERRUPTED
        without_checks = ctx.statistic.no_checks_ran and not event.failures
        message = self.progress_manager.get_completion_message(
            is_interrupted=is_interrupted, without_checks=without_checks
        )
        self.console.print(Indent(Text(message, style="white")))
        self.console.print()

        if ctx.errors:
            display_errors(ctx.errors, base_url_is_set=ctx.base_url_is_set)

        display_failures(ctx.statistic, ctx.config.output, record_crashes=ctx.config.cache.enabled)

        unmatched_filter = ctx.warning_collector.data.unmatched_filter
        if unmatched_filter:
            display_warning_blocks([unmatched_filter_block(unmatched_filter)])

        display_section_name("SUMMARY")
        click.echo()

        summary = ctx.summary()

        if summary.operations is not None:
            display_api_operations(summary.operations)

        # The final line says why nothing was tested; a stop reason next to it would read as a normal run.
        if ctx.nothing_tested_reason is None:
            click.echo(f"Stop reason: {event.stop_reason.skip_explanation or 'Completed'}")
            click.echo()

        display_summary_counts(summary)
        display_summary_footer(ctx, summary)
        display_final_line(
            failures=ctx.statistic.total_failures,
            errors=len(ctx.errors),
            warnings=ctx.warning_collector.data.kind_count,
            running_time=event.running_time,
            total_cases=ctx.statistic.total_cases,
            nothing_tested_reason=ctx.nothing_tested_reason,
            exit_code=ctx.exit_code,
            no_checks_ran=ctx.statistic.no_checks_ran,
            interrupted=event.stop_reason == StopReason.INTERRUPTED,
        )
