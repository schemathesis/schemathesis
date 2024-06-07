from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import hypothesis

    from ..models import CheckFunction


def _default_checks_factory() -> tuple[CheckFunction, ...]:
    from ..checks import ALL_CHECKS
    from ..specs.openapi.checks import use_after_free

    return ALL_CHECKS + (use_after_free,)


def _get_default_hypothesis_settings_kwargs() -> dict[str, Any]:
    import hypothesis

    return {"phases": (hypothesis.Phase.generate,), "deadline": None}


def _default_hypothesis_settings_factory() -> hypothesis.settings:
    # To avoid importing hypothesis at the module level
    import hypothesis

    return hypothesis.settings(**_get_default_hypothesis_settings_kwargs())


@dataclass
class StatefulTestRunnerConfig:
    """Configuration for the stateful test runner."""

    # Checks to run against each response
    checks: tuple[CheckFunction, ...] = field(default_factory=_default_checks_factory)
    # Hypothesis settings for state machine execution
    hypothesis_settings: hypothesis.settings = field(default_factory=_default_hypothesis_settings_factory)
    # Whether to stop the execution after the first failure
    exit_first: bool = False
    # Custom headers sent with each request
    headers: dict[str, str] = field(default_factory=dict)
    # Timeout for each request in milliseconds
    request_timeout: int | None = None

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
    if settings.deadline == hypothesis_default.deadline:
        kwargs["deadline"] = state_machine_default.deadline
    return kwargs
