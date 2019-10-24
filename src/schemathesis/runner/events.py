from typing import Callable, Iterable

import attr
import hypothesis

from ..models import Endpoint, StatsCollector
from ..schemas import BaseSchema


@attr.s()
class ExecutionEvent:
    statistic: StatsCollector = attr.ib()


@attr.s(slots=True)
class Initialized(ExecutionEvent):
    """Runner is initialized, settings are prepared, requests session is ready."""

    schema: BaseSchema = attr.ib()
    checks: Iterable[Callable] = attr.ib()
    hypothesis_settings: hypothesis.settings = attr.ib()


@attr.s(slots=True)
class BeforeExecution(ExecutionEvent):
    endpoint: Endpoint = attr.ib()


@attr.s(slots=True)
class FailedExecution(ExecutionEvent):
    endpoint: Endpoint = attr.ib()


@attr.s(slots=True)
class AfterExecution(ExecutionEvent):
    endpoint: Endpoint = attr.ib()


@attr.s(slots=True)
class Finished(ExecutionEvent):
    pass
