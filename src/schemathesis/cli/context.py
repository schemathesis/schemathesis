from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Generator

from schemathesis.core.output import OutputConfig
from schemathesis.runner.events import NonFatalError

if TYPE_CHECKING:
    import os

    import hypothesis

    from schemathesis.runner.models.outcome import TestResult

    from ..stateful.sink import StateMachineSink


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
    results: list[TestResult] = field(default_factory=list)
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
