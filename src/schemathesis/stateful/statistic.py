from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from schemathesis.runner import events


@dataclass
class TransitionStats:
    """Statistic for transitions in a state machine."""

    def consume(self, event: events.TestEvent) -> None:
        raise NotImplementedError

    def to_formatted_table(self, width: int) -> str:
        raise NotImplementedError
