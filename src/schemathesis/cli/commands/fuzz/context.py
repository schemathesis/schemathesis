from __future__ import annotations

from dataclasses import dataclass, field

from schemathesis.cli.constants import ExitCode
from schemathesis.cli.context import BaseExecutionContext
from schemathesis.cli.summary import (
    SummaryData,
    WarningData,
    build_operations,
    reduce_baseline,
    reduce_errors,
    reduce_failures,
    reduce_test_cases,
)
from schemathesis.core.failures import RUN_CHECKS_LABEL
from schemathesis.engine import Status, events
from schemathesis.engine.events import FuzzScenarioFinished


@dataclass
class FuzzExecutionContext(BaseExecutionContext):
    """Execution state for `st fuzz`."""

    errors: set[events.NonFatalError] = field(default_factory=set)

    def summary(self) -> SummaryData:
        # `st fuzz` has no phases, so the map stays empty and the report reports none.
        return SummaryData(
            operations=build_operations(api_statistic=self.api_statistic, statistic=self.statistic, errors=self.errors),
            phases={},
            test_cases=reduce_test_cases(self.statistic),
            failures=reduce_failures(self.statistic),
            errors=reduce_errors(self.errors),
            warnings=WarningData(),
            baseline=reduce_baseline(self.statistic),
            filtered=self.statistic.filtered_failures,
        )

    def on_event(self, event: events.EngineEvent) -> None:
        super().on_event(event)
        if isinstance(event, FuzzScenarioFinished):
            self.statistic.on_scenario_finished(event.recorder, failure_label=lambda case: case.operation.label)
            if event.status in (Status.FAILURE, Status.ERROR):
                self.exit_code = ExitCode.FAILURES
        elif isinstance(event, events.EngineFinished):
            # after_run failures arrive here.
            if event.failures:
                self.statistic.record_run_check_failures(event.failures, label=RUN_CHECKS_LABEL)
                self.exit_code = ExitCode.FAILURES
            self.on_engine_finished(event.stop_reason)
        elif isinstance(event, events.NonFatalError):
            self.errors.add(event)
            self.exit_code = ExitCode.FAILURES
