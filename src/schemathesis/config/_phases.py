from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from schemathesis.config._checks import ChecksConfig
from schemathesis.config._diff_base import DiffBase
from schemathesis.config._generation import GenerationConfig


@dataclass(repr=False)
class PhaseConfig(DiffBase):
    enabled: bool
    generation: GenerationConfig
    checks: ChecksConfig

    __slots__ = ("enabled", "generation", "checks")

    def __init__(
        self,
        *,
        enabled: bool = True,
        generation: GenerationConfig | None = None,
        checks: ChecksConfig | None = None,
    ) -> None:
        self.enabled = enabled
        self.generation = generation or GenerationConfig()
        self.checks = checks or ChecksConfig()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PhaseConfig:
        return cls(
            enabled=data.get("enabled", True),
            generation=GenerationConfig.from_dict(data.get("generation", {})),
            checks=ChecksConfig.from_dict(data.get("checks", {})),
        )


@dataclass(repr=False)
class CoveragePhaseConfig(DiffBase):
    enabled: bool
    unexpected_methods: set[str]
    generation: GenerationConfig
    checks: ChecksConfig

    __slots__ = ("enabled", "unexpected_methods", "generation", "checks")

    def __init__(
        self,
        *,
        enabled: bool = True,
        unexpected_methods: set[str] | None = None,
        generation: GenerationConfig | None = None,
        checks: ChecksConfig | None = None,
    ) -> None:
        self.enabled = enabled
        self.unexpected_methods = unexpected_methods or {"get", "put", "post", "delete", "options", "patch", "trace"}
        self.generation = generation or GenerationConfig()
        self.checks = checks or ChecksConfig()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CoveragePhaseConfig:
        return cls(
            enabled=data.get("enabled", True),
            unexpected_methods={method.lower() for method in data.get("unexpected-methods", [])},
            generation=GenerationConfig.from_dict(data.get("generation", {})),
            checks=ChecksConfig.from_dict(data.get("checks", {})),
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

    def get_by_name(
        self, *, name: Literal["examples", "coverage", "fuzzing", "stateful"]
    ) -> PhaseConfig | CoveragePhaseConfig:
        return {
            "examples": self.examples,
            "coverage": self.coverage,
            "fuzzing": self.fuzzing,
            "stateful": self.stateful,
        }[name]  # type: ignore[return-value]

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PhasesConfig:
        return cls(
            examples=PhaseConfig.from_dict(data.get("examples", {})),
            coverage=CoveragePhaseConfig.from_dict(data.get("coverage", {})),
            fuzzing=PhaseConfig.from_dict(data.get("fuzzing", {})),
            stateful=PhaseConfig.from_dict(data.get("stateful", {})),
        )
