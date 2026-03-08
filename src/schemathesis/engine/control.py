"""Control for the Schemathesis Engine execution."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from schemathesis.engine import StopReason


@dataclass
class ExecutionControl:
    """Controls engine execution flow and tracks failures."""

    stop_event: threading.Event
    max_failures: int | None
    max_time: int | None
    _failures_counter: int
    has_reached_the_failure_limit: bool
    _start_time: float

    __slots__ = (
        "stop_event",
        "max_failures",
        "max_time",
        "_failures_counter",
        "has_reached_the_failure_limit",
        "_start_time",
    )

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
        self._failures_counter = 0
        self.has_reached_the_failure_limit = False
        self._start_time = time.monotonic() if start_time is None else start_time

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

    def count_failure(self) -> None:
        # N failures limit
        if self.max_failures is not None:
            self._failures_counter += 1
            if self._failures_counter >= self.max_failures:
                self.has_reached_the_failure_limit = True

    @property
    def stop_reason(self) -> StopReason:
        from schemathesis.engine import StopReason

        if self.has_reached_time_limit:
            return StopReason.MAX_TIME
        if self.has_reached_the_failure_limit:
            return StopReason.FAILURE_LIMIT
        if self.is_interrupted:
            return StopReason.INTERRUPTED
        return StopReason.COMPLETED
