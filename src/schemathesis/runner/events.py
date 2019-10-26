import os
import shutil
from enum import IntEnum
from typing import Callable, Iterable, List

import attr
import hypothesis

from ..models import Endpoint, StatsCollector
from ..schemas import BaseSchema


class ExecutionResult(IntEnum):
    success = 1
    failure = 2
    error = 3


@attr.s(slots=True)
class ExecutionContext:
    """Storage for the current context of the execution."""

    hypothesis_output: List[str] = attr.ib()  # pragma: no mutate
    endpoints_processed: int = attr.ib(default=0)  # pragma: no mutate
    current_line_length: int = attr.ib(default=0)  # pragma: no mutate
    terminal_size: os.terminal_size = attr.ib(factory=shutil.get_terminal_size)  # pragma: no mutate


@attr.s()  # pragma: no mutate
class ExecutionEvent:
    statistic: StatsCollector = attr.ib()  # pragma: no mutate
    schema: BaseSchema = attr.ib()  # pragma: no mutate


@attr.s(slots=True)  # pragma: no mutate
class Initialized(ExecutionEvent):
    """Runner is initialized, settings are prepared, requests session is ready."""

    checks: Iterable[Callable] = attr.ib()  # pragma: no mutate
    hypothesis_settings: hypothesis.settings = attr.ib()  # pragma: no mutate


@attr.s(slots=True)  # pragma: no mutate
class BeforeExecution(ExecutionEvent):
    endpoint: Endpoint = attr.ib()  # pragma: no mutate


@attr.s(slots=True)
class AfterExecution(ExecutionEvent):
    endpoint: Endpoint = attr.ib()  # pragma: no mutate
    result: ExecutionResult = attr.ib()  # pragma: no mutate


@attr.s(slots=True)  # pragma: no mutate
class Finished(ExecutionEvent):
    pass
