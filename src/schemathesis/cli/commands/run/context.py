from __future__ import annotations

from dataclasses import dataclass, field

from schemathesis.cli.commands.run.warnings import WarningCollector
from schemathesis.cli.context import BaseExecutionContext
from schemathesis.cli.events import LoadingFinished
from schemathesis.cli.summary import SummaryData, WarningData
from schemathesis.core.failures import RUN_CHECKS_LABEL
from schemathesis.core.statistic import ApiStatistic
from schemathesis.engine import Status, StopReason, events
from schemathesis.engine.run import PhaseName, PhaseSkipReason


@dataclass
class ExecutionContext(BaseExecutionContext):
    """Execution state for `st run`."""

    api_statistic: ApiStatistic | None = None
    errors: set[events.NonFatalError] = field(default_factory=set)
    phases: dict[PhaseName, tuple[Status, PhaseSkipReason | None]] = field(
        default_factory=lambda: dict.fromkeys(PhaseName, (Status.SKIP, None))
    )
    # Keyed by operation label - a reason only applies to the operation it came from.
    skip_reasons: dict[str, set[str]] = field(default_factory=dict)
    stop_reason: StopReason = StopReason.INTERRUPTED
    warning_collector: WarningCollector | None = None

    def __post_init__(self) -> None:
        self.warning_collector = WarningCollector(config=self.config)

    def on_event(self, event: events.EngineEvent) -> None:
        super().on_event(event)
        collector = self.warning_collector
        assert collector is not None
        if isinstance(event, LoadingFinished):
            self.api_statistic = event.statistic
            self.config = event.config
            collector.config = event.config
            collector.on_unmatched_filters(self, event.statistic)
        elif isinstance(event, events.SchemaAnalysisWarnings):
            collector.on_schema_warnings(self, event)
        elif isinstance(event, events.PhaseFinished):
            self.phases[event.phase.name] = (event.status, event.phase.skip_reason)
        elif isinstance(event, events.ScenarioFinished):
            self.statistic.on_scenario_finished(event.recorder)
            collector.on_scenario_finished(self, event)
            if (
                event.phase in (PhaseName.EXAMPLES, PhaseName.COVERAGE, PhaseName.FUZZING)
                and event.status == Status.SKIP
                and event.skip_reason is not None
                and event.label
            ):
                self.skip_reasons.setdefault(event.label, set()).add(event.skip_reason)
        elif isinstance(event, events.EngineFinished):
            self.stop_reason = event.stop_reason
            # after_run failures arrive here.
            if event.failures:
                self.statistic.record_run_check_failures(event.failures, label=RUN_CHECKS_LABEL)
                self.exit_code = 1
        if isinstance(event, events.NonFatalError):
            self.errors.add(event)
        if isinstance(event, events.NonFatalError) or (
            isinstance(event, events.PhaseFinished)
            and event.phase.is_enabled
            and event.status in (Status.FAILURE, Status.ERROR)
        ):
            self.exit_code = 1

    @property
    def warnings(self) -> WarningData:
        return self.warning_collector.data if self.warning_collector is not None else WarningData()

    def summary(self) -> SummaryData:
        return SummaryData.from_run(
            api_statistic=self.api_statistic,
            statistic=self.statistic,
            errors=self.errors,
            phases=self.phases,
            skip_reasons=self.skip_reasons,
            stop_reason=self.stop_reason,
            warnings=self.warnings,
        )
