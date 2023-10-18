from dataclasses import dataclass

from ..runner import events
from ..sanitization import sanitize_serialized_check, sanitize_serialized_interaction
from .handlers import EventHandler, ExecutionContext


@dataclass
class SanitizationHandler(EventHandler):
    def handle_event(self, context: ExecutionContext, event: events.ExecutionEvent) -> None:
        if isinstance(event, events.AfterExecution):
            for check in event.result.checks:
                sanitize_serialized_check(check)
            for interaction in event.result.interactions:
                sanitize_serialized_interaction(interaction)
