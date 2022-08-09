import os
import shutil
from queue import Queue
from typing import List, Optional, Union

import attr
import hypothesis

from ..constants import CodeSampleStyle
from ..runner.serialization import SerializedTestResult


@attr.s(slots=True)  # pragma: no mutate
class ServiceReportContext:
    queue: Queue = attr.ib()  # pragma: no mutate
    service_base_url: str = attr.ib()  # pragma: no mutate


@attr.s(slots=True)  # pragma: no mutate
class FileReportContext:
    queue: Queue = attr.ib()  # pragma: no mutate
    filename: str = attr.ib(default=None)  # pragma: no mutate


@attr.s(slots=True)  # pragma: no mutate
class ExecutionContext:
    """Storage for the current context of the execution."""

    hypothesis_settings: hypothesis.settings = attr.ib()  # pragma: no mutate
    hypothesis_output: List[str] = attr.ib(factory=list)  # pragma: no mutate
    workers_num: int = attr.ib(default=1)  # pragma: no mutate
    show_errors_tracebacks: bool = attr.ib(default=False)  # pragma: no mutate
    validate_schema: bool = attr.ib(default=True)  # pragma: no mutate
    operations_processed: int = attr.ib(default=0)  # pragma: no mutate
    # It is set in runtime, from a `Initialized` event
    operations_count: Optional[int] = attr.ib(default=None)  # pragma: no mutate
    current_line_length: int = attr.ib(default=0)  # pragma: no mutate
    terminal_size: os.terminal_size = attr.ib(factory=shutil.get_terminal_size)  # pragma: no mutate
    results: List[SerializedTestResult] = attr.ib(factory=list)  # pragma: no mutate
    cassette_path: Optional[str] = attr.ib(default=None)  # pragma: no mutate
    junit_xml_file: Optional[str] = attr.ib(default=None)  # pragma: no mutate
    is_interrupted: bool = attr.ib(default=False)  # pragma: no mutate
    verbosity: int = attr.ib(default=0)  # pragma: no mutate
    code_sample_style: CodeSampleStyle = attr.ib(default=CodeSampleStyle.default())  # pragma: no mutate
    report: Optional[Union[ServiceReportContext, FileReportContext]] = attr.ib(default=None)  # pragma: no mutate
