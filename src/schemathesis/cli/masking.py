from dataclasses import dataclass

from ..masking import mask_serialized_check, mask_serialized_interaction
from ..runner import events
from .handlers import EventHandler, ExecutionContext


@dataclass
class MaskingOutputHandler(EventHandler):
    def handle_event(self, context: ExecutionContext, event: events.ExecutionEvent) -> None:
        if isinstance(event, events.AfterExecution):
            for check in event.result.checks:
                mask_serialized_check(check)
            for interaction in event.result.interactions:
                mask_serialized_interaction(interaction)
