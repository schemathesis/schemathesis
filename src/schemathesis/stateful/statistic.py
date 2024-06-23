from __future__ import annotations

from dataclasses import dataclass

from . import events


@dataclass
class TransitionStats:
    """Statistic for transitions in a state machine."""

    def consume(self, event: events.StatefulEvent) -> None:
        raise NotImplementedError

    def copy(self) -> TransitionStats:
        """Create a copy of the statistic."""
        raise NotImplementedError

    def to_formatted_table(self, width: int) -> str:
        raise NotImplementedError
