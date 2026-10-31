from __future__ import annotations

from dataclasses import dataclass, field

from schemathesis.core.failures import Failure
from schemathesis.engine import Status
from schemathesis.generation.metrics import MetricCollector


@dataclass
class StatefulContext:
    """Mutable context for state machine execution."""

    # All seen failure keys, both grouped and individual ones
    seen_in_run: set[Failure] = field(default_factory=set)
    # Failures keys seen in the current suite
    seen_in_suite: set[Failure] = field(default_factory=set)
    # Whether the current suite saw a failure outside the baseline
    has_new_failures_in_suite: bool = False
    # Status of the current step
    current_step_status: Status | None = None
    # The total number of completed test scenario
    completed_scenarios: int = 0
    # Metrics collector for targeted testing
    metric_collector: MetricCollector = field(default_factory=MetricCollector)
    # Inputs that already passed in the current scenario
    passed_steps: set[int] = field(default_factory=set)
    # Operations whose skipped timed-out steps are already reported
    reported_timeouts: set[str] = field(default_factory=set)

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

    def reset(self) -> None:
        self.seen_in_suite.clear()
        self.has_new_failures_in_suite = False
        self.reset_scenario()
        self.metric_collector.reset()
