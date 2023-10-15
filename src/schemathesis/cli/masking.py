from dataclasses import dataclass

from ..masking import mask_serialized_case, mask_serialized_check, mask_serialized_interaction
from ..runner import events
from .handlers import EventHandler, ExecutionContext


@dataclass
class MaskingOutputHandler(EventHandler):
    def handle_event(self, context: ExecutionContext, event: events.ExecutionEvent) -> None:
        # TODO: CLI-specific tests
        if isinstance(event, events.AfterExecution):
            for check in event.result.checks:
                mask_serialized_check(check)
            for error in event.result.errors:
                if error.example:
                    # TODO: Check errors
                    mask_serialized_case(error.example)
            for interaction in event.result.interactions:
                # TODO: Cassettes tests
                mask_serialized_interaction(interaction)
