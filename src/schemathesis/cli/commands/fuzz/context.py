from __future__ import annotations

from dataclasses import dataclass

from schemathesis.cli.constants import ExitCode
from schemathesis.cli.context import BaseExecutionContext
from schemathesis.engine import Status, StopReason, events
from schemathesis.engine.events import FuzzScenarioFinished
from schemathesis.engine.summary import SummaryData


@dataclass
class FuzzExecutionContext(BaseExecutionContext):
    """Execution state for `st fuzz`."""

    stop_reason: StopReason = StopReason.COMPLETED

    def summary(self) -> SummaryData:
        # `st fuzz` has no phases, so the map stays empty and the report reports none.
        return SummaryData.from_run(
            api_statistic=self.api_statistic,
            statistic=self.statistic,
            errors=self.errors,
            phases={},
            skip_reasons=self.skip_reasons,
            stop_reason=self.stop_reason,
            warnings=self.warnings,
            error_phases=None,
        )

    def on_event(self, event: events.EngineEvent) -> None:
        super().on_event(event)
        if isinstance(event, FuzzScenarioFinished):
            self.statistic.on_scenario_finished(event.recorder, failure_label=lambda case: case.operation.label)
            if event.status in (Status.FAILURE, Status.ERROR):
                self.exit_code = ExitCode.FAILURES
        elif isinstance(event, events.ScenarioFinished) and event.label and event.skip_reason is not None:
            self.skip_reasons.setdefault(event.label, set()).add(event.skip_reason)
