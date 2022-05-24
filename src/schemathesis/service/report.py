import json
import tarfile
import threading
import time
from io import BytesIO
from queue import Queue
from typing import Any, Optional

import attr

from ..cli.context import ExecutionContext
from ..cli.handlers import EventHandler
from ..runner.events import ExecutionEvent, Initialized, InternalError, Interrupted
from . import ServiceClient, events
from .constants import REPORT_FORMAT_VERSION, STOP_MARKER, WORKER_JOIN_TIMEOUT
from .hosts import HostData
from .metadata import Metadata
from .serialization import serialize_event


@attr.s(slots=True)
class ReportWriter:
    """Schemathesis.io test run report.

    Simplifies adding new files to the archive.
    """

    _tar: tarfile.TarFile = attr.ib()
    _events_count: int = attr.ib(default=0)

    def add_json_file(self, name: str, data: Any) -> None:
        buffer = BytesIO()
        buffer.write(json.dumps(data, separators=(",", ":")).encode())
        buffer.seek(0)
        info = tarfile.TarInfo(name=name)
        info.size = len(buffer.getbuffer())
        info.mtime = int(time.time())
        self._tar.addfile(info, buffer)

    def add_metadata(self, *, api_name: Optional[str], location: str, base_url: str, metadata: Metadata) -> None:
        data = {
            # API identifier on the Schemathesis.io side (optional)
            "api_name": api_name,
            # The place, where the API schema is located
            "location": location,
            # The base URL against which the tests are running
            "base_url": base_url,
            # Metadata about CLI environment
            "environment": attr.asdict(metadata),
            # Report format version
            "version": REPORT_FORMAT_VERSION,
        }
        self.add_json_file("metadata.json", data)

    def add_event(self, event: ExecutionEvent) -> None:
        """Add an execution event to the report."""
        self._events_count += 1
        filename = f"events/{self._events_count}-{event.__class__.__name__}.json"
        self.add_json_file(filename, serialize_event(event))


@attr.s(slots=True)  # pragma: no mutate
class ReportHandler(EventHandler):
    client: ServiceClient = attr.ib()  # pragma: no mutate
    host_data: HostData = attr.ib()  # pragma: no mutate
    api_name: Optional[str] = attr.ib()  # pragma: no mutate
    location: str = attr.ib()  # pragma: no mutate
    base_url: Optional[str] = attr.ib()  # pragma: no mutate
    out_queue: Queue = attr.ib()  # pragma: no mutate
    in_queue: Queue = attr.ib(factory=Queue)  # pragma: no mutate
    worker: threading.Thread = attr.ib(init=False)  # pragma: no mutate

    def __attrs_post_init__(self) -> None:
        self.worker = threading.Thread(
            target=start,
            kwargs={
                "client": self.client,
                "host_data": self.host_data,
                "api_name": self.api_name,
                "location": self.location,
                "base_url": self.base_url,
                "in_queue": self.in_queue,
                "out_queue": self.out_queue,
            },
        )
        self.worker.start()

    def handle_event(self, context: ExecutionContext, event: ExecutionEvent) -> None:
        self.in_queue.put(event)

    def shutdown(self) -> None:
        self._stop_worker()

    def _stop_worker(self) -> None:
        self.in_queue.put(STOP_MARKER)
        self.worker.join(WORKER_JOIN_TIMEOUT)


def start(
    client: ServiceClient,
    host_data: HostData,
    api_name: Optional[str],
    location: str,
    base_url: str,
    in_queue: Queue,
    out_queue: Queue,
) -> None:
    """Create a compressed ``tar.gz`` file during the run & upload it to Schemathesis.io when the run is finished."""
    payload = BytesIO()
    try:
        with tarfile.open(mode="w:gz", fileobj=payload) as tar:
            writer = ReportWriter(tar)
            writer.add_metadata(api_name=api_name, location=location, base_url=base_url, metadata=Metadata())
            while True:
                event = in_queue.get()
                if event is STOP_MARKER or isinstance(event, (Interrupted, InternalError)):
                    # If the run is interrupted, or there is an internal error - do not send the report
                    return
                # Add every event to the report
                if isinstance(event, Initialized):
                    writer.add_json_file("schema.json", event.schema)
                writer.add_event(event)
                if event.is_terminal:
                    break
        response = client.upload_report(payload.getvalue(), host_data.correlation_id)
        host_data.store_correlation_id(response.correlation_id)
        event = events.Completed(message=response.message, next_url=response.next_url)
        out_queue.put(event)
    except Exception as exc:
        out_queue.put(events.Error(exc))
