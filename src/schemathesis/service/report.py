import json
import tarfile
import time
from io import BytesIO
from queue import Queue
from typing import Any

import attr

from ..cli.context import ExecutionContext
from ..cli.handlers import EventHandler
from ..runner.events import ExecutionEvent, Finished
from . import ServiceClient, events
from .metadata import Metadata
from .serialization import serialize_event


@attr.s(slots=True)
class Report:
    """Schemathesis.io test run report."""

    _data: BytesIO = attr.ib(factory=BytesIO)
    _events_count: int = attr.ib(default=1)
    _tar: tarfile.TarFile = attr.ib(init=False)

    def __attrs_post_init__(self) -> None:
        # pylint: disable=consider-using-with
        self._tar = tarfile.open(mode="w:gz", fileobj=self._data)

    def _add_json_file(self, name: str, data: Any) -> None:
        buffer = BytesIO()
        buffer.write(json.dumps(data, separators=(",", ":")).encode())
        buffer.seek(0)
        info = tarfile.TarInfo(name=name)
        info.size = len(buffer.getbuffer())
        info.mtime = int(time.time())
        self._tar.addfile(info, buffer)

    def add_metadata(self, metadata: Metadata) -> None:
        self._add_json_file("metadata.json", attr.asdict(metadata))

    def add_event(self, event: ExecutionEvent) -> None:
        """Add an execution event to the report."""
        self._add_json_file(f"events/{self._events_count}.json", serialize_event(event))
        self._events_count += 1

    def finish(self) -> bytes:
        """Finish the report and get the underlying data."""
        self._tar.close()
        return self._data.getvalue()


@attr.s(slots=True)  # pragma: no mutate
class ReportHandler(EventHandler):
    client: ServiceClient = attr.ib()  # pragma: no mutate
    out_queue: Queue = attr.ib()  # pragma: no mutate
    report: Report = attr.ib(factory=Report)  # pragma: no mutate

    def handle_event(self, context: ExecutionContext, event: ExecutionEvent) -> None:
        self.report.add_event(event)
        if isinstance(event, Finished):
            try:
                self.report.add_metadata(Metadata())
                payload = self.report.finish()
                self.client.upload_report(payload)
                self.out_queue.put(events.Completed(short_url="TODO"))
            except Exception as exc:
                self.out_queue.put(events.Error(exc))
