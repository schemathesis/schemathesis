from __future__ import annotations

import time
from dataclasses import dataclass
from functools import cached_property
from typing import TYPE_CHECKING, Any

from schemathesis.checks import CheckContext
from schemathesis.core import NOT_SET, NotSet
from schemathesis.engine.recorder import ScenarioRecorder
from schemathesis.generation.case import Case
from schemathesis.schemas import BaseSchema

from .control import ExecutionControl

if TYPE_CHECKING:
    import threading

    import requests

    from schemathesis.engine.config import EngineConfig


@dataclass
class EngineContext:
    """Holds context shared for a test run."""

    schema: BaseSchema
    control: ExecutionControl
    outcome_cache: dict[int, BaseException | None]
    config: EngineConfig
    start_time: float

    def __init__(
        self,
        *,
        schema: BaseSchema,
        stop_event: threading.Event,
        config: EngineConfig,
        session: requests.Session | None = None,
    ) -> None:
        self.schema = schema
        self.control = ExecutionControl(stop_event=stop_event, max_failures=config.execution.max_failures)
        self.outcome_cache = {}
        self.config = config
        self.start_time = time.monotonic()
        self._session = session

    def _repr_pretty_(self, *args: Any, **kwargs: Any) -> None: ...

    @property
    def running_time(self) -> float:
        return time.monotonic() - self.start_time

    @property
    def has_to_stop(self) -> bool:
        """Check if execution should stop."""
        return self.control.is_stopped

    @property
    def is_interrupted(self) -> bool:
        return self.control.is_interrupted

    @property
    def has_reached_the_failure_limit(self) -> bool:
        return self.control.has_reached_the_failure_limit

    def stop(self) -> None:
        self.control.stop()

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
        if config.proxy is not None:
            session.proxies["all"] = config.proxy
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

    def get_check_context(self, recorder: ScenarioRecorder) -> CheckContext:
        from requests.models import CaseInsensitiveDict

        return CheckContext(
            override=self.config.override,
            auth=self.config.network.auth,
            headers=CaseInsensitiveDict(self.config.network.headers) if self.config.network.headers else None,
            config=self.config.checks_config,
            transport_kwargs=self.transport_kwargs,
            recorder=recorder,
        )
