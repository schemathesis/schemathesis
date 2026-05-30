from __future__ import annotations

import threading
from dataclasses import dataclass

# Below this many observations, the success/failure ratio is too noisy to act on.
MIN_SAMPLES = 3
DEFAULT_USE_PROBABILITY = 1.0
# Floor selection probability so a recovered op can re-enter the schedule.
MIN_USE_PROBABILITY = 0.05
TIGHTEN_AFTER_FAILURES = 2
TIGHTENED_TIMEOUT_SECONDS = 1.0
PHASE_FATAL_DISTINCT_OPERATIONS = 3
PHASE_FATAL_WINDOW_SECONDS = 30.0


@dataclass(slots=True)
class OperationHealth:
    """Per-operation completion vs transport-failure bookkeeping.

    `use_probability` is the observed success ratio clamped to a floor;
    below `MIN_SAMPLES` total observations, returns `DEFAULT_USE_PROBABILITY`.
    """

    completed: int = 0
    transport_failures: int = 0
    consecutive_failures: int = 0
    last_failure_time: float | None = None

    @property
    def use_probability(self) -> float:
        total = self.completed + self.transport_failures
        if total < MIN_SAMPLES:
            return DEFAULT_USE_PROBABILITY
        return max(MIN_USE_PROBABILITY, self.completed / total)


class HealthState:
    """Per-operation transport-failure tracking for stateful-phase recovery.

    Mutations are lock-guarded; reads run lock-free on the scheduler hot path
    and may observe slightly stale snapshots but never torn state.
    """

    __slots__ = ("operations", "_frozen_use_probability", "_lock")

    def __init__(self) -> None:
        self.operations: dict[str, OperationHealth] = {}
        # Per-run snapshot of use-probabilities; stays stable across a Hypothesis replay so generation
        # is reproducible. Refreshed at suite boundaries by `begin_iteration`; `operations` stays live.
        self._frozen_use_probability: dict[str, float] = {}
        self._lock = threading.Lock()

    def begin_iteration(self) -> None:
        """Refresh the per-run use-probability snapshot at a suite boundary."""
        with self._lock:
            self._frozen_use_probability = {label: h.use_probability for label, h in self.operations.items()}

    def record_completion(self, *, operation_label: str) -> None:
        with self._lock:
            health = self.operations.setdefault(operation_label, OperationHealth())
            health.completed += 1
            health.consecutive_failures = 0
            health.last_failure_time = None

    def record_transport_failure(self, *, operation_label: str, now: float) -> None:
        with self._lock:
            health = self.operations.setdefault(operation_label, OperationHealth())
            health.transport_failures += 1
            health.consecutive_failures += 1
            health.last_failure_time = now

    def frozen_use_probability(self, operation_label: str) -> float:
        return self._frozen_use_probability.get(operation_label, DEFAULT_USE_PROBABILITY)

    def timeout_override(self, operation_label: str) -> float | None:
        health = self.operations.get(operation_label)
        if health is None or health.consecutive_failures < TIGHTEN_AFTER_FAILURES:
            return None
        return TIGHTENED_TIMEOUT_SECONDS

    def abort_reason(self, *, now: float) -> str | None:
        with self._lock:
            offending: list[tuple[str, float]] = []
            for label, health in self.operations.items():
                last_failure_time = health.last_failure_time
                if last_failure_time is None:
                    continue
                seconds_ago = now - last_failure_time
                if seconds_ago < PHASE_FATAL_WINDOW_SECONDS:
                    offending.append((label, seconds_ago))
        if len(offending) < PHASE_FATAL_DISTINCT_OPERATIONS:
            return None
        lines = [
            f"API appears unhealthy: {len(offending)} operations had transport failures "
            f"within the last {PHASE_FATAL_WINDOW_SECONDS:.0f}s"
        ]
        for label, seconds_ago in offending:
            lines.append(f"  - {label} (last failure {seconds_ago:.1f}s ago)")
        return "\n".join(lines)
