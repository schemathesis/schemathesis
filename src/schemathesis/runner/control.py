"""Control for the Schemathesis Engine execution."""

from __future__ import annotations

import threading
from dataclasses import dataclass

from . import events
from .models.status import Status


@dataclass
class ExecutionControl:
    """Controls test execution flow and tracks failures."""

    stop_event: threading.Event
    max_failures: int | None
    _failures_counter: int = 0
    _is_limit_hit: bool = False

    @property
    def is_stopped(self) -> bool:
        """Check if execution should stop."""
        return self.stop_event.is_set() or self._is_limit_hit

    def stop(self) -> None:
        """Signal to stop execution."""
        self.stop_event.set()

    def on_event(self, event: events.ExecutionEvent) -> bool:
        """Process event and update execution state."""
        if isinstance(event, events.Interrupted):
            # Explicit CTRL+C
            self.stop()
            return True

        if self._is_failure_event(event):
            # N failures limit
            if self.max_failures is not None:
                self._failures_counter += 1
                if self._failures_counter >= self.max_failures:
                    self._is_limit_hit = True
                    return True
        return False

    @property
    def remaining_failures(self) -> int | None:
        if self.max_failures is None:
            return None
        return self.max_failures - self._failures_counter

    def _is_failure_event(self, event: events.ExecutionEvent) -> bool:
        """Determine if event should count towards failure limit."""
        return (
            isinstance(event, events.AfterExecution) and event.status in (Status.error, Status.failure)
        ) or isinstance(event, events.InternalError)
