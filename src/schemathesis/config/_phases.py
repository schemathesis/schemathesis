from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from schemathesis.config._diff_base import DiffBase


@dataclass(repr=False)
class PhaseConfig(DiffBase):
    enabled: bool

    __slots__ = ("enabled",)

    def __init__(self, *, enabled: bool = True) -> None:
        self.enabled = enabled

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PhaseConfig:
        return cls(enabled=data.get("enabled", True))


@dataclass(repr=False)
class CoveragePhaseConfig(DiffBase):
    enabled: bool
    unexpected_methods: set[str]

    __slots__ = ("enabled", "unexpected_methods")

    def __init__(self, *, enabled: bool = True, unexpected_methods: set[str] | None = None) -> None:
        self.enabled = enabled
        self.unexpected_methods = unexpected_methods or {"get", "put", "post", "delete", "options", "patch", "trace"}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CoveragePhaseConfig:
        return cls(
            enabled=data.get("enabled", True),
            unexpected_methods={method.lower() for method in data.get("unexpected-methods", [])},
        )


@dataclass(repr=False)
class PhasesConfig(DiffBase):
    examples: PhaseConfig
    coverage: CoveragePhaseConfig
    fuzzing: PhaseConfig
    stateful: PhaseConfig

    __slots__ = ("examples", "coverage", "fuzzing", "stateful")

    def __init__(
        self,
        *,
        examples: PhaseConfig | None = None,
        coverage: CoveragePhaseConfig | None = None,
        fuzzing: PhaseConfig | None = None,
        stateful: PhaseConfig | None = None,
    ) -> None:
        self.examples = examples or PhaseConfig()
        self.coverage = coverage or CoveragePhaseConfig()
        self.fuzzing = fuzzing or PhaseConfig()
        self.stateful = stateful or PhaseConfig()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PhasesConfig:
        return cls(
            examples=PhaseConfig.from_dict(data.get("examples", {})),
            coverage=CoveragePhaseConfig.from_dict(data.get("coverage", {})),
            fuzzing=PhaseConfig.from_dict(data.get("fuzzing", {})),
            stateful=PhaseConfig.from_dict(data.get("stateful", {})),
        )
