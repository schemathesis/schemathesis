from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Iterable

if TYPE_CHECKING:
    import hypothesis

    from .._override import CaseOverride
    from ..checks import CheckFunction
    from ..generation import GenerationConfig
    from ..internal.checks import CheckConfig
    from ..schemas import BaseSchema
    from ..service.client import ServiceClient
    from ..stateful import Stateful
    from ..targets import Target
    from ..types import RawAuth, RequestCert


@dataclass
class ExecutionConfig:
    """Configuration for test execution."""

    checks: Iterable[CheckFunction]
    targets: Iterable[Target]
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

    auth: RawAuth | None = None
    auth_type: str | None = None
    headers: dict[str, Any] | None = None
    timeout: int | None = None
    tls_verify: bool | str = True
    proxy: str | None = None
    cert: RequestCert | None = None


@dataclass
class EngineConfig:
    """Complete runner configuration."""

    schema: BaseSchema
    execution: ExecutionConfig
    network: NetworkConfig
    checks_config: CheckConfig
    override: CaseOverride | None = None
    service_client: ServiceClient | None = None
