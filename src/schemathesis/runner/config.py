from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Sequence

if TYPE_CHECKING:
    import hypothesis

    from schemathesis.checks import ChecksConfig
    from schemathesis.generation.targets import TargetFunction

    from .._override import CaseOverride
    from ..checks import CheckFunction
    from ..generation import GenerationConfig
    from ..schemas import BaseSchema
    from ..service.client import ServiceClient
    from ..stateful import Stateful


@dataclass
class ExecutionConfig:
    """Configuration for test execution."""

    checks: list[CheckFunction]
    targets: Sequence[TargetFunction]
    hypothesis_settings: hypothesis.settings
    max_response_time: int | None = None
    generation_config: GenerationConfig | None = None
    max_failures: int | None = None
    unique_data: bool = False
    dry_run: bool = False
    seed: int | None = None
    stateful: Stateful | None = None
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
    override: CaseOverride | None = None
    service_client: ServiceClient | None = None
