from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..runner import events
    from .context import ExecutionContext


class EventHandler:
    def handle_event(self, context: ExecutionContext, event: events.ExecutionEvent) -> None:
        raise NotImplementedError

    def shutdown(self) -> None:
        # Do nothing by default
        pass
