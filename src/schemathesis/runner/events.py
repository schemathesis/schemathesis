from typing import Callable, Iterable

import attr
import hypothesis

from ..models import Endpoint, StatsCollector
from ..schemas import BaseSchema


@attr.s()  # pragma: no mutate
class ExecutionEvent:
    statistic: StatsCollector = attr.ib()  # pragma: no mutate


@attr.s(slots=True)  # pragma: no mutate
class Initialized(ExecutionEvent):
    """Runner is initialized, settings are prepared, requests session is ready."""

    schema: BaseSchema = attr.ib()  # pragma: no mutate
    checks: Iterable[Callable] = attr.ib()  # pragma: no mutate
    hypothesis_settings: hypothesis.settings = attr.ib()  # pragma: no mutate


@attr.s(slots=True)  # pragma: no mutate
class BeforeExecution(ExecutionEvent):
    endpoint: Endpoint = attr.ib()  # pragma: no mutate


@attr.s(slots=True)
class FailedExecution(ExecutionEvent):
    endpoint: Endpoint = attr.ib()


@attr.s(slots=True)  # pragma: no mutate
class AfterExecution(ExecutionEvent):
    endpoint: Endpoint = attr.ib()  # pragma: no mutate


@attr.s(slots=True)  # pragma: no mutate
class Finished(ExecutionEvent):
    pass
