from __future__ import annotations

import enum
import warnings
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..context import EngineContext
    from ..events import EventGenerator


class PhaseKind(enum.Enum):
    """Available execution phases."""

    PROBING = enum.auto()
    ANALYSIS = enum.auto()
    UNIT_TESTING = enum.auto()
    STATEFUL_TESTING = enum.auto()


@dataclass
class Phase:
    """A logically separate engine execution phase."""

    kind: PhaseKind
    is_enabled: bool = True

    def execute(self, ctx: EngineContext) -> EventGenerator:
        from urllib3.exceptions import InsecureRequestWarning

        from . import analysis, probes, stateful, unit

        with warnings.catch_warnings():
            warnings.simplefilter("ignore", InsecureRequestWarning)

            if self.kind == PhaseKind.PROBING:
                yield from probes.execute(ctx)
            elif self.kind == PhaseKind.ANALYSIS:
                yield from analysis.execute(ctx)
            elif self.kind == PhaseKind.UNIT_TESTING:
                yield from unit.execute(ctx)
            elif self.kind == PhaseKind.STATEFUL_TESTING:
                yield from stateful.execute(ctx)

    def should_execute(self, ctx: EngineContext) -> bool:
        """Determine if phase should run based on context & configuration."""
        if not self.is_enabled:
            return False
        if ctx.is_stopped:
            return False
        return True
