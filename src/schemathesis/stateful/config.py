from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from typing import TYPE_CHECKING, Any

from ..constants import DEFAULT_DEADLINE

if TYPE_CHECKING:
    import hypothesis
    from requests.auth import HTTPDigestAuth

    from .._override import CaseOverride
    from ..models import CheckFunction
    from ..targets import Target
    from ..transports import RequestConfig
    from ..types import RawAuth


def _default_checks_factory() -> tuple[CheckFunction, ...]:
    from ..checks import ALL_CHECKS
    from ..specs.openapi.checks import ensure_resource_availability, use_after_free

    return ALL_CHECKS + (use_after_free, ensure_resource_availability)


def _get_default_hypothesis_settings_kwargs() -> dict[str, Any]:
    import hypothesis

    return {
        "phases": (hypothesis.Phase.generate,),
        "deadline": None,
        "stateful_step_count": 6,
        "suppress_health_check": list(hypothesis.HealthCheck),
    }


def _default_hypothesis_settings_factory() -> hypothesis.settings:
    # To avoid importing hypothesis at the module level
    import hypothesis

    return hypothesis.settings(**_get_default_hypothesis_settings_kwargs())


def _default_request_config_factory() -> RequestConfig:
    from ..transports import RequestConfig

    return RequestConfig()


@dataclass
class StatefulTestRunnerConfig:
    """Configuration for the stateful test runner."""

    # Checks to run against each response
    checks: tuple[CheckFunction, ...] = field(default_factory=_default_checks_factory)
    # Hypothesis settings for state machine execution
    hypothesis_settings: hypothesis.settings = field(default_factory=_default_hypothesis_settings_factory)
    # Request-level configuration
    request: RequestConfig = field(default_factory=_default_request_config_factory)
    # Whether to stop the execution after the first failure
    exit_first: bool = False
    max_failures: int | None = None
    # Custom headers sent with each request
    headers: dict[str, str] = field(default_factory=dict)
    auth: HTTPDigestAuth | RawAuth | None = None
    seed: int | None = None
    override: CaseOverride | None = None
    max_response_time: int | None = None
    dry_run: bool = False
    targets: list[Target] = field(default_factory=list)

    def __post_init__(self) -> None:
        import hypothesis

        kwargs = _get_hypothesis_settings_kwargs_override(self.hypothesis_settings)
        if kwargs:
            self.hypothesis_settings = hypothesis.settings(self.hypothesis_settings, **kwargs)


def _get_hypothesis_settings_kwargs_override(settings: hypothesis.settings) -> dict[str, Any]:
    """Get the settings that should be overridden to match the defaults for API state machines."""
    import hypothesis

    kwargs = {}
    hypothesis_default = hypothesis.settings()
    state_machine_default = _default_hypothesis_settings_factory()
    if settings.phases == hypothesis_default.phases:
        kwargs["phases"] = state_machine_default.phases
    if settings.stateful_step_count == hypothesis_default.stateful_step_count:
        kwargs["stateful_step_count"] = state_machine_default.stateful_step_count
    if settings.deadline in (hypothesis_default.deadline, timedelta(milliseconds=DEFAULT_DEADLINE)):
        kwargs["deadline"] = state_machine_default.deadline
    if settings.suppress_health_check == hypothesis_default.suppress_health_check:
        kwargs["suppress_health_check"] = state_machine_default.suppress_health_check
    return kwargs
