from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from schemathesis.config._checks import ChecksConfig
from schemathesis.config._diff_base import DiffBase
from schemathesis.config._generation import GenerationConfig
from schemathesis.core import DEFAULT_STATEFUL_STEP_COUNT

DEFAULT_UNEXPECTED_METHODS = {"get", "put", "post", "delete", "options", "patch", "trace"}


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
    generation: GenerationConfig
    checks: ChecksConfig
    unexpected_methods: set[str]

    __slots__ = ("enabled", "generation", "checks", "unexpected_methods")

    def __init__(
        self,
        *,
        enabled: bool = True,
        generation: GenerationConfig | None = None,
        checks: ChecksConfig | None = None,
        unexpected_methods: set[str] | None = None,
    ) -> None:
        self.enabled = enabled
        self.unexpected_methods = unexpected_methods or DEFAULT_UNEXPECTED_METHODS
        self.generation = generation or GenerationConfig()
        self.checks = checks or ChecksConfig()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CoveragePhaseConfig:
        return cls(
            enabled=data.get("enabled", True),
            unexpected_methods={method.lower() for method in data.get("unexpected-methods", [])}
            if "unexpected-methods" in data
            else None,
            generation=GenerationConfig.from_dict(data.get("generation", {})),
            checks=ChecksConfig.from_dict(data.get("checks", {})),
        )


@dataclass(repr=False)
class StatefulPhaseConfig(DiffBase):
    enabled: bool
    generation: GenerationConfig
    checks: ChecksConfig
    max_steps: int

    __slots__ = ("enabled", "generation", "checks", "max_steps")

    def __init__(
        self,
        *,
        enabled: bool = True,
        generation: GenerationConfig | None = None,
        checks: ChecksConfig | None = None,
        max_steps: int | None = None,
    ) -> None:
        self.enabled = enabled
        self.max_steps = max_steps or DEFAULT_STATEFUL_STEP_COUNT
        self.generation = generation or GenerationConfig()
        self.checks = checks or ChecksConfig()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> StatefulPhaseConfig:
        return cls(
            enabled=data.get("enabled", True),
            max_steps=data.get("max-steps"),
            generation=GenerationConfig.from_dict(data.get("generation", {})),
            checks=ChecksConfig.from_dict(data.get("checks", {})),
        )


@dataclass(repr=False)
class PhasesConfig(DiffBase):
    examples: PhaseConfig
    coverage: CoveragePhaseConfig
    fuzzing: PhaseConfig
    stateful: StatefulPhaseConfig

    __slots__ = ("examples", "coverage", "fuzzing", "stateful")

    def __init__(
        self,
        *,
        examples: PhaseConfig | None = None,
        coverage: CoveragePhaseConfig | None = None,
        fuzzing: PhaseConfig | None = None,
        stateful: StatefulPhaseConfig | None = None,
    ) -> None:
        self.examples = examples or PhaseConfig()
        self.coverage = coverage or CoveragePhaseConfig()
        self.fuzzing = fuzzing or PhaseConfig()
        self.stateful = stateful or StatefulPhaseConfig()

    def get_by_name(self, *, name: str) -> PhaseConfig | CoveragePhaseConfig | StatefulPhaseConfig:
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
            stateful=StatefulPhaseConfig.from_dict(data.get("stateful", {})),
        )

    def update(self, *, phases: list[str]) -> None:
        self.examples.enabled = "examples" in phases
        self.coverage.enabled = "coverage" in phases
        self.fuzzing.enabled = "fuzzing" in phases
        self.stateful.enabled = "stateful" in phases
