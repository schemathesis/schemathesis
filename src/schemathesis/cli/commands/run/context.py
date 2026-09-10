from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from schemathesis.baseline import Baseline, BaselineEntry
from schemathesis.cli.commands.run.warnings import WarningCollector
from schemathesis.cli.context import BaseExecutionContext
from schemathesis.cli.events import LoadingFinished
from schemathesis.cli.summary import SummaryData, WarningData
from schemathesis.core.failures import RUN_CHECKS_LABEL, is_reproducible_failure
from schemathesis.core.statistic import ApiStatistic
from schemathesis.engine import Status, StopReason, events
from schemathesis.engine.run import PhaseName, PhaseSkipReason

# How telling each outcome is about a phase that ran several times: the summary keeps the highest.
PHASE_STATUS_PRIORITY = {
    Status.SKIP: 0,
    Status.SUCCESS: 1,
    Status.INTERRUPTED: 2,
    Status.FAILURE: 3,
    Status.ERROR: 4,
}


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
    baseline_update: bool = False
    baseline_prune: bool = False
    baseline_recorded: int | None = None
    baseline_pruned: list[str] | None = None

    def __post_init__(self) -> None:
        super().__post_init__()
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
            # A phase repeats under a time budget; a later pass that ran out of budget or was cut short
            # must not downgrade what an earlier one already found.
            current, _ = self.phases[event.phase.name]
            if PHASE_STATUS_PRIORITY[event.status] >= PHASE_STATUS_PRIORITY[current]:
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
            self._write_baseline()
        if isinstance(event, events.NonFatalError):
            self.errors.add(event)
        if isinstance(event, events.NonFatalError) or (
            isinstance(event, events.PhaseFinished)
            and event.phase.is_enabled
            and event.status in (Status.FAILURE, Status.ERROR)
        ):
            self.exit_code = 1

    def _write_baseline(self) -> None:
        if self.config.baseline is None:
            return
        path = Path(self.config.baseline)
        # A baseline that does not exist yet is created from this run, the way a lockfile is.
        creating = not path.exists()
        if not (creating or self.baseline_update or self.baseline_prune):
            return
        loaded = self.statistic.baseline or Baseline(entries=[])
        # Work off a copy so the summary still reports against the baseline as the run loaded it.
        entries = list(loaded.entries)
        if self.baseline_prune:
            observed = set(self.statistic.known_failures.values())
            kept = [
                entry
                for entry in entries
                if entry.id in observed or entry.operation not in self.statistic.tested_operations
            ]
            dropped = {entry.id for entry in entries} - {entry.id for entry in kept}
            self.baseline_pruned = sorted(dropped)
            entries = kept
        if self.baseline_update or creating:
            today = date.today().isoformat()
            existing = {entry.identity: entry for entry in entries}
            seen = set(existing)
            self.baseline_recorded = 0
            for failure, check in self.statistic.observed_failures.values():
                # Response time flaps with machine load, so an entry for it would never settle.
                if not is_reproducible_failure(failure):
                    continue
                entry = BaselineEntry.from_failure(failure, check=check)
                seen_before = existing.get(entry.identity)
                if seen_before is not None:
                    seen_before.last_seen = today
                if entry.identity not in seen:
                    entries.append(entry)
                    seen.add(entry.identity)
                    self.baseline_recorded += 1
        Baseline(entries=entries).save(path)

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
            baseline_recorded=self.baseline_recorded,
            baseline_pruned=self.baseline_pruned,
        )
