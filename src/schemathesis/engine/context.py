from __future__ import annotations

import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from schemathesis.config import ProjectConfig
from schemathesis.core import NOT_SET, NotSet
from schemathesis.generation.case import Case
from schemathesis.schemas import APIOperation, BaseSchema

from .control import ExecutionControl

if TYPE_CHECKING:
    import threading

    import requests


@dataclass
class EngineContext:
    """Holds context shared for a test run."""

    schema: BaseSchema
    control: ExecutionControl
    outcome_cache: dict[int, BaseException | None]
    start_time: float

    def __init__(
        self,
        *,
        schema: BaseSchema,
        stop_event: threading.Event,
        session: requests.Session | None = None,
    ) -> None:
        self.schema = schema
        self.control = ExecutionControl(stop_event=stop_event, max_failures=schema.config.max_failures)
        self.outcome_cache = {}
        self.start_time = time.monotonic()
        self._session = session

    def _repr_pretty_(self, *args: Any, **kwargs: Any) -> None: ...

    @property
    def config(self) -> ProjectConfig:
        return self.schema.config

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

    def get_session(self, *, operation: APIOperation | None = None) -> requests.Session:
        if self._session is not None:
            return self._session
        import requests

        session = requests.Session()
        config = self.config

        session.verify = config.tls_verify_for(operation=operation)
        auth = config.auth_for(operation=operation)
        if auth is not None:
            session.auth = auth
        headers = config.headers_for(operation=operation)
        if headers:
            session.headers.update(headers)
        request_cert = config.request_cert_for(operation=operation)
        if request_cert is not None:
            session.cert = request_cert
        proxy = config.proxy_for(operation=operation)
        if proxy is not None:
            session.proxies["all"] = proxy
        return session

    def get_transport_kwargs(self, operation: APIOperation | None = None) -> dict[str, Any]:
        config = self.config
        kwargs: dict[str, Any] = {
            "session": self.get_session(operation=operation),
            "headers": config.headers_for(operation=operation),
            "timeout": config.request_timeout_for(operation=operation),
            "verify": config.tls_verify_for(operation=operation),
            "cert": config.request_cert_for(operation=operation),
        }
        proxy = config.proxy_for(operation=operation)
        if proxy is not None:
            kwargs["proxies"] = {"all": proxy}
        return kwargs
