from __future__ import annotations
import os
import shutil
from dataclasses import dataclass, field
from queue import Queue
from typing import TYPE_CHECKING

from ..code_samples import CodeSampleStyle
from ..runner.serialization import SerializedTestResult

if TYPE_CHECKING:
    import hypothesis


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
    show_errors_tracebacks: bool = False
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
