from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from schemathesis.engine.phases import PhaseName
from schemathesis.generation import GenerationConfig

if TYPE_CHECKING:
    import hypothesis

    from schemathesis.generation.targets import TargetFunction


def _default_hypothesis_settings() -> hypothesis.settings:
    import hypothesis

    return hypothesis.settings(deadline=None)


@dataclass
class ExecutionConfig:
    """Configuration for test execution."""

    phases: list[PhaseName] = field(default_factory=PhaseName.defaults)
    targets: list[TargetFunction] = field(default_factory=list)
    hypothesis_settings: hypothesis.settings = field(default_factory=_default_hypothesis_settings)
    generation: GenerationConfig = field(default_factory=GenerationConfig)
    max_failures: int | None = None
    unique_inputs: bool = False
    continue_on_failure: bool = False
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
