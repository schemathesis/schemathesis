from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from schemathesis.config._checks import ChecksConfig
from schemathesis.config._diff_base import DiffBase
from schemathesis.config._generation import GenerationConfig
from schemathesis.core import DEFAULT_STATEFUL_STEP_COUNT

DEFAULT_UNEXPECTED_METHODS = {"get", "put", "post", "delete", "options", "patch", "trace"}


@dataclass(repr=False)
class ExtraDataSourcesConfig(DiffBase):
    """Configuration for extra data sources used to augment test generation."""

    responses: bool

    __slots__ = ("responses", "_is_default")

    def __init__(
        self,
        *,
        responses: bool = True,
    ) -> None:
        self.responses = responses
        self._is_default = responses

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ExtraDataSourcesConfig:
        return cls(
            responses=data.get("responses", True),
        )

    @property
    def is_enabled(self) -> bool:
        """Extra data sources are enabled if any source is enabled."""
        return self.responses


class OperationOrdering(str, Enum):
    """Strategy for ordering API operations during test execution."""

    AUTO = "auto"
    """Try dependency graph first, fallback to RESTful heuristic"""

    NONE = "none"
    """No ordering - operations execute in schema iteration order"""


@dataclass(repr=False)
class FuzzingPhaseConfig(DiffBase):
    enabled: bool
    generation: GenerationConfig
    checks: ChecksConfig
    operation_ordering: OperationOrdering
    extra_data_sources: ExtraDataSourcesConfig

    __slots__ = (
        "enabled",
        "generation",
        "checks",
        "operation_ordering",
        "extra_data_sources",
        "_checks_is_default",
        "_extra_data_sources_is_default",
    )

    def __init__(
        self,
        *,
        enabled: bool = True,
        generation: GenerationConfig | None = None,
        checks: ChecksConfig | None = None,
        operation_ordering: OperationOrdering | str = OperationOrdering.AUTO,
        extra_data_sources: ExtraDataSourcesConfig | None = None,
    ) -> None:
        self.enabled = enabled
        self.generation = generation or GenerationConfig()
        self.checks = checks or ChecksConfig()
        self.operation_ordering = (
            OperationOrdering(operation_ordering) if isinstance(operation_ordering, str) else operation_ordering
        )
        self.extra_data_sources = extra_data_sources or ExtraDataSourcesConfig()
        # Track whether nested configs were provided or created as defaults
        self._checks_is_default = checks is None
        self._extra_data_sources_is_default = extra_data_sources is None

    @property
    def _is_default(self) -> bool:
        """Check if this config is still in default state.

        A config is default if enabled is True, operation_ordering is AUTO,
        and all nested configs are in their default state.
        """
        return (
            self.enabled
            and self.generation._is_default
            and self._checks_is_default
            and self.operation_ordering == OperationOrdering.AUTO
            and self._extra_data_sources_is_default
        )

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> FuzzingPhaseConfig:
        return cls(
            enabled=data.get("enabled", True),
            generation=GenerationConfig.from_dict(data.get("generation", {})),
            checks=ChecksConfig.from_dict(data.get("checks", {})),
            operation_ordering=data.get("operation-ordering", "auto"),
            extra_data_sources=ExtraDataSourcesConfig.from_dict(data.get("extra-data-sources", {})),
        )


@dataclass(repr=False)
class ExamplesPhaseConfig(DiffBase):
    enabled: bool
    fill_missing: bool
    generation: GenerationConfig
    checks: ChecksConfig
    operation_ordering: OperationOrdering

    __slots__ = (
        "enabled",
        "fill_missing",
        "generation",
        "checks",
        "operation_ordering",
        "_is_default",
    )

    def __init__(
        self,
        *,
        enabled: bool = True,
        fill_missing: bool = False,
        generation: GenerationConfig | None = None,
        checks: ChecksConfig | None = None,
        operation_ordering: OperationOrdering | str = OperationOrdering.AUTO,
    ) -> None:
        self.enabled = enabled
        self.fill_missing = fill_missing
        self.generation = generation or GenerationConfig()
        self.checks = checks or ChecksConfig()
        self.operation_ordering = (
            OperationOrdering(operation_ordering) if isinstance(operation_ordering, str) else operation_ordering
        )
        self._is_default = (
            enabled
            and not fill_missing
            and generation is None
            and checks is None
            and self.operation_ordering == OperationOrdering.AUTO
        )

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ExamplesPhaseConfig:
        return cls(
            enabled=data.get("enabled", True),
            fill_missing=data.get("fill-missing", False),
            generation=GenerationConfig.from_dict(data.get("generation", {})),
            checks=ChecksConfig.from_dict(data.get("checks", {})),
            operation_ordering=data.get("operation-ordering", "auto"),
        )


