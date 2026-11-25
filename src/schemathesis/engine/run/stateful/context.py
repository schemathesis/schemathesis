from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from schemathesis.core.failures import Failure
from schemathesis.engine import Status
from schemathesis.engine.link_calibration import MIN_SAMPLES
from schemathesis.generation.metrics import MetricCollector

if TYPE_CHECKING:
    from schemathesis.schemas import APIOperation

# Refusals of one identical request in a row, all with the same status, that mark it a dead end.
# A link is judged after as many answers, so a dead end never hides one from that judgement.
DEAD_END_SENDS = MIN_SAMPLES


@dataclass
class StatefulContext:
    # All seen failure keys, both grouped and individual ones
    seen_in_run: set[Failure] = field(default_factory=set)
    seen_in_suite: set[Failure] = field(default_factory=set)
    # Whether the current suite saw a failure outside the baseline
    has_new_failures_in_suite: bool = False
    current_step_status: Status | None = None
    completed_scenarios: int = 0
    metric_collector: MetricCollector = field(default_factory=MetricCollector)
    # Inputs that already passed in the current scenario
    passed_steps: set[int] = field(default_factory=set)
    # Operations whose skipped timed-out steps are already reported
    reported_timeouts: set[str] = field(default_factory=set)
    # Operations whose data cannot be generated, by label; later scenarios leave them out
    unbuildable_operations: dict[str, tuple[APIOperation, Exception]] = field(default_factory=dict)
    # Labels of those operations that are already reported
    reported_unbuildable: set[str] = field(default_factory=set)
    # Per identical request, across the whole run: the status of its latest refusals and how many came in a row.
    refusals: dict[tuple[str, int], tuple[int, int]] = field(default_factory=dict)

    def reset_scenario(self) -> None:
        self.completed_scenarios += 1
        self.current_step_status = None
        self.passed_steps.clear()

    def mark_timeout_reported(self, operation_label: str) -> bool:
        """`True` only the first time an operation's timeout is reported."""
        if operation_label in self.reported_timeouts:
            return False
        self.reported_timeouts.add(operation_label)
        return True

    def take_unreported_unbuildable(self) -> list[tuple[APIOperation, Exception]]:
        """Operations whose data cannot be generated and that are not reported yet, each returned once."""
        unreported = [
            entry for label, entry in self.unbuildable_operations.items() if label not in self.reported_unbuildable
        ]
        self.reported_unbuildable.update(self.unbuildable_operations)
        return unreported

    def reset(self) -> None:
        self.seen_in_suite.clear()
        self.has_new_failures_in_suite = False
        self.reset_scenario()
        self.metric_collector.reset()

    def record_request_outcome(self, key: tuple[str, int], status_code: int) -> None:
        # Rate limiting and unavailability pass with time, whatever the request carries.
        if status_code in (429, 503):
            return
        if status_code < 400:
            self.refusals.pop(key, None)
            return
        recorded = self.refusals.get(key)
        if recorded is not None and recorded[0] == status_code:
            self.refusals[key] = (status_code, recorded[1] + 1)
        else:
            self.refusals[key] = (status_code, 1)

    def is_dead_end(self, key: tuple[str, int]) -> bool:
        """Whether the API refused the latest sends of the request with the same error every time."""
        recorded = self.refusals.get(key)
        # An operation that answers `405` is dropped as a whole once enough of its requests got that answer.
        return recorded is not None and recorded[1] >= DEAD_END_SENDS and recorded[0] != 405
