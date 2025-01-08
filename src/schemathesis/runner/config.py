from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from schemathesis.generation.overrides import Override

if TYPE_CHECKING:
    import hypothesis

    from schemathesis.checks import ChecksConfig
    from schemathesis.generation.targets import TargetFunction

    from ..checks import CheckFunction
    from ..generation import GenerationConfig
    from ..schemas import BaseSchema


@dataclass
class ExecutionConfig:
    """Configuration for test execution."""

    checks: list[CheckFunction]
    targets: list[TargetFunction]
    hypothesis_settings: hypothesis.settings
    generation_config: GenerationConfig
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
    """Complete runner configuration."""

    schema: BaseSchema
    execution: ExecutionConfig
    network: NetworkConfig
    checks_config: ChecksConfig
    override: Override | None = None
