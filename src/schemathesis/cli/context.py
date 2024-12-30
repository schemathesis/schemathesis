from __future__ import annotations

import shutil
from collections import Counter
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Generator

from schemathesis.core.output import OutputConfig
from schemathesis.runner import Status
from schemathesis.runner.events import NonFatalError
from schemathesis.runner.models.check import Check

if TYPE_CHECKING:
    import os

    import hypothesis

    from ..stateful.sink import StateMachineSink


@dataclass
class Statistic:
    """Running statistics about test execution."""

    totals: dict[str, dict[str, int]]  # Per-check statistics
    failures: list[tuple[str, list[Check]]]

    __slots__ = ("totals", "failures")

    def __init__(self) -> None:
        self.totals = {}
        self.failures = []

    def record_checks(self, label: str, checks: list[Check]) -> None:
        """Update statistics and store failures from a new batch of checks."""
        # Update totals incrementally
        for check in checks:
            self.totals.setdefault(check.name, Counter())
            self.totals[check.name][check.status] += 1
            self.totals[check.name]["total"] += 1

        # Store only failures
        failed_checks = [check for check in checks if check.status == Status.FAILURE]
        if failed_checks:
            self.failures.append((label, failed_checks))


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
    is_interrupted: bool = False
    output_config: OutputConfig = field(default_factory=OutputConfig)
    state_machine_sink: StateMachineSink | None = None
    initialization_lines: list[str | Generator[str, None, None]] = field(default_factory=list)
    summary_lines: list[str | Generator[str, None, None]] = field(default_factory=list)

    def add_initialization_line(self, line: str | Generator[str, None, None]) -> None:
        self.initialization_lines.append(line)

    def add_summary_line(self, line: str | Generator[str, None, None]) -> None:
        self.summary_lines.append(line)
