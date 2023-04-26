import json
from dataclasses import dataclass

from click.utils import LazyFile

from ..runner import events
from .handlers import EventHandler, ExecutionContext


@dataclass
class DebugOutputHandler(EventHandler):
    file_handle: LazyFile

    def handle_event(self, context: ExecutionContext, event: events.ExecutionEvent) -> None:
        stream = self.file_handle.open()
        data = event.asdict()
        stream.write(json.dumps(data))
        stream.write("\n")

    def shutdown(self) -> None:
        self.file_handle.close()
