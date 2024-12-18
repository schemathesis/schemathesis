from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Sequence

from schemathesis.generation.overrides import Override

if TYPE_CHECKING:
    import hypothesis

    from schemathesis.checks import ChecksConfig
    from schemathesis.generation.targets import TargetFunction

    from ..checks import CheckFunction
    from ..generation import GenerationConfig
    from ..schemas import BaseSchema
    from ..service.client import ServiceClient


@dataclass
class ExecutionConfig:
    """Configuration for test execution."""

    checks: list[CheckFunction]
    targets: Sequence[TargetFunction]
    hypothesis_settings: hypothesis.settings
    generation_config: GenerationConfig | None = None
    max_failures: int | None = None
    unique_data: bool = False
    no_failfast: bool = False
    dry_run: bool = False
    seed: int | None = None
    workers_num: int = 1


@dataclass
class NetworkConfig:
    """Network-related configuration."""

    auth: tuple[str, str] | None = None
    headers: dict[str, Any] | None = None
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
    service_client: ServiceClient | None = None
