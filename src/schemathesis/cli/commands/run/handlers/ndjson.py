from __future__ import annotations

import base64
import json
import threading
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, fields, is_dataclass
from queue import Queue
from typing import TYPE_CHECKING, Any

from schemathesis.cli.commands.run.context import ExecutionContext
from schemathesis.cli.commands.run.handlers.base import EventHandler, TextOutput, open_text_output
from schemathesis.cli.commands.run.handlers.cassettes import get_command_representation
from schemathesis.core import NOT_SET
from schemathesis.core.output.sanitization import sanitize_url, sanitize_value
from schemathesis.core.result import Err, Ok
from schemathesis.core.transport import Response
from schemathesis.core.version import SCHEMATHESIS_VERSION
from schemathesis.engine import events

if TYPE_CHECKING:
    from schemathesis.config import ProjectConfig, SanitizationConfig

WRITER_WORKER_JOIN_TIMEOUT = 1

# Fields to skip during serialization per type (too large or not useful for analysis)
SKIP_FIELDS: dict[str, frozenset[str]] = {
    "LoadingFinished": frozenset({"schema", "config", "find_operation_by_label"}),
    "Case": frozenset({"operation"}),
    "NonFatalError": frozenset({"info"}),  # Duplicate of `value`
}


def serialize(obj: Any, *, sanitization: SanitizationConfig | None = None) -> Any:
    """Recursively serialize objects to JSON-compatible types."""
    import requests

    if obj is NOT_SET:
        return None
    if obj is None or isinstance(obj, (bool, int, float, str)):
        return obj
    if isinstance(obj, bytes):
        return {"$base64": base64.b64encode(obj).decode("ascii")}
    if isinstance(obj, uuid.UUID):
        return obj.hex
    if isinstance(obj, dict):
        return {k: serialize(v, sanitization=sanitization) for k, v in obj.items()}
    if isinstance(obj, Mapping):
        return {k: serialize(v, sanitization=sanitization) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [serialize(v, sanitization=sanitization) for v in obj]
    if isinstance(obj, Ok):
        return serialize(obj.ok(), sanitization=sanitization)
    if isinstance(obj, Err):
        return serialize(obj.err(), sanitization=sanitization)
    if isinstance(obj, Response):
        headers = serialize(obj.headers, sanitization=sanitization)
        if sanitization is not None:
            sanitize_value(headers, config=sanitization)
        return {
            "status_code": obj.status_code,
            "headers": headers,
            "content": serialize(obj.content, sanitization=sanitization),
            "elapsed": obj.elapsed,
            "verify": obj.verify,
            "message": obj.message,
            "http_version": obj.http_version,
            "encoding": obj.encoding,
        }
    if isinstance(obj, requests.PreparedRequest):
        url = obj.url or ""
        if sanitization is not None:
            url = sanitize_url(url, config=sanitization)
        headers = dict(obj.headers) if obj.headers else {}
        if sanitization is not None:
            sanitize_value(headers, config=sanitization)
        return {
            "method": obj.method,
            "url": url,
            "headers": headers,
            "body": serialize(obj.body, sanitization=sanitization),
        }
    if isinstance(obj, Exception):
        return {"type": type(obj).__name__, "message": str(obj)}
    if is_dataclass(obj) and not isinstance(obj, type):
        dc_data = {}
        skip = SKIP_FIELDS.get(type(obj).__name__, frozenset())
        for field in fields(obj):
            if field.name.startswith("_") or field.name in skip:
                continue
            value = serialize(getattr(obj, field.name), sanitization=sanitization)
            if value is not None and value != {} and value != []:
                dc_data[field.name] = value
        return dc_data
    return str(obj)


@dataclass(slots=True)
class Initialize:
    """Initial metadata message."""

    command: str
    schemathesis_version: str
    seed: int | None


@dataclass(slots=True)
class Event:
    """Engine event wrapper."""

    payload: events.EngineEvent


@dataclass(slots=True)
class Shutdown:
    """Signal to stop the writer thread."""

    pass


class NdjsonWriter(EventHandler):
    """Write engine events to NDJSON (newline-delimited JSON) format."""

    __slots__ = ("output", "config", "queue", "worker")

    def __init__(
        self,
        output: TextOutput,
        config: ProjectConfig,
        queue: Queue | None = None,
    ) -> None:
        self.output = output
        self.config = config
        self.queue: Queue = queue or Queue()
        sanitization = config.output.sanitization if config.output.sanitization.enabled else None
        self.worker = threading.Thread(
            name="SchemathesisNdjsonWriter",
            target=ndjson_writer,
            kwargs={
                "output": self.output,
                "queue": self.queue,
                "sanitization": sanitization,
            },
        )
        self.worker.start()

    def start(self, ctx: ExecutionContext) -> None:
        self.queue.put(
            Initialize(
                command=get_command_representation(),
                schemathesis_version=SCHEMATHESIS_VERSION,
                seed=ctx.config.seed,
            )
        )

    def handle_event(self, ctx: ExecutionContext, event: events.EngineEvent) -> None:
        self.queue.put(Event(payload=event))

    def shutdown(self, ctx: ExecutionContext) -> None:
        self.queue.put(Shutdown())
        self.worker.join(WRITER_WORKER_JOIN_TIMEOUT)


def ndjson_writer(output: TextOutput, queue: Queue, sanitization: SanitizationConfig | None) -> None:
    """Write engine events as NDJSON to a file."""
    with open_text_output(output) as stream:
        while True:
            item = queue.get()
            if isinstance(item, Initialize):
                data = {
                    "Initialize": {
                        "command": item.command,
                        "schemathesis_version": item.schemathesis_version,
                        "seed": item.seed,
                    }
                }
                stream.write(json.dumps(data, separators=(",", ":")))
                stream.write("\n")
                stream.flush()
            elif isinstance(item, Event):
                event_name = type(item.payload).__name__
                data = {event_name: serialize(item.payload, sanitization=sanitization)}
                stream.write(json.dumps(data, separators=(",", ":")))
                stream.write("\n")
                stream.flush()
            elif isinstance(item, Shutdown):
                break
