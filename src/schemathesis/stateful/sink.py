from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from . import events

if TYPE_CHECKING:
    from ..runner.models import Check
    from .statistic import TransitionStats


@dataclass
class StateMachineSink:
    """Collects events and stores data about the state machine execution."""

    transitions: TransitionStats
    steps: dict[events.StepStatus, int] = field(default_factory=lambda: {status: 0 for status in events.StepStatus})
    scenarios: dict[events.ScenarioStatus, int] = field(
        default_factory=lambda: {status: 0 for status in events.ScenarioStatus}
    )
    suites: dict[events.SuiteStatus, int] = field(default_factory=lambda: {status: 0 for status in events.SuiteStatus})
    failures: list[Check] = field(default_factory=list)
    start_time: float | None = None
    end_time: float | None = None

    def consume(self, event: events.StatefulEvent) -> None:
        self.transitions.consume(event)
        if isinstance(event, events.RunStarted):
            self.start_time = event.timestamp
        elif isinstance(event, events.StepFinished) and event.status is not None:
            self.steps[event.status] += 1
        elif isinstance(event, events.ScenarioFinished):
            self.scenarios[event.status] += 1
        elif isinstance(event, events.SuiteFinished):
            self.suites[event.status] += 1
            self.failures.extend(event.failures)
        elif isinstance(event, events.RunFinished):
            self.end_time = event.timestamp
