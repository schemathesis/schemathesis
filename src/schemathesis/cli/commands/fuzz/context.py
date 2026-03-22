from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from schemathesis.cli.events import LoadingFinished
from schemathesis.cli.statistics import Statistic
from schemathesis.engine import Status, events
from schemathesis.engine.events import FuzzScenarioFinished

if TYPE_CHECKING:
    from schemathesis.config import ProjectConfig
    from schemathesis.schemas import ApiStatistic


@dataclass
class FuzzExecutionContext:
    config: ProjectConfig
    statistic: Statistic = field(default_factory=Statistic)
    api_statistic: ApiStatistic | None = None
    exit_code: int = 0
    errors: set[events.NonFatalError] = field(default_factory=set)

    def on_event(self, event: events.EngineEvent) -> None:
        if isinstance(event, LoadingFinished):
            self.api_statistic = event.statistic
        elif isinstance(event, FuzzScenarioFinished):
            self.statistic.on_scenario_finished(event.recorder)
            if event.status in (Status.FAILURE, Status.ERROR):
                self.exit_code = 1
        elif isinstance(event, events.NonFatalError):
            self.errors.add(event)
