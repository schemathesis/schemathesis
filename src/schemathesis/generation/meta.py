from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from schemathesis.generation import GenerationMode


class TestPhase(str, Enum):
    __test__ = False

    EXAMPLES = "examples"
    COVERAGE = "coverage"
    FUZZING = "fuzzing"


class ComponentKind(str, Enum):
    """Components that can be generated."""

    QUERY = "query"
    PATH_PARAMETERS = "path_parameters"
    HEADERS = "headers"
    COOKIES = "cookies"
    BODY = "body"


@dataclass
class ComponentInfo:
    """Information about how a specific component was generated."""

    mode: GenerationMode

    __slots__ = ("mode",)


@dataclass
class GeneratePhaseData:
    """Metadata specific to generate phase."""


@dataclass
class ExplicitPhaseData:
    """Metadata specific to explicit phase."""


@dataclass
class CoveragePhaseData:
    """Metadata specific to coverage phase."""

    description: str
    location: str | None
    parameter: str | None
    parameter_location: str | None

    __slots__ = ("description", "location", "parameter", "parameter_location")


@dataclass
class PhaseInfo:
    """Phase-specific information."""

    name: TestPhase
    data: CoveragePhaseData | ExplicitPhaseData | GeneratePhaseData

    __slots__ = ("name", "data")

    @classmethod
    def coverage(
        cls,
        description: str,
        location: str | None = None,
        parameter: str | None = None,
        parameter_location: str | None = None,
    ) -> PhaseInfo:
        return cls(
            name=TestPhase.COVERAGE,
            data=CoveragePhaseData(
                description=description, location=location, parameter=parameter, parameter_location=parameter_location
            ),
        )

    @classmethod
    def generate(cls) -> PhaseInfo:
        return cls(name=TestPhase.FUZZING, data=GeneratePhaseData())


@dataclass
class GenerationInfo:
    """Information about test case generation."""

    time: float
    mode: GenerationMode

    __slots__ = ("time", "mode")


@dataclass
class CaseMetadata:
    """Complete metadata for generated cases."""

    generation: GenerationInfo
    components: dict[ComponentKind, ComponentInfo]
    phase: PhaseInfo

    __slots__ = ("generation", "components", "phase")

    def __init__(
        self,
        generation: GenerationInfo,
        components: dict[ComponentKind, ComponentInfo],
        phase: PhaseInfo,
    ) -> None:
        self.generation = generation
        self.components = components
        self.phase = phase
