from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import TYPE_CHECKING

from . import analysis as analysis
from . import probes as probes
from . import stateful as stateful
from . import unit as unit

if TYPE_CHECKING:
    from ..context import RunnerContext


class PhaseKind(enum.Enum):
    """Available execution phases."""

    PROBING = enum.auto()
    ANALYSIS = enum.auto()
    UNIT_TESTING = enum.auto()
    STATEFUL_TESTING = enum.auto()


@dataclass
class Phase:
    """Base structure for execution phase."""

    kind: PhaseKind
    is_enabled: bool = True

    def should_run(self, ctx: RunnerContext) -> bool:
        """Determine if phase should run based on context & configuration."""
        if not self.is_enabled:
            return False
        if ctx.is_stopped:
            return False
        return True
