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

    PROBING = "probing"
    SCHEMA_ANALYSIS = "schema_analysis"
    EXAMPLES = "examples"
    COVERAGE = "coverage"
    FUZZING = "fuzzing"
    STATEFUL_TESTING = "stateful"

    @classmethod
    def defaults(cls) -> list[PhaseName]:
        return [PhaseName.EXAMPLES, PhaseName.COVERAGE, PhaseName.FUZZING, PhaseName.STATEFUL_TESTING]

    @property
    def display(self) -> str:
        """Title-cased label for terminal output."""
        if self is PhaseName.PROBING:
            return "API probing"
        return self.value.replace("_", " ").capitalize()

    @classmethod
    def from_str(cls, value: str) -> PhaseName:
        return cls(value.lower())


class PhaseSkipReason(str, enum.Enum):
    """Reasons why a phase might not be executed."""

    # Explicitly disabled via config
    DISABLED = "disabled"
    # Feature not supported by schema
    NOT_SUPPORTED = "not_supported"
    # No relevant data (e.g., no transitions for stateful)
    NOT_APPLICABLE = "not_applicable"
    FAILURE_LIMIT_REACHED = "failure_limit_reached"
    NOTHING_TO_TEST = "nothing_to_test"

    @property
    def display(self) -> str:
        """Label for terminal output."""
        return self.value.replace("_", " ")


@dataclass(slots=True)
class Phase:
    """A logically separate engine execution phase."""

    name: PhaseName
    is_enabled: bool
    skip_reason: PhaseSkipReason | None

    def __init__(self, name: PhaseName, is_enabled: bool = True, skip_reason: PhaseSkipReason | None = None) -> None:
        self.name = name
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
        elif phase.name == PhaseName.EXAMPLES or phase.name == PhaseName.COVERAGE or phase.name == PhaseName.FUZZING:
            yield from unit.execute(ctx, phase)
        elif phase.name == PhaseName.STATEFUL_TESTING:
            yield from stateful.execute(ctx, phase)
