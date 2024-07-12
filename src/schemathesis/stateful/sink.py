from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from . import events
from .statistic import TransitionStats

if TYPE_CHECKING:
    from ..models import Check


@dataclass
class AverageResponseTime:
    """Average response time for a given status code.

    Stored as a sum of all response times and a count of responses.
    """

    total: float
    count: int

    __slots__ = ("total", "count")

    def __init__(self) -> None:
        self.total = 0.0
        self.count = 0


@dataclass
class StateMachineSink:
    """Collects events and stores data about the state machine execution."""

    transitions: TransitionStats
    response_times: dict[str, dict[int, AverageResponseTime]] = field(default_factory=dict)
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
            responses = self.response_times.setdefault(event.target, {})
            if event.response is not None:
                average = responses.setdefault(event.response.status_code, AverageResponseTime())
                average.total += event.response.elapsed.total_seconds()
                average.count += 1
        elif isinstance(event, events.ScenarioFinished):
            self.scenarios[event.status] += 1
        elif isinstance(event, events.SuiteFinished):
            self.suites[event.status] += 1
            self.failures.extend(event.failures)
        elif isinstance(event, events.RunFinished):
            self.end_time = event.timestamp

    @property
    def duration(self) -> float | None:
        if self.start_time is not None and self.end_time is not None:
            return self.end_time - self.start_time
        return None
