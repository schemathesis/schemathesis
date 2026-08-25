from __future__ import annotations

from datetime import datetime, timezone
from time import perf_counter


class Instant:
    """A monotonic start point to measure elapsed time from, inspired by Rust's `Instant`."""

    __slots__ = ("start",)

    def __init__(self) -> None:
        self.start = perf_counter()

    @property
    def elapsed(self) -> float:
        return perf_counter() - self.start

    @property
    def elapsed_ms(self) -> int:
        return int(self.elapsed * 1000)


def format_timestamp(value: float) -> str:
    """UTC timestamp for report payloads, from a `time.time()` value."""
    return datetime.fromtimestamp(value, timezone.utc).isoformat()
