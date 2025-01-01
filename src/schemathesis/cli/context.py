from __future__ import annotations

import shutil
from collections import Counter
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Generator, cast

from schemathesis.core.failures import Failure
from schemathesis.core.output import OutputConfig
from schemathesis.core.transport import Response
from schemathesis.runner import Status, events
from schemathesis.runner.dataforest import DataForest
from schemathesis.runner.events import NonFatalError

if TYPE_CHECKING:
    import os

    import hypothesis
    import requests

    from ..stateful.sink import StateMachineSink


@dataclass
class Statistic:
    """Running statistics about test execution."""

    totals: dict[str, dict[str, int]]  # Per-check statistics
    failures: dict[str, list[GroupedFailures]]

    __slots__ = ("totals", "failures")

    def __init__(self) -> None:
        self.totals = {}
        self.failures = {}

    def record_checks(self, forest: DataForest) -> None:
        """Update statistics and store failures from a new batch of checks."""
        failures = {}
        # Process all checks in a single pass
        for case_id in forest.cases:
            checks = forest.checks.get(case_id, [])
            for check in checks:
                response = forest.interactions[case_id].response
                # Update totals
                totals = self.totals.setdefault(check.name, Counter())
                totals[check.status] += 1
                totals["total"] += 1

                # Collect failures
                if check.status == Status.FAILURE and check.failure and check.code_sample:
                    if case_id not in failures:
                        failures[case_id] = GroupedFailures(
                            case_id=case_id,
                            code_sample=check.code_sample,
                            failures=[],
                            response=response,
                        )
                    failures[case_id].failures.append(check.failure)
        if failures:
            # Sort failures so that server errors appear first
            for group in failures.values():
                group.failures = sorted(
                    set(group.failures), key=lambda f: (f.code != "server_error", f.__class__.__name__, f.message)
                )
            self.failures[forest.label] = list(failures.values())


@dataclass
class GroupedFailures:
    """Represents failures grouped by case ID."""

    case_id: str
    code_sample: str
    failures: list[Failure]
    response: Response | requests.Timeout | requests.ConnectionError

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
        if isinstance(event, events.AfterExecution) or (
            isinstance(event, events.ScenarioFinished) and not event.is_final
        ):
            self.operations_processed += 1
            self.statistic.record_checks(event.forest)
        elif isinstance(event, events.Initialized):
            self.operations_count = cast(int, event.operations_count)  # INVARIANT: should not be `None`
            self.seed = event.seed
        elif isinstance(event, events.NonFatalError):
            self.errors.append(event)
        elif isinstance(event, events.Warning):
            self.warnings.append(event.message)
