from __future__ import annotations

import time
from dataclasses import dataclass
from functools import cached_property
from typing import TYPE_CHECKING, Any

from schemathesis.checks import CheckContext
from schemathesis.core import NOT_SET, NotSet
from schemathesis.stateful.graph import ExecutionGraph

from .control import ExecutionControl
from .errors import EngineErrorInfo
from .models import TestResult, TestResultSet

if TYPE_CHECKING:
    import threading

    import requests

    from schemathesis.core.errors import InvalidSchema
    from schemathesis.generation.case import Case

    from . import events
    from .config import EngineConfig
    from .phases import PhaseName


class PhaseStorage:
    """Manages storage of phase-specific data."""

    def __init__(self) -> None:
        self._storage: dict[PhaseName, object] = {}

    def store(self, phase: PhaseName, data: object) -> None:
        """Store phase-specific data."""
        self._storage[phase] = data

    def get(self, phase: PhaseName) -> object:
        if phase not in self._storage:
            return None
        return self._storage[phase]


@dataclass
class EngineContext:
    """Holds context shared for a test run."""

    data: TestResultSet
    control: ExecutionControl
    outcome_cache: dict[int, BaseException | None]
    config: EngineConfig
    phase_data: PhaseStorage
    start_time: float

    def __init__(
        self, *, stop_event: threading.Event, config: EngineConfig, session: requests.Session | None = None
    ) -> None:
        self.data = TestResultSet(seed=config.execution.seed)
        self.control = ExecutionControl(stop_event=stop_event, max_failures=config.execution.max_failures)
        self.outcome_cache = {}
        self.config = config
        self.phase_data = PhaseStorage()
        self.start_time = time.monotonic()
        self.execution_graph = ExecutionGraph()
        self._session = session

    def _repr_pretty_(self, *args: Any, **kwargs: Any) -> None: ...

    @property
    def running_time(self) -> float:
        return time.monotonic() - self.start_time

    @property
    def is_stopped(self) -> bool:
        """Check if execution should stop."""
        return self.control.is_stopped

    def on_event(self, event: events.EngineEvent) -> bool:
        """Process event and update execution state."""
        return self.control.on_event(event)

    @property
    def has_all_not_found(self) -> bool:
        """Check if all responses are 404."""
        has_not_found = False
        for entry in self.data.results:
            for check in entry.checks:
                if check.response.status_code == 404:
                    has_not_found = True
                else:
                    # There are non-404 responses, no reason to check any other response
                    return False
        # Only happens if all responses are 404, or there are no responses at all.
        # In the first case, it returns True, for the latter - False
        return has_not_found

    def add_result(self, result: TestResult) -> None:
        self.data.append(result)

    def add_error(self, error: InvalidSchema) -> None:
        self.data.errors.append(EngineErrorInfo(error, title=error.full_path))

    def add_warning(self, message: str) -> None:
        self.data.add_warning(message)

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


ALL_NOT_FOUND_WARNING_MESSAGE = "All API responses have a 404 status code. Did you specify the proper API location?"
