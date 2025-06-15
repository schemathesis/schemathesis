"""Control for the Schemathesis Engine execution."""

from __future__ import annotations

import threading
from dataclasses import dataclass


@dataclass
class ExecutionControl:
    """Controls engine execution flow and tracks failures."""

    stop_event: threading.Event
    max_failures: int | None
    _failures_counter: int
    has_reached_the_failure_limit: bool

    __slots__ = ("stop_event", "max_failures", "_failures_counter", "has_reached_the_failure_limit")

    def __init__(self, stop_event: threading.Event, max_failures: int | None) -> None:
        self.stop_event = stop_event
        self.max_failures = max_failures
        self._failures_counter = 0
        self.has_reached_the_failure_limit = False

    @property
    def is_stopped(self) -> bool:
        """Check if execution should stop."""
        return self.is_interrupted or self.has_reached_the_failure_limit

    @property
    def is_interrupted(self) -> bool:
        return self.stop_event.is_set()

    def stop(self) -> None:
        """Signal to stop execution."""
        self.stop_event.set()

    def count_failure(self) -> None:
        # N failures limit
        if self.max_failures is not None:
            self._failures_counter += 1
            if self._failures_counter >= self.max_failures:
                self.has_reached_the_failure_limit = True
