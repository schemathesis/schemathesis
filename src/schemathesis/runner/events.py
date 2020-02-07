import os
import shutil
import time
from typing import Callable, Iterable, List

import attr
import hypothesis

from ..models import Endpoint, Status, TestResultSet
from ..schemas import BaseSchema


@attr.s(slots=True)  # pragma: no mutate
class ExecutionContext:
    """Storage for the current context of the execution."""

    hypothesis_output: List[str] = attr.ib(factory=list)  # pragma: no mutate
    workers_num: int = attr.ib(default=1)  # pragma: no mutate
    show_errors_tracebacks: bool = attr.ib(default=False)  # pragma: no mutate
    endpoints_processed: int = attr.ib(default=0)  # pragma: no mutate
    current_line_length: int = attr.ib(default=0)  # pragma: no mutate
    terminal_size: os.terminal_size = attr.ib(factory=shutil.get_terminal_size)  # pragma: no mutate


@attr.s()  # pragma: no mutate
class ExecutionEvent:
    results: TestResultSet = attr.ib()  # pragma: no mutate
    schema: BaseSchema = attr.ib()  # pragma: no mutate


@attr.s(slots=True)  # pragma: no mutate
class Initialized(ExecutionEvent):
    """Runner is initialized, settings are prepared, requests session is ready."""

    checks: Iterable[Callable] = attr.ib()  # pragma: no mutate
    hypothesis_settings: hypothesis.settings = attr.ib()  # pragma: no mutate
    start_time: float = attr.ib(factory=time.time)


@attr.s(slots=True)  # pragma: no mutate
class BeforeExecution(ExecutionEvent):
    endpoint: Endpoint = attr.ib()  # pragma: no mutate


@attr.s(slots=True)  # pragma: no mutate
class AfterExecution(ExecutionEvent):
    endpoint: Endpoint = attr.ib()  # pragma: no mutate
    status: Status = attr.ib()  # pragma: no mutate
    hypothesis_output: List[str] = attr.ib(factory=list)  # pragma: no mutate


@attr.s(slots=True)  # pragma: no mutate
class Interrupted(ExecutionEvent):
    pass


@attr.s(slots=True)  # pragma: no mutate
class Finished(ExecutionEvent):
    running_time: float = attr.ib()
