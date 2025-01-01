from __future__ import annotations

import shutil
from collections import Counter
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Generator, cast

from schemathesis.core.failures import Failure
from schemathesis.core.output import OutputConfig
from schemathesis.core.transport import Response
from schemathesis.runner import Status, events
from schemathesis.runner.events import NonFatalError
from schemathesis.runner.phases import PhaseName
from schemathesis.runner.recorder import ScenarioRecorder
from schemathesis.stateful.sink import StateMachineSink

if TYPE_CHECKING:
    import os

    import hypothesis


@dataclass
class Statistic:
    """Running statistics about test execution."""

    totals: dict[str, dict[str, int]]  # Per-check statistics
    outcomes: dict[Status, int]
    failures: dict[str, list[GroupedFailures]]

    __slots__ = ("totals", "outcomes", "failures")

    def __init__(self) -> None:
        self.totals = {}
        self.outcomes = {}
        self.failures = {}

    def record_outcome(self, status: Status) -> None:
        value = self.outcomes.setdefault(status, 0)
        self.outcomes[status] = value + 1

    def record_checks(self, recorder: ScenarioRecorder) -> None:
        """Update statistics and store failures from a new batch of checks."""
        failures = {}
        # Process all checks in a single pass
        for case_id in recorder.cases:
            checks = recorder.checks.get(case_id, [])
            for check in checks:
                response = recorder.interactions[case_id].response
                # Update totals
                totals = self.totals.setdefault(check.name, Counter())
                totals[check.status] += 1
                totals["total"] += 1

                # Collect failures
                if check.failure_info is not None:
                    if case_id not in failures:
                        failures[case_id] = GroupedFailures(
                            case_id=case_id,
                            code_sample=check.failure_info.code_sample,
                            failures=[],
                            response=response,
                        )
                    failures[case_id].failures.append(check.failure_info.failure)
        if failures:
            for group in failures.values():
                group.failures = sorted(set(group.failures))
            self.failures[recorder.label] = list(failures.values())


@dataclass
class GroupedFailures:
    """Represents failures grouped by case ID."""

    case_id: str
    code_sample: str
    failures: list[Failure]
    response: Response | None

    __slots__ = ("case_id", "code_sample", "failures", "response")


@dataclass
class ExecutionContext:
    """Storage for the current context of the execution."""

    hypothesis_settings: hypothesis.settings
    workers_num: int = 1
    rate_limit: str | None = None
    wait_for_schema: float | None = None
    operations_processed: int = 0
    # It is set in runtime, from the `Initialized` event
    operations_count: int | None = None
    seed: int | None = None
    current_line_length: int = 0
    terminal_size: os.terminal_size = field(default_factory=shutil.get_terminal_size)
    statistic: Statistic = field(default_factory=Statistic)
    errors: list[NonFatalError] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    cassette_path: str | None = None
    junit_xml_file: str | None = None
    output_config: OutputConfig = field(default_factory=OutputConfig)
    state_machine_sink: StateMachineSink | None = None
    initialization_lines: list[str | Generator[str, None, None]] = field(default_factory=list)
    summary_lines: list[str | Generator[str, None, None]] = field(default_factory=list)

    def add_initialization_line(self, line: str | Generator[str, None, None]) -> None:
        self.initialization_lines.append(line)

    def add_summary_line(self, line: str | Generator[str, None, None]) -> None:
        self.summary_lines.append(line)

    def on_event(self, event: events.EngineEvent) -> None:
        if isinstance(event, events.AfterExecution):
            self.statistic.record_outcome(event.status)
        if isinstance(event, events.AfterExecution) or (
            isinstance(event, events.ScenarioFinished) and not event.is_final
        ):
            self.operations_processed += 1
            self.statistic.record_checks(event.recorder)
        elif isinstance(event, events.Initialized):
            self.operations_count = cast(int, event.operations_count)  # INVARIANT: should not be `None`
            self.seed = event.seed
        elif isinstance(event, events.NonFatalError):
            self.errors.append(event)
        elif isinstance(event, events.Warning):
            self.warnings.append(event.message)
        elif isinstance(event, events.PhaseStarted):
            if event.phase.name == PhaseName.STATEFUL_TESTING and event.phase.is_enabled:
                from schemathesis.specs.openapi.stateful.statistic import OpenAPILinkStats

                self.state_machine_sink = StateMachineSink(transitions=OpenAPILinkStats())
        elif isinstance(event, events.PhaseFinished):
            if event.phase.name == PhaseName.STATEFUL_TESTING and event.phase.is_enabled:
                self.statistic.record_outcome(event.status)
