from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from schemathesis.generation._methods import DataGenerationMethod


class TestPhase(str, Enum):
    __test__ = False

    EXPLICIT = "explicit"
    COVERAGE = "coverage"
    GENERATE = "generate"


@dataclass
class GenerationMetadata:
    """Stores various information about how data is generated."""

    query: DataGenerationMethod | None
    path_parameters: DataGenerationMethod | None
    headers: DataGenerationMethod | None
    cookies: DataGenerationMethod | None
    body: DataGenerationMethod | None
    phase: TestPhase
    # Temporary attributes to carry info specific to the coverage phase
    description: str | None
    location: str | None
    parameter: str | None
    parameter_location: str | None

    __slots__ = (
        "query",
        "path_parameters",
        "headers",
        "cookies",
        "body",
        "phase",
        "description",
        "location",
        "parameter",
        "parameter_location",
    )