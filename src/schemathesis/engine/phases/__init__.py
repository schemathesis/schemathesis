from __future__ import annotations

import enum
import warnings
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from schemathesis.engine.context import EngineContext
    from schemathesis.engine.events import EventGenerator


class PhaseName(enum.Enum):
    """Available execution phases."""

    PROBING = "API probing"
    EXAMPLES = "Examples"
    COVERAGE = "Coverage"
    FUZZING = "Fuzzing"
    STATEFUL_TESTING = "Stateful"

    @classmethod
    def defaults(cls) -> list[PhaseName]:
        return [PhaseName.EXAMPLES, PhaseName.COVERAGE, PhaseName.FUZZING, PhaseName.STATEFUL_TESTING]

    @classmethod
    def from_str(cls, value: str) -> PhaseName:
        return {
            "probing": cls.PROBING,
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
    is_enabled: bool = True
    skip_reason: PhaseSkipReason | None = None

    def should_execute(self, ctx: EngineContext) -> bool:
        """Determine if phase should run based on context & configuration."""
        return self.is_enabled and not ctx.has_to_stop


def execute(ctx: EngineContext, phase: Phase) -> EventGenerator:
    from urllib3.exceptions import InsecureRequestWarning

    from . import probes, stateful, unit

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", InsecureRequestWarning)

        if phase.name == PhaseName.PROBING:
            yield from probes.execute(ctx, phase)
        elif phase.name == PhaseName.EXAMPLES:
            yield from unit.execute(ctx, phase)
        elif phase.name == PhaseName.COVERAGE:
            yield from unit.execute(ctx, phase)
        elif phase.name == PhaseName.FUZZING:
            yield from unit.execute(ctx, phase)
        elif phase.name == PhaseName.STATEFUL_TESTING:
            yield from stateful.execute(ctx, phase)
