from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from . import events

if TYPE_CHECKING:
    from .statistic import TransitionStats


@dataclass
class StateMachineSink:
    """Collects events and stores data about the state machine execution."""

    transitions: TransitionStats

    def consume(self, event: events.StatefulEvent) -> None:
        self.transitions.consume(event)
