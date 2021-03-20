import json

import attr
from click.utils import LazyFile

from ..runner import events
from .handlers import EventHandler, ExecutionContext


@attr.s(slots=True)  # pragma: no mutate
class DebugOutputHandler(EventHandler):
    file_handle: LazyFile = attr.ib()  # pragma: no mutate

    def handle_event(self, context: ExecutionContext, event: events.ExecutionEvent) -> None:
        stream = self.file_handle.open()
        stream.write(json.dumps(event.asdict()))
        stream.write("\n")

    def shutdown(self) -> None:
        self.file_handle.close()
