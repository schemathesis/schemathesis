from __future__ import annotations

from dataclasses import dataclass

from schemathesis.cli.context import BaseExecutionContext
from schemathesis.engine import Status, events


@dataclass
class ExecutionContext(BaseExecutionContext):
    """Execution state for `st run`."""

    def on_event(self, event: events.EngineEvent) -> None:
        super().on_event(event)
        if isinstance(event, events.ScenarioFinished):
            self.statistic.on_scenario_finished(event.recorder)
        elif isinstance(event, events.NonFatalError) or (
            isinstance(event, events.PhaseFinished)
            and event.phase.is_enabled
            and event.status in (Status.FAILURE, Status.ERROR)
        ):
            self.exit_code = 1