@dataclass(repr=False)
class CoveragePhaseConfig(DiffBase):
    enabled: bool
    generate_duplicate_query_parameters: bool
    generation: GenerationConfig
    checks: ChecksConfig
    unexpected_methods: set[str]
    operation_ordering: OperationOrdering

    __slots__ = (
        "enabled",
        "generate_duplicate_query_parameters",
        "generation",
        "checks",
        "unexpected_methods",
        "operation_ordering",
        "_is_default",
    )

    def __init__(
        self,
        *,
        enabled: bool = True,
        generate_duplicate_query_parameters: bool = False,
        generation: GenerationConfig | None = None,
        checks: ChecksConfig | None = None,
        unexpected_methods: set[str] | None = None,
        operation_ordering: OperationOrdering | str = OperationOrdering.AUTO,
    ) -> None:
        self.enabled = enabled
        self.generate_duplicate_query_parameters = generate_duplicate_query_parameters
        self.unexpected_methods = unexpected_methods if unexpected_methods is not None else DEFAULT_UNEXPECTED_METHODS
        self.generation = generation or GenerationConfig()
        self.checks = checks or ChecksConfig()
        self.operation_ordering = (
            OperationOrdering(operation_ordering) if isinstance(operation_ordering, str) else operation_ordering
        )
        self._is_default = (
            enabled
            and not generate_duplicate_query_parameters
            and generation is None
            and checks is None
            and unexpected_methods is None
            and self.operation_ordering == OperationOrdering.AUTO
        )

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CoveragePhaseConfig:
        return cls(
            enabled=data.get("enabled", True),
            generate_duplicate_query_parameters=data.get("generate-duplicate-query-parameters", False),
            unexpected_methods={method.lower() for method in data.get("unexpected-methods", [])}
            if "unexpected-methods" in data
            else None,
            generation=GenerationConfig.from_dict(data.get("generation", {})),
            checks=ChecksConfig.from_dict(data.get("checks", {})),
            operation_ordering=data.get("operation-ordering", "auto"),
        )


class InferenceAlgorithm(str, Enum):
    LOCATION_HEADERS = "location-headers"
    DEPENDENCY_ANALYSIS = "dependency-analysis"


@dataclass(repr=False)
class InferenceConfig(DiffBase):
    algorithms: list[InferenceAlgorithm]

    __slots__ = ("algorithms",)

    def __init__(
        self,
        *,
        algorithms: list[str] | None = None,
    ) -> None:
        self.algorithms = (
            [InferenceAlgorithm(a) for a in algorithms] if algorithms is not None else list(InferenceAlgorithm)
        )

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> InferenceConfig:
        return cls(
            algorithms=data.get("algorithms", list(InferenceAlgorithm)),
        )

    @property
    def is_enabled(self) -> bool:
        """Inference is enabled if any algorithms are configured."""
        return bool(self.algorithms)

    def is_algorithm_enabled(self, algorithm: InferenceAlgorithm) -> bool:
        return algorithm in self.algorithms


@dataclass(repr=False)
class StatefulPhaseConfig(DiffBase):
    enabled: bool
    generation: GenerationConfig
    checks: ChecksConfig
    max_steps: int
    inference: InferenceConfig

    __slots__ = ("enabled", "generation", "checks", "max_steps", "inference", "_is_default")

    def __init__(
        self,
        *,
        enabled: bool = True,
        generation: GenerationConfig | None = None,
        checks: ChecksConfig | None = None,
        max_steps: int | None = None,
        inference: InferenceConfig | None = None,
    ) -> None:
        self.enabled = enabled
        self.max_steps = max_steps or DEFAULT_STATEFUL_STEP_COUNT
        self.generation = generation or GenerationConfig()
        self.checks = checks or ChecksConfig()
        self.inference = inference or InferenceConfig()
        self._is_default = enabled and generation is None and checks is None and max_steps is None and inference is None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> StatefulPhaseConfig:
        return cls(
            enabled=data.get("enabled", True),
            max_steps=data.get("max-steps"),
            generation=GenerationConfig.from_dict(data.get("generation", {})),
            checks=ChecksConfig.from_dict(data.get("checks", {})),
            inference=InferenceConfig.from_dict(data.get("inference", {})),
        )


@dataclass(repr=False)
class PhasesConfig(DiffBase):
    examples: ExamplesPhaseConfig
    coverage: CoveragePhaseConfig
    fuzzing: FuzzingPhaseConfig
    stateful: StatefulPhaseConfig

    __slots__ = ("examples", "coverage", "fuzzing", "stateful")

    def __init__(
        self,
        *,
        examples: ExamplesPhaseConfig | None = None,
        coverage: CoveragePhaseConfig | None = None,
        fuzzing: FuzzingPhaseConfig | None = None,
        stateful: StatefulPhaseConfig | None = None,
    ) -> None:
        self.examples = examples or ExamplesPhaseConfig()
        self.coverage = coverage or CoveragePhaseConfig()
        self.fuzzing = fuzzing or FuzzingPhaseConfig()
        self.stateful = stateful or StatefulPhaseConfig()

    def get_by_name(
        self, *, name: str
    ) -> FuzzingPhaseConfig | ExamplesPhaseConfig | CoveragePhaseConfig | StatefulPhaseConfig:
        return {
            "examples": self.examples,
            "coverage": self.coverage,
            "fuzzing": self.fuzzing,
            "stateful": self.stateful,
        }[name]  # type: ignore[return-value]

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PhasesConfig:
        # Use the outer "enabled" value as default for all phases.
        default_enabled = data.get("enabled", None)

        def merge(sub: dict[str, Any]) -> dict[str, Any]:
            # Merge the default enabled flag with the sub-dict; the sub-dict takes precedence.
            if default_enabled is not None:
                return {"enabled": default_enabled, **sub}
            return sub

        return cls(
            examples=ExamplesPhaseConfig.from_dict(merge(data.get("examples", {}))),
            coverage=CoveragePhaseConfig.from_dict(merge(data.get("coverage", {}))),
            fuzzing=FuzzingPhaseConfig.from_dict(merge(data.get("fuzzing", {}))),
            stateful=StatefulPhaseConfig.from_dict(merge(data.get("stateful", {}))),
        )

    def update(self, *, phases: list[str]) -> None:
        self.examples.enabled = "examples" in phases
        self.coverage.enabled = "coverage" in phases
        self.fuzzing.enabled = "fuzzing" in phases
        self.stateful.enabled = "stateful" in phases
