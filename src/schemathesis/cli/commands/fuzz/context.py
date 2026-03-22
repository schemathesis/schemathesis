from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from schemathesis.cli.context import BaseExecutionContext
from schemathesis.cli.events import LoadingFinished
from schemathesis.engine import Status, events
from schemathesis.engine.events import FuzzScenarioFinished

if TYPE_CHECKING:
    from schemathesis.schemas import ApiStatistic


@dataclass
class FuzzExecutionContext(BaseExecutionContext):
    """Execution state for `st fuzz`."""

    api_statistic: ApiStatistic | None = None
    errors: set[events.NonFatalError] = field(default_factory=set)

    def on_event(self, event: events.EngineEvent) -> None:
        super().on_event(event)
        if isinstance(event, LoadingFinished):
            self.api_statistic = event.statistic
        elif isinstance(event, FuzzScenarioFinished):
            self.statistic.on_scenario_finished(event.recorder, failure_label=lambda case: case.operation.label)
            if event.status in (Status.FAILURE, Status.ERROR):
                self.exit_code = 1
        elif isinstance(event, events.NonFatalError):
            self.errors.add(event)
            self.exit_code = 1
