from __future__ import annotations

import enum
import warnings
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from schemathesis.engine.context import EngineContext
    from schemathesis.engine.events import EventGenerator


class PhaseName(str, enum.Enum):
    """Available execution phases."""

    PROBING = "API probing"
    SCHEMA_ANALYSIS = "Schema analysis"
    EXAMPLES = "Examples"
    COVERAGE = "Coverage"
    FUZZING = "Fuzzing"
    STATEFUL_TESTING = "Stateful"

    @classmethod
    def defaults(cls) -> list[PhaseName]:
        return [PhaseName.EXAMPLES, PhaseName.COVERAGE, PhaseName.FUZZING, PhaseName.STATEFUL_TESTING]

    @property
    def name(self) -> str:
        return {
            PhaseName.PROBING: "probing",
            PhaseName.SCHEMA_ANALYSIS: "schema analysis",
            PhaseName.EXAMPLES: "examples",
            PhaseName.COVERAGE: "coverage",
            PhaseName.FUZZING: "fuzzing",
            PhaseName.STATEFUL_TESTING: "stateful",
        }[self]

    @classmethod
    def from_str(cls, value: str) -> PhaseName:
        return {
            "probing": cls.PROBING,
            "schema analysis": cls.SCHEMA_ANALYSIS,
            "examples": cls.EXAMPLES,
            "coverage": cls.COVERAGE,
            "fuzzing": cls.FUZZING,
            "stateful": cls.STATEFUL_TESTING,
        }[value.lower()]


class PhaseSkipReason(str, enum.Enum):
    """Reasons why a phase might not be executed."""

    DISABLED = "disabled"  # Explicitly disabled via config
    NOT_SUPPORTED = "not supported"  # Feature not supported by schema
    NOT_APPLICABLE = "not applicable"  # No relevant data (e.g., no links for stateful)
    FAILURE_LIMIT_REACHED = "failure limit reached"
    NOTHING_TO_TEST = "nothing to test"


@dataclass
class Phase:
    """A logically separate engine execution phase."""

    name: PhaseName
    is_supported: bool
    is_enabled: bool
    skip_reason: PhaseSkipReason | None

    __slots__ = ("name", "is_supported", "is_enabled", "skip_reason")

    def __init__(
        self, name: PhaseName, is_supported: bool, is_enabled: bool = True, skip_reason: PhaseSkipReason | None = None
    ) -> None:
        self.name = name
        self.is_supported = is_supported
        self.is_enabled = is_enabled
        self.skip_reason = skip_reason

    def should_execute(self, ctx: EngineContext) -> bool:
        """Determine if phase should run based on context & configuration."""
        return self.is_enabled and not ctx.has_to_stop

    def enable(self) -> None:
        """Enable this test phase."""
        self.is_enabled = True
        self.skip_reason = None


def execute(ctx: EngineContext, phase: Phase) -> EventGenerator:
    from urllib3.exceptions import InsecureRequestWarning

    from . import analysis, probes, stateful, unit

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", InsecureRequestWarning)

        if phase.name == PhaseName.PROBING:
            yield from probes.execute(ctx, phase)
        elif phase.name == PhaseName.SCHEMA_ANALYSIS:
            yield from analysis.execute(ctx, phase)
        elif phase.name == PhaseName.EXAMPLES:
            yield from unit.execute(ctx, phase)
        elif phase.name == PhaseName.COVERAGE:
            yield from unit.execute(ctx, phase)
        elif phase.name == PhaseName.FUZZING:
            yield from unit.execute(ctx, phase)
        elif phase.name == PhaseName.STATEFUL_TESTING:
            yield from stateful.execute(ctx, phase)
