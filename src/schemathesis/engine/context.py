from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from schemathesis.checks import RunChecks
from schemathesis.config import ProjectConfig
from schemathesis.core import NOT_SET, NotSet
from schemathesis.core.error_feedback import ErrorFeedbackStore
from schemathesis.engine._lazy import LazyInit
from schemathesis.engine.control import ExecutionControl
from schemathesis.engine.health import HealthState
from schemathesis.engine.link_calibration import LinkCalibrationState
from schemathesis.engine.observations import Observations
from schemathesis.engine.run.cache import Cache
from schemathesis.engine.supervisor import Supervisor
from schemathesis.generation.case import Case
from schemathesis.python._constants.orchestrator import extract_registered
from schemathesis.python._constants.pool import ConstantsPool
from schemathesis.schemas import APIOperation

if TYPE_CHECKING:
    import requests

    from schemathesis.core.spec import ApiSchema
    from schemathesis.engine import StopReason
    from schemathesis.engine.recorder import ScenarioRecorder
    from schemathesis.resources import ExtraDataSource


@dataclass
class EngineContext:
    """Holds context shared for a test run."""

    schema: ApiSchema
    control: ExecutionControl
    outcome_cache: dict[int, BaseException | None]
    start_time: float
    observations: Observations | None
    link_calibration: LinkCalibrationState | None

    __slots__ = (
        "schema",
        "control",
        "outcome_cache",
        "health",
        "link_calibration",
        "start_time",
        "observations",
        "_thread_local",
        "_transport_kwargs_cache",
        "_extra_data_source",
        "_extra_data_source_lock",
        "_error_feedback",
        "_error_feedback_lock",
        "_supervisor",
        "_supervisor_lock",
        "_cache",
        "_cache_lock",
        "_checks",
        "_checks_lock",
        "_constants_extraction",
        "_constants_extraction_lock",
    )

    def __init__(
        self,
        *,
        schema: ApiSchema,
        stop_event: threading.Event,
        observations: Observations | None = None,
        max_time: int | None = None,
    ) -> None:
        self.schema = schema
        self.start_time = time.monotonic()
        self.control = ExecutionControl(
            stop_event=stop_event,
            max_failures=schema.config.max_failures,
            max_time=max_time,
            start_time=self.start_time,
        )
        self.outcome_cache = {}
        self.health = HealthState()
        self.link_calibration = LinkCalibrationState() if schema.config.phases.stateful.link_calibration else None
        self.observations = observations
        self._thread_local = threading.local()
        self._transport_kwargs_cache: dict[str | None, dict[str, Any]] = {}
        self._extra_data_source = LazyInit.UNSET
        self._extra_data_source_lock = threading.Lock()
        self._error_feedback = LazyInit.UNSET
        self._error_feedback_lock = threading.Lock()
        self._supervisor = LazyInit.UNSET
        self._supervisor_lock = threading.Lock()
        self._cache = LazyInit.UNSET
        self._cache_lock = threading.Lock()
        self._checks = LazyInit.UNSET
        self._checks_lock = threading.Lock()
        self._constants_extraction = LazyInit.UNSET
        self._constants_extraction_lock = threading.Lock()

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

    @property
    def stop_reason(self) -> StopReason:
        return self.control.stop_reason

    def record_observations(self, recorder: ScenarioRecorder) -> None:
        """Add new observations from a scenario."""
        if self.observations is not None:
            self.observations.extract_observations_from(recorder)

    def apply_stateful_inference(self) -> int:
        """Discover spec-specific stateful transitions; return the number available."""
        return self.schema.apply_stateful_inference(self)

    def extract_constants(self) -> None:
        """Force one-time constant extraction so later strategy builds hit a ready pool."""
        self.constants_extraction  # noqa: B018

    def stop(self) -> None:
        self.control.stop()

    def cache_outcome(self, case: Case, outcome: BaseException | None) -> None:
        self.outcome_cache[hash(case)] = outcome

    def get_cached_outcome(self, case: Case) -> BaseException | None | NotSet:
        return self.outcome_cache.get(hash(case), NOT_SET)

    def get_session(self, *, operation: APIOperation | None = None) -> requests.Session:
        session = getattr(self._thread_local, "session", None)
        if session is not None:
            return session
        session = make_session(self.config, operation=operation)
        self._thread_local.session = session
        return session

    def get_transport_kwargs(self, operation: APIOperation | None = None) -> dict[str, Any]:
        key = operation.label if operation is not None else None
        cached = self._transport_kwargs_cache.get(key)
        if cached is None:
            config = self.config
            cached = {
                "headers": config.headers_for(operation=operation),
                "max_redirects": config.max_redirects_for(operation=operation),
                "timeout": config.request_timeout_for(operation=operation),
                "verify": config.tls_verify_for(operation=operation),
                "cert": config.request_cert_for(operation=operation),
            }
            proxy = config.proxy_for(operation=operation)
            if proxy is not None:
                cached["proxies"] = {"all": proxy}
            self._transport_kwargs_cache[key] = cached
        kwargs = cached.copy()
        # Apply the health timeout override only when it strictly tightens the configured timeout.
        if operation is not None:
            override = self.health.timeout_override(operation.label)
            if override is not None:
                base_timeout = kwargs.get("timeout")
                if base_timeout is None or override < base_timeout:
                    kwargs["timeout"] = override
        kwargs["session"] = self.get_session(operation=operation)
        return kwargs

    # Extra data source for augmenting test generation with real data.
    # Lazily initialized to support per-operation configuration overrides.
    extra_data_source: LazyInit[ExtraDataSource | None] = LazyInit(lambda ctx: ctx.schema.create_extra_data_source())

    # Store of parser observations from 4xx responses; returns `None` when disabled by config.
    error_feedback: LazyInit[ErrorFeedbackStore | None] = LazyInit(
        lambda ctx: ErrorFeedbackStore() if ctx.config.phases.fuzzing.error_feedback.is_enabled else None
    )

    # Per-operation runtime supervisor that issues scheduling directives based on observed signals.
    supervisor: LazyInit[Supervisor] = LazyInit(lambda ctx: Supervisor())

    # Runtime cache controller -- replay during probing, persist at end of run.
    cache: LazyInit[Cache] = LazyInit(lambda ctx: Cache(ctx))

    # Per-run class-based check instances, shared across worker threads.
    checks: LazyInit[RunChecks] = LazyInit(lambda ctx: RunChecks.from_registry(config=ctx.config.checks_config_for()))

    # Extracted constants from user-defined sources; lazily computed once per run.
    constants_extraction: LazyInit[ConstantsPool] = LazyInit(lambda ctx: extract_registered())


def make_session(config: ProjectConfig, *, operation: APIOperation | None = None) -> requests.Session:
    """Build a `requests.Session` configured from project config."""
    from schemathesis.transport.requests import ManagedCookiesSession

    session = ManagedCookiesSession()
    session.headers = {}
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
