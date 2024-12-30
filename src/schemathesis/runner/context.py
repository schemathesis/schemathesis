from __future__ import annotations

import time
from dataclasses import dataclass
from functools import cached_property
from typing import TYPE_CHECKING, Any

from schemathesis.checks import CheckContext
from schemathesis.core import NOT_SET, NotSet
from schemathesis.runner import Status
from schemathesis.runner.models.outcome import TestResult
from schemathesis.stateful.graph import ExecutionGraph

from .control import ExecutionControl

if TYPE_CHECKING:
    import threading

    import requests

    from schemathesis.generation.case import Case

    from . import events
    from .config import EngineConfig


@dataclass
class EngineContext:
    """Holds context shared for a test run."""

    data: list[TestResult]
    control: ExecutionControl
    outcome_cache: dict[int, BaseException | None]
    config: EngineConfig
    start_time: float
    outcome_statistic: dict[Status, int]

    def __init__(
        self, *, stop_event: threading.Event, config: EngineConfig, session: requests.Session | None = None
    ) -> None:
        self.data = []
        self.control = ExecutionControl(stop_event=stop_event, max_failures=config.execution.max_failures)
        self.outcome_cache = {}
        self.config = config
        self.start_time = time.monotonic()
        self.execution_graph = ExecutionGraph()
        self.outcome_statistic = {}
        self._session = session

    def _repr_pretty_(self, *args: Any, **kwargs: Any) -> None: ...

    @property
    def running_time(self) -> float:
        return time.monotonic() - self.start_time

    @property
    def is_stopped(self) -> bool:
        """Check if execution should stop."""
        return self.control.is_stopped

    def record_item(self, status: Status) -> None:
        value = self.outcome_statistic.setdefault(status, 0)
        self.outcome_statistic[status] = value + 1

    def on_event(self, event: events.EngineEvent) -> bool:
        """Process event and update execution state."""
        return self.control.on_event(event)

    def add_result(self, result: TestResult) -> None:
        self.data.append(result)

    def cache_outcome(self, case: Case, outcome: BaseException | None) -> None:
        self.outcome_cache[hash(case)] = outcome

    def get_cached_outcome(self, case: Case) -> BaseException | None | NotSet:
        return self.outcome_cache.get(hash(case), NOT_SET)

    @cached_property
    def session(self) -> requests.Session:
        if self._session is not None:
            return self._session
        import requests

        session = requests.Session()
        config = self.config.network
        session.verify = config.tls_verify
        if config.auth is not None:
            session.auth = config.auth
        if config.headers:
            session.headers.update(config.headers)
        if config.cert is not None:
            session.cert = config.cert
        return session

    @property
    def transport_kwargs(self) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "session": self.session,
            "headers": self.config.network.headers,
            "timeout": self.config.network.timeout,
            "verify": self.config.network.tls_verify,
            "cert": self.config.network.cert,
        }
        if self.config.network.proxy is not None:
            kwargs["proxies"] = {"all": self.config.network.proxy}
        return kwargs

    @property
    def check_context(self) -> CheckContext:
        from requests.models import CaseInsensitiveDict

        return CheckContext(
            override=self.config.override,
            auth=self.config.network.auth,
            headers=CaseInsensitiveDict(self.config.network.headers) if self.config.network.headers else None,
            config=self.config.checks_config,
            transport_kwargs=self.transport_kwargs,
            execution_graph=self.execution_graph,
        )
