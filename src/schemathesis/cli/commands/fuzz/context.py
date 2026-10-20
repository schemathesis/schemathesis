from __future__ import annotations

from dataclasses import dataclass, field

from schemathesis.cli.commands.run.warnings import WarningCollector
from schemathesis.cli.constants import ExitCode
from schemathesis.cli.context import BaseExecutionContext
from schemathesis.cli.events import LoadingFinished
from schemathesis.cli.summary import (
    SummaryData,
    build_operations,
    reduce_baseline,
    reduce_errors,
    reduce_failures,
    reduce_test_cases,
)
from schemathesis.core.failures import RUN_CHECKS_LABEL
from schemathesis.engine import Status, StopReason, events
from schemathesis.engine.events import FuzzScenarioFinished


@dataclass
class FuzzExecutionContext(BaseExecutionContext):
    """Execution state for `st fuzz`."""

    errors: set[events.NonFatalError] = field(default_factory=set)
    skip_reasons: dict[str, set[str]] = field(default_factory=dict)
    stop_reason: StopReason = StopReason.COMPLETED
    warning_collector: WarningCollector = field(init=False)

    def __post_init__(self) -> None:
        super().__post_init__()
        self.warning_collector = WarningCollector(config=self.config)

    def summary(self) -> SummaryData:
        # `st fuzz` has no phases, so the map stays empty and the report reports none.
        return SummaryData(
            operations=build_operations(
                api_statistic=self.api_statistic,
                statistic=self.statistic,
                errors=self.errors,
                skip_reasons=self.skip_reasons,
                stop_reason=self.stop_reason,
            ),
            phases={},
            test_cases=reduce_test_cases(self.statistic),
            failures=reduce_failures(self.statistic),
            errors=reduce_errors(self.errors),
            warnings=self.warning_collector.data,
            baseline=reduce_baseline(self.statistic),
            filtered=self.statistic.filtered_failures,
        )

    def on_event(self, event: events.EngineEvent) -> None:
        super().on_event(event)
        if isinstance(event, LoadingFinished):
            self.warning_collector.on_unmatched_filters(event.statistic)
        elif isinstance(event, FuzzScenarioFinished):
            self.statistic.on_scenario_finished(event.recorder, failure_label=lambda case: case.operation.label)
            if event.status in (Status.FAILURE, Status.ERROR):
                self.exit_code = ExitCode.FAILURES
        elif isinstance(event, events.ScenarioFinished) and event.label and event.skip_reason is not None:
            self.skip_reasons.setdefault(event.label, set()).add(event.skip_reason)
        elif isinstance(event, events.EngineFinished):
            self.stop_reason = event.stop_reason
            # after_run failures arrive here.
            if event.failures:
                self.statistic.record_run_check_failures(event.failures, label=RUN_CHECKS_LABEL)
                self.exit_code = ExitCode.FAILURES
            if self.warning_collector.fails_the_run() and self.exit_code == ExitCode.OK:
                self.exit_code = ExitCode.FAILURES
            self.on_engine_finished(event.stop_reason)
        elif isinstance(event, events.NonFatalError):
            self.errors.add(event)
            self.exit_code = ExitCode.FAILURES
