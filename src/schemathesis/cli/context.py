from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field
from queue import Queue
from typing import TYPE_CHECKING, Generator

from ..code_samples import CodeSampleStyle
from ..internal.deprecation import deprecated_property
from ..internal.output import OutputConfig
from ..internal.result import Result
from ..runner.probes import ProbeRun
from ..runner.serialization import SerializedTestResult
from ..service.models import AnalysisResult

if TYPE_CHECKING:
    import hypothesis

    from ..stateful.sink import StateMachineSink


@dataclass
class ServiceReportContext:
    queue: Queue
    service_base_url: str


@dataclass
class FileReportContext:
    queue: Queue
    filename: str | None = None


@dataclass
class ExecutionContext:
    """Storage for the current context of the execution."""

    hypothesis_settings: hypothesis.settings
    hypothesis_output: list[str] = field(default_factory=list)
    workers_num: int = 1
    rate_limit: str | None = None
    show_trace: bool = False
    wait_for_schema: float | None = None
    validate_schema: bool = True
    operations_processed: int = 0
    # It is set in runtime, from the `Initialized` event
    operations_count: int | None = None
    seed: int | None = None
    current_line_length: int = 0
    terminal_size: os.terminal_size = field(default_factory=shutil.get_terminal_size)
    results: list[SerializedTestResult] = field(default_factory=list)
    cassette_path: str | None = None
    junit_xml_file: str | None = None
    is_interrupted: bool = False
    verbosity: int = 0
    code_sample_style: CodeSampleStyle = CodeSampleStyle.default()
    report: ServiceReportContext | FileReportContext | None = None
    probes: list[ProbeRun] | None = None
    analysis: Result[AnalysisResult, Exception] | None = None
    output_config: OutputConfig = field(default_factory=OutputConfig)
    state_machine_sink: StateMachineSink | None = None
    initialization_lines: list[str | Generator[str, None, None]] = field(default_factory=list)
    summary_lines: list[str | Generator[str, None, None]] = field(default_factory=list)

    @deprecated_property(removed_in="4.0", replacement="show_trace")
    def show_errors_tracebacks(self) -> bool:
        return self.show_trace

    def add_initialization_line(self, line: str | Generator[str, None, None]) -> None:
        self.initialization_lines.append(line)

    def add_summary_line(self, line: str | Generator[str, None, None]) -> None:
        self.summary_lines.append(line)
