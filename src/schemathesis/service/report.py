from __future__ import annotations

import enum
import json
import os
import tarfile
import threading
import time
from contextlib import suppress
from dataclasses import asdict, dataclass, field
from io import BytesIO
from queue import Queue
from typing import TYPE_CHECKING, Any

import click

from ..cli.handlers import EventHandler
from ..runner.events import Initialized, InternalError, Interrupted
from . import ci, events, usage
from .constants import REPORT_FORMAT_VERSION, STOP_MARKER, WORKER_JOIN_TIMEOUT
from .hosts import HostData
from .metadata import Metadata
from .models import UploadResponse
from .serialization import serialize_event

if TYPE_CHECKING:
    from ..cli.context import ExecutionContext
    from ..runner.events import ExecutionEvent
    from .client import ServiceClient


@dataclass
class ReportWriter:
    """Schemathesis.io test run report.

    Simplifies adding new files to the archive.
    """

    _tar: tarfile.TarFile
    _events_count: int = 0

    def add_json_file(self, name: str, data: Any) -> None:
        buffer = BytesIO()
        buffer.write(json.dumps(data, separators=(",", ":")).encode())
        buffer.seek(0)
        info = tarfile.TarInfo(name=name)
        info.size = len(buffer.getbuffer())
        info.mtime = int(time.time())
        self._tar.addfile(info, buffer)

    def add_metadata(
        self,
        *,
        api_name: str | None,
        location: str,
        base_url: str | None,
        started_at: str,
        metadata: Metadata,
        ci_environment: ci.Environment | None,
        usage_data: dict[str, Any] | None,
    ) -> None:
        data = {
            # API identifier on the Schemathesis.io side (optional)
            "api_name": api_name,
            # The place, where the API schema is located
            "location": location,
            # The base URL against which the tests are running
            "base_url": base_url,
            # The time that the test run began
            "started_at": started_at,
            # Metadata about CLI environment
            "environment": asdict(metadata),
            # Environment variables specific for CI providers
            "ci": ci_environment.asdict() if ci_environment is not None else None,
            # CLI usage statistic
            "usage": usage_data,
            # Report format version
            "version": REPORT_FORMAT_VERSION,
        }
        self.add_json_file("metadata.json", data)

    def add_event(self, event: ExecutionEvent) -> None:
        """Add an execution event to the report."""
        self._events_count += 1
        filename = f"events/{self._events_count}-{event.__class__.__name__}.json"
        self.add_json_file(filename, serialize_event(event))


class BaseReportHandler(EventHandler):
    in_queue: Queue
    worker: threading.Thread

    def handle_event(self, context: ExecutionContext, event: ExecutionEvent) -> None:
        self.in_queue.put(event)

    def shutdown(self) -> None:
        self._stop_worker()

    def _stop_worker(self) -> None:
        self.in_queue.put(STOP_MARKER)
        self.worker.join(WORKER_JOIN_TIMEOUT)


@dataclass
class ServiceReportHandler(BaseReportHandler):
    client: ServiceClient
    host_data: HostData
    api_name: str | None
    location: str
    base_url: str | None
    started_at: str
    telemetry: bool
    out_queue: Queue
    in_queue: Queue = field(default_factory=Queue)
    worker: threading.Thread = field(init=False)

    def __post_init__(self) -> None:
        self.worker = threading.Thread(
            target=write_remote,
            kwargs={
                "client": self.client,
                "host_data": self.host_data,
                "api_name": self.api_name,
                "location": self.location,
                "base_url": self.base_url,
                "started_at": self.started_at,
                "in_queue": self.in_queue,
                "out_queue": self.out_queue,
                "usage_data": usage.collect() if self.telemetry else None,
            },
        )
        self.worker.start()


@enum.unique
class ConsumeResult(enum.Enum):
    NORMAL = 1
    INTERRUPT = 2


def consume_events(writer: ReportWriter, in_queue: Queue) -> ConsumeResult:
    while True:
        event = in_queue.get()
        if event is STOP_MARKER or isinstance(event, (Interrupted, InternalError)):
            # If the run is interrupted, or there is an internal error - do not send the report
            return ConsumeResult.INTERRUPT
        # Add every event to the report
        if isinstance(event, Initialized):
            writer.add_json_file("schema.json", event.schema)
        writer.add_event(event)
        if event.is_terminal:
            break
    return ConsumeResult.NORMAL


def write_remote(
    client: ServiceClient,
    host_data: HostData,
    api_name: str | None,
    location: str,
    base_url: str | None,
    started_at: str,
    in_queue: Queue,
    out_queue: Queue,
    usage_data: dict[str, Any] | None,
) -> None:
    """Create a compressed ``tar.gz`` file during the run & upload it to Schemathesis.io when the run is finished."""
    payload = BytesIO()
    try:
        with tarfile.open(mode="w:gz", fileobj=payload) as tar:
            writer = ReportWriter(tar)
            ci_environment = ci.environment()
            writer.add_metadata(
                api_name=api_name,
                location=location,
                base_url=base_url,
                started_at=started_at,
                metadata=Metadata(),
                ci_environment=ci_environment,
                usage_data=usage_data,
            )
            if consume_events(writer, in_queue) == ConsumeResult.INTERRUPT:
                return
        data = payload.getvalue()
        out_queue.put(events.Metadata(size=len(data), ci_environment=ci_environment))
        provider = ci_environment.provider if ci_environment is not None else None
        response = client.upload_report(data, host_data.correlation_id, provider)
        event: events.Event
        if isinstance(response, UploadResponse):
            host_data.store_correlation_id(response.correlation_id)
            event = events.Completed(message=response.message, next_url=response.next_url)
        else:
            event = events.Failed(detail=response.detail)
        out_queue.put(event)
    except Exception as exc:
        out_queue.put(events.Error(exc))


@dataclass
class FileReportHandler(BaseReportHandler):
    file_handle: click.utils.LazyFile
    api_name: str | None
    location: str
    base_url: str | None
    started_at: str
    telemetry: bool
    out_queue: Queue
    in_queue: Queue = field(default_factory=Queue)
    worker: threading.Thread = field(init=False)

    def __post_init__(self) -> None:
        self.worker = threading.Thread(
            target=write_file,
            kwargs={
                "file_handle": self.file_handle,
                "api_name": self.api_name,
                "location": self.location,
                "base_url": self.base_url,
                "started_at": self.started_at,
                "in_queue": self.in_queue,
                "out_queue": self.out_queue,
                "usage_data": usage.collect() if self.telemetry else None,
            },
        )
        self.worker.start()


def write_file(
    file_handle: click.utils.LazyFile,
    api_name: str | None,
    location: str,
    base_url: str | None,
    started_at: str,
    in_queue: Queue,
    out_queue: Queue,
    usage_data: dict[str, Any] | None,
) -> None:
    with file_handle.open() as fileobj, tarfile.open(mode="w:gz", fileobj=fileobj) as tar:
        writer = ReportWriter(tar)
        ci_environment = ci.environment()
        writer.add_metadata(
            api_name=api_name,
            location=location,
            base_url=base_url,
            started_at=started_at,
            metadata=Metadata(),
            ci_environment=ci_environment,
            usage_data=usage_data,
        )
        result = consume_events(writer, in_queue)
    if result == ConsumeResult.INTERRUPT:
        with suppress(OSError):
            os.remove(file_handle.name)
    else:
        out_queue.put(events.Metadata(size=os.path.getsize(file_handle.name), ci_environment=ci_environment))
