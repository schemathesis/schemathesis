"""Control for the Schemathesis Engine execution."""

from __future__ import annotations

import threading
import time
from collections.abc import Hashable
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from schemathesis.engine import StopReason


@dataclass(slots=True)
class ExecutionControl:
    """Controls engine execution flow and tracks failures."""

    stop_event: threading.Event
    max_failures: int | None
    max_time: int | None
    _counted_failures: set[Hashable]
    has_reached_the_failure_limit: bool
    _start_time: float

    def __init__(
        self,
        stop_event: threading.Event,
        max_failures: int | None,
        max_time: int | None = None,
        start_time: float | None = None,
    ) -> None:
        self.stop_event = stop_event
        self.max_failures = max_failures
        self.max_time = max_time
        self._counted_failures = set()
        self.has_reached_the_failure_limit = False
        self._start_time = time.monotonic() if start_time is None else start_time

    @property
    def deadline(self) -> float | None:
        """Monotonic instant the run must stop at; `None` when unbounded."""
        if self.max_time is None:
            return None
        return self._start_time + self.max_time

    @property
    def remaining_time(self) -> float | None:
        """Seconds left before the run must stop; `None` when unbounded."""
        deadline = self.deadline
        if deadline is None:
            return None
        return max(deadline - time.monotonic(), 0.0)

    @property
    def has_reached_time_limit(self) -> bool:
        if self.max_time is None:
            return False
        return time.monotonic() - self._start_time >= self.max_time

    @property
    def is_stopped(self) -> bool:
        """Check if execution should stop."""
        return self.is_interrupted or self.has_reached_the_failure_limit or self.has_reached_time_limit

    @property
    def is_interrupted(self) -> bool:
        return self.stop_event.is_set()

    def stop(self) -> None:
        """Signal to stop execution."""
        self.stop_event.set()

    def count_failure(self, key: Hashable) -> None:
        """Count one failure towards the limit; a repeat under a time budget must not spend it twice."""
        if self.max_failures is not None:
            self._counted_failures.add(key)
            if len(self._counted_failures) >= self.max_failures:
                self.has_reached_the_failure_limit = True

    @property
    def stop_reason(self) -> StopReason:
        from schemathesis.engine import StopReason

        # A user stop wins over the clock: an interrupt that lands past the deadline is still an interrupt.
        if self.is_interrupted:
            return StopReason.INTERRUPTED
        if self.has_reached_time_limit:
            return StopReason.MAX_TIME
        if self.has_reached_the_failure_limit:
            return StopReason.FAILURE_LIMIT
        return StopReason.COMPLETED
