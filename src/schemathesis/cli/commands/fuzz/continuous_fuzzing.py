from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

from schemathesis.cli.output import format_duration
from schemathesis.engine import Status

if TYPE_CHECKING:
    from rich.console import Console
    from rich.live import Live
    from rich.progress import Progress, TaskID


@dataclass(slots=True, init=False)
class ContinuousFuzzingProgressManager:
    """Manages progress display for continuous fuzzing."""

    console: Console
    title: str
    total_operations: int
    scenarios: int
    start_time: float

    # Progress components
    title_progress: Progress
    stats: dict[Status, int]
    unique_failures: int
    non_fatal_errors: int
    last_unique_failure_timestamp: float | None
    stats_progress: Progress
    details_progress: Progress
    live: Live | None

    # Task IDs
    title_task_id: TaskID | None
    stats_task_id: TaskID
    details_task_id: TaskID

    is_interrupted: bool
    refresh_thread: threading.Thread | None
    refresh_stop_event: threading.Event

    def __init__(self, *, console: Console, title: str, total_operations: int) -> None:
        from rich.progress import Progress, SpinnerColumn, TextColumn
        from rich.style import Style

        self.console = console
        self.title = title
        self.total_operations = total_operations
        self.scenarios = 0
        self.start_time = time.monotonic()

        self.title_progress = Progress(
            TextColumn(""),
            SpinnerColumn("clock"),
            TextColumn("{task.description}", style=Style(color="white")),
            console=self.console,
        )
        self.title_task_id = None

        self.stats_progress = Progress(
            TextColumn("    "),
            TextColumn("{task.description}"),
            console=self.console,
        )
        self.stats_task_id = self.stats_progress.add_task("")
        self.details_progress = Progress(
            TextColumn("    "),
            TextColumn("{task.description}"),
            console=self.console,
        )
        self.details_task_id = self.details_progress.add_task("")
        self.stats = {
            Status.SUCCESS: 0,
            Status.FAILURE: 0,
            Status.SKIP: 0,
            Status.ERROR: 0,
            Status.INTERRUPTED: 0,
        }
        self.unique_failures = 0
        self.non_fatal_errors = 0
        self.last_unique_failure_timestamp = None
        self._update_stats_display()

        self.live = None
        self.is_interrupted = False
        self.refresh_thread = None
        self.refresh_stop_event = threading.Event()

    def _scenarios_per_second(self, *, now: float) -> float:
        if self.scenarios == 0:
            return 0.0
        elapsed = max(now - self.start_time, 1e-9)
        return self.scenarios / elapsed

    def _format_test_case_count(self) -> str:
        suffix = "" if self.scenarios == 1 else "s"
        return f"{self.scenarios} test case{suffix}"

    def _last_unique_failure_age(self, *, now: float) -> str:
        if self.last_unique_failure_timestamp is None:
            return "none yet"
        return format_duration(int((now - self.last_unique_failure_timestamp) * 1000))

    def _get_primary_stats_message(self) -> str:
        now = time.monotonic()
        parts = [
            self._format_test_case_count(),
            f"cases/s: {self._scenarios_per_second(now=now):.1f}",
        ]
        if self.unique_failures:
            suffix = "s" if self.unique_failures > 1 else ""
            parts.append(f"❌ {self.unique_failures} unique failure{suffix}")
        if self.non_fatal_errors:
            suffix = "s" if self.non_fatal_errors > 1 else ""
            parts.append(f"🚫 {self.non_fatal_errors} error{suffix}")
        return " · ".join(parts)

    def _get_secondary_stats_message(self) -> str:
        return f"Time since last unique failure: {self._last_unique_failure_age(now=time.monotonic())}"

    def _get_stats_message(self) -> str:
        return f"{self._get_primary_stats_message()}\n{self._get_secondary_stats_message()}"

    def _update_stats_display(self) -> None:
        self.stats_progress.update(self.stats_task_id, description=self._get_primary_stats_message())
        self.details_progress.update(self.details_task_id, description=self._get_secondary_stats_message())

    def start(self) -> None:
        from rich.console import Group
        from rich.live import Live

        group = Group(
            self.title_progress,
            self.stats_progress,
            self.details_progress,
        )
        self.live = Live(group, refresh_per_second=10, console=self.console, transient=True)
        self.live.start()
        self.title_task_id = self.title_progress.add_task(self.title)
        self.refresh_stop_event.clear()
        self.refresh_thread = threading.Thread(target=self._refresh_loop, daemon=True)
        self.refresh_thread.start()

    def _refresh_loop(self) -> None:
        while not self.refresh_stop_event.wait(0.2):
            self._update_stats_display()

    def update_stats(self, status: Status, *, label: str | None, unique_failures: int, non_fatal_errors: int) -> None:
        now = time.monotonic()
        self.scenarios += 1
        self.stats[status] += 1
        if unique_failures > self.unique_failures:
            self.last_unique_failure_timestamp = now
        self.unique_failures = unique_failures
        self.non_fatal_errors = non_fatal_errors
        self._update_stats_display()

    def update_non_fatal_errors(self, count: int) -> None:
        self.non_fatal_errors = count
        self._update_stats_display()

    def interrupt(self) -> None:
        self.is_interrupted = True
        if self.live:
            self.stop()

    def stop(self) -> None:
        self.refresh_stop_event.set()
        if self.refresh_thread is not None and self.refresh_thread.is_alive():
            self.refresh_thread.join(timeout=0.3)
        self.refresh_thread = None
        if self.live:
            self.live.stop()

    def _get_status_icon(self, default_icon: str = "🕛") -> str:
        if self.is_interrupted:
            icon = "⚡"
        elif self.non_fatal_errors > 0 or self.stats[Status.ERROR] > 0:
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
        duration = format_duration(int((time.monotonic() - self.start_time) * 1000))
        icon = self._get_status_icon(default_icon)
        details = [
            self._format_test_case_count(),
        ]
        if self.unique_failures:
            suffix = "s" if self.unique_failures > 1 else ""
            details.append(f"{self.unique_failures} unique failure{suffix}")
        if self.non_fatal_errors:
            suffix = "s" if self.non_fatal_errors > 1 else ""
            details.append(f"{self.non_fatal_errors} error{suffix}")
        if not self.unique_failures and not self.non_fatal_errors:
            details.append("no issues found")
        message = ", ".join(details)
        if self.is_interrupted:
            duration_message = f"interrupted after {duration}"
        else:
            duration_message = f"in {duration}"
        return f"{icon}  {self.title} ({duration_message})\n\n    {message}"
