import os
import shutil
from dataclasses import dataclass, field
from queue import Queue
from typing import List, Optional, Union

import hypothesis

from ..constants import CodeSampleStyle
from ..runner.serialization import SerializedTestResult


@dataclass
class ServiceReportContext:
    queue: Queue
    service_base_url: str


@dataclass
class FileReportContext:
    queue: Queue
    filename: Optional[str] = None


@dataclass
class ExecutionContext:
    """Storage for the current context of the execution."""

    hypothesis_settings: hypothesis.settings
    hypothesis_output: List[str] = field(default_factory=list)
    workers_num: int = 1
    rate_limit: Optional[str] = None
    show_errors_tracebacks: bool = False
    validate_schema: bool = True
    operations_processed: int = 0
    # It is set in runtime, from a `Initialized` event
    operations_count: Optional[int] = None
    current_line_length: int = 0
    terminal_size: os.terminal_size = field(default_factory=shutil.get_terminal_size)
    results: List[SerializedTestResult] = field(default_factory=list)
    cassette_path: Optional[str] = None
    junit_xml_file: Optional[str] = None
    is_interrupted: bool = False
    verbosity: int = 0
    code_sample_style: CodeSampleStyle = CodeSampleStyle.default()
    report: Optional[Union[ServiceReportContext, FileReportContext]] = None
