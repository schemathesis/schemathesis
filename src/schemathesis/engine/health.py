from __future__ import annotations

import threading
from collections import deque
from dataclasses import dataclass

# Below this many observations, the success/failure ratio is too noisy to act on.
MIN_SAMPLES = 3
DEFAULT_USE_PROBABILITY = 1.0
# Floor selection probability so a recovered op can re-enter the schedule.
MIN_USE_PROBABILITY = 0.05
TIGHTEN_AFTER_FAILURES = 2
TIGHTENED_TIMEOUT_SECONDS = 1.0
ADAPTIVE_TIMEOUT_MULTIPLIER = 4.0
PHASE_FATAL_FAILURES = 3
UNRESPONSIVE_MIN_FAILURES = 10
UNRESPONSIVE_FAILURE_RATIO = 0.75
PHASE_FATAL_WINDOW_SECONDS = 30.0


@dataclass(slots=True)
class OperationHealth:
    """Per-operation completion vs transport-failure bookkeeping.

    `use_probability` is the observed success ratio clamped to a floor;
    below `MIN_SAMPLES` total observations, returns `DEFAULT_USE_PROBABILITY`.
    """

    completed: int = 0
    transport_failures: int = 0
    # Slowest answered request, in seconds; `None` until a request with known duration is answered.
    slowest_completion: float | None = None
    first_failure_time: float | None = None
    unresponsive: bool = False

    @property
    def use_probability(self) -> float:
        total = self.completed + self.transport_failures
        if total < MIN_SAMPLES:
            return DEFAULT_USE_PROBABILITY
        return max(MIN_USE_PROBABILITY, self.completed / total)


class HealthState:
    """Per-operation answers and transport failures, shared by every phase.

    Mutations are lock-guarded; reads run lock-free on the scheduler hot path
    and may observe slightly stale snapshots but never torn state.
    """

    __slots__ = (
        "operations",
        "_last_answer_time",
        "_first_failure_time",
        "_recent_failures",
        "_frozen_use_probability",
        "_lock",
    )

    def __init__(self) -> None:
        self.operations: dict[str, OperationHealth] = {}
        self._last_answer_time: float | None = None
        self._first_failure_time: float | None = None
        # Transport failures as `(operation label, time)`, oldest first; pruned to the abort window.
        self._recent_failures: deque[tuple[str, float]] = deque()
        # Per-run snapshot of use-probabilities; stays stable across a Hypothesis replay so generation
        # is reproducible. Refreshed at suite boundaries by `begin_iteration`; `operations` stays live.
        self._frozen_use_probability: dict[str, float] = {}
        self._lock = threading.Lock()

    def begin_iteration(self) -> None:
        """Refresh the per-run use-probability snapshot at a suite boundary."""
        with self._lock:
            self._frozen_use_probability = {label: h.use_probability for label, h in self.operations.items()}

    def record_completion(self, *, operation_label: str, now: float, elapsed: float | None = None) -> None:
        with self._lock:
            health = self.operations.setdefault(operation_label, OperationHealth())
            health.completed += 1
            if elapsed is not None and (health.slowest_completion is None or elapsed > health.slowest_completion):
                health.slowest_completion = elapsed
            self._last_answer_time = now

    def record_transport_failure(self, *, operation_label: str, now: float) -> None:
        with self._lock:
            health = self.operations.setdefault(operation_label, OperationHealth())
            health.transport_failures += 1
            if health.first_failure_time is None:
                health.first_failure_time = now
            if self._first_failure_time is None:
                self._first_failure_time = now
            self._recent_failures.append((operation_label, now))
            self._prune(now)

    def frozen_use_probability(self, operation_label: str) -> float:
        return self._frozen_use_probability.get(operation_label, DEFAULT_USE_PROBABILITY)

    def timeout_override(self, operation_label: str) -> float | None:
        health = self.operations.get(operation_label)
        if health is None or health.transport_failures < TIGHTEN_AFTER_FAILURES:
            return None
        # Once an operation keeps hanging, wait only as long as its answers ever took; hanging inputs stay cheap.
        if health.slowest_completion is not None:
            return max(TIGHTENED_TIMEOUT_SECONDS, ADAPTIVE_TIMEOUT_MULTIPLIER * health.slowest_completion)
        return TIGHTENED_TIMEOUT_SECONDS

    def mark_unresponsive(self, operation_label: str) -> bool:
        """Flag an operation whose requests mostly hang while the API keeps answering; `True` only the first time."""
        with self._lock:
            health = self.operations.get(operation_label)
            if health is None or health.unresponsive or health.first_failure_time is None:
                return False
            total = health.completed + health.transport_failures
            if (
                health.transport_failures < UNRESPONSIVE_MIN_FAILURES
                or health.transport_failures / total < UNRESPONSIVE_FAILURE_RATIO
                or self._last_answer_time is None
                or self._last_answer_time <= health.first_failure_time
            ):
                return False
            health.unresponsive = True
            return True

    def is_unresponsive(self, operation_label: str) -> bool:
        health = self.operations.get(operation_label)
        return health is not None and health.unresponsive

    def _prune(self, now: float) -> None:
        while self._recent_failures and now - self._recent_failures[0][1] >= PHASE_FATAL_WINDOW_SECONDS:
            self._recent_failures.popleft()

    def abort_reason(self, *, now: float) -> str | None:
        """Why the API counts as no longer responding, or `None` while it still answers something."""
        with self._lock:
            silent_since = self._last_answer_time if self._last_answer_time is not None else self._first_failure_time
            if silent_since is None or now - silent_since < PHASE_FATAL_WINDOW_SECONDS:
                return None
            self._prune(now)
            if len(self._recent_failures) < PHASE_FATAL_FAILURES:
                return None
            silence = now - silent_since
            failures = len(self._recent_failures)
            per_operation: dict[str, tuple[int, float]] = {}
            for label, failed_at in self._recent_failures:
                count, _ = per_operation.get(label, (0, failed_at))
                per_operation[label] = (count + 1, failed_at)
        lines = [
            f"API stopped responding: no answers for {silence:.1f}s, "
            f"{failures} requests failed in the last {PHASE_FATAL_WINDOW_SECONDS:.0f}s"
        ]
        for label, (count, failed_at) in per_operation.items():
            lines.append(f"  - {label} ({count} failed, last {now - failed_at:.1f}s ago)")
        return "\n".join(lines)
