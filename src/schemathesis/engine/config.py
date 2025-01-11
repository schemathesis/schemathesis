from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from schemathesis.checks import CheckFunction, not_a_server_error
from schemathesis.generation import GenerationConfig
from schemathesis.generation.hypothesis import DEFAULT_DEADLINE
from schemathesis.generation.overrides import Override

if TYPE_CHECKING:
    import hypothesis

    from schemathesis.checks import ChecksConfig
    from schemathesis.generation.targets import TargetFunction


def _default_hypothesis_settings() -> hypothesis.settings:
    import hypothesis

    return hypothesis.settings(deadline=DEFAULT_DEADLINE)


@dataclass
class ExecutionConfig:
    """Configuration for test execution."""

    checks: list[CheckFunction] = field(default_factory=lambda: [not_a_server_error])
    targets: list[TargetFunction] = field(default_factory=list)
    hypothesis_settings: hypothesis.settings = field(default_factory=_default_hypothesis_settings)
    generation: GenerationConfig = field(default_factory=GenerationConfig)
    max_failures: int | None = None
    unique_data: bool = False
    no_failfast: bool = False
    seed: int | None = None
    workers_num: int = 1


@dataclass
class NetworkConfig:
    """Network-related configuration."""

    auth: tuple[str, str] | None = None
    headers: dict[str, Any] = field(default_factory=dict)
    timeout: int | None = None
    tls_verify: bool | str = True
    proxy: str | None = None
    cert: str | tuple[str, str] | None = None


@dataclass
class EngineConfig:
    """Complete engine configuration."""

    execution: ExecutionConfig = field(default_factory=ExecutionConfig)
    network: NetworkConfig = field(default_factory=NetworkConfig)
    checks_config: ChecksConfig = field(default_factory=dict)
    override: Override | None = None
