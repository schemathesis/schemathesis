from __future__ import annotations

import base64
import json
import uuid
from collections.abc import Mapping
from dataclasses import fields, is_dataclass
from io import StringIO
from pathlib import Path
from types import TracebackType
from typing import IO, TYPE_CHECKING, Any

import requests

from schemathesis.core import NOT_SET
from schemathesis.core.output.sanitization import sanitize_url, sanitize_value
from schemathesis.core.result import Err, Ok
from schemathesis.core.transforms import Unresolvable
from schemathesis.core.transport import Response
from schemathesis.core.version import SCHEMATHESIS_VERSION
from schemathesis.engine import events
from schemathesis.engine.recorder import Request as RecorderRequest

if TYPE_CHECKING:
    from schemathesis.config import SanitizationConfig

TextOutput = IO[str] | StringIO | Path

# Fields to skip during serialization per type (too large or not useful for analysis)
SKIP_FIELDS: dict[str, frozenset[str]] = {
    "LoadingFinished": frozenset({"schema", "config", "find_operation_by_label"}),
    "Case": frozenset({"operation"}),
    "NonFatalError": frozenset({"info"}),  # Duplicate of `value`
    "CheckFailureInfo": frozenset({"code_sample"}),  # Reconstructable from case + interaction
}

# Underscore-prefixed dataclass fields are skipped by default. EXPOSE_PRIVATE_FIELDS
# whitelists per-class fields to keep, mapped to the public name they emit under.
EXPOSE_PRIVATE_FIELDS: dict[str, dict[str, str]] = {
    "Case": {"_meta": "meta"},
}

# Standard request headers that are always the same and not useful for analysis
SKIP_REQUEST_HEADERS: frozenset[str] = frozenset(
    {
        "User-Agent",  # Always schemathesis/<version>
        "Accept-Encoding",  # Always gzip, deflate, zstd
        "Accept",  # Always */*
        "Connection",  # Always keep-alive
        "Content-Length",  # Derivable from body
        "X-Schemathesis-TestCaseId",  # Already the key in interactions dict
    }
)

# Response headers that are not useful for analysis
SKIP_RESPONSE_HEADERS: frozenset[str] = frozenset(
    {
        "date",  # Server timestamp, not useful
    }
)


def serialize(obj: Any, *, sanitization: SanitizationConfig | None = None) -> Any:
    """Recursively serialize objects to JSON-compatible types."""
    if obj is NOT_SET:
        return None
    if isinstance(obj, Unresolvable):
        return {"$unresolvable": True}
    if obj is None or isinstance(obj, bool | int | float | str):
        return obj
    if isinstance(obj, bytes):
        return {"$base64": base64.b64encode(obj).decode("ascii")}
    if isinstance(obj, uuid.UUID):
        return obj.hex
    if isinstance(obj, dict):
        return {k: serialize(v, sanitization=sanitization) for k, v in obj.items()}
    if isinstance(obj, Mapping):
        return {k: serialize(v, sanitization=sanitization) for k, v in obj.items()}
    if isinstance(obj, list | tuple):
        return [serialize(v, sanitization=sanitization) for v in obj]
    if isinstance(obj, Ok):
        return serialize(obj.ok(), sanitization=sanitization)
    if isinstance(obj, Err):
        return serialize(obj.err(), sanitization=sanitization)
    if isinstance(obj, Response):
        headers = serialize(obj.headers, sanitization=sanitization)
        if sanitization is not None:
            sanitize_value(headers, config=sanitization)
        # Filter out headers that are not useful for analysis
        headers = {k: v for k, v in headers.items() if k.lower() not in {h.lower() for h in SKIP_RESPONSE_HEADERS}}
        return {
            "status_code": obj.status_code,
            "headers": headers,
            "content": serialize(obj.content, sanitization=sanitization),
            "elapsed": obj.elapsed,
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
    if isinstance(obj, RecorderRequest):
        # Filter out standard headers that are not useful for analysis
        headers = {k: v for k, v in obj.headers.items() if k not in SKIP_REQUEST_HEADERS}
        if sanitization is not None:
            sanitize_value(headers, config=sanitization)
        result: dict[str, Any] = {
            "method": obj.method,
            "uri": obj.uri,
            "headers": headers,
        }
        if sanitization is not None:
            result["uri"] = sanitize_url(obj.uri, config=sanitization)
        if obj.body is not None:
            result["body"] = serialize(obj.body, sanitization=sanitization)
        return result
    if is_dataclass(obj) and not isinstance(obj, type):
        cls_name = type(obj).__name__
        skip = SKIP_FIELDS.get(cls_name, frozenset())
        expose = EXPOSE_PRIVATE_FIELDS.get(cls_name, {})
        dc_data = {}
        for field in fields(obj):
            if field.name in skip:
                continue
            if field.name.startswith("_") and field.name not in expose:
                continue
            public_name = expose.get(field.name, field.name)
            value = serialize(getattr(obj, field.name), sanitization=sanitization)
            if value is not None and value != {} and value != []:
                dc_data[public_name] = value
        return dc_data
    return str(obj)


class NdjsonWriter:
    """Write engine events to NDJSON (newline-delimited JSON) format."""

    def __init__(self, output: TextOutput, sanitization: SanitizationConfig | None = None) -> None:
        self._output = output
        self._sanitization = sanitization
        self._stream: IO[str] | None = None
        self._owned_file: IO[str] | None = None

    def open(self, seed: int | None = None, *, command: str) -> None:
        """Open the output file and write the Initialize record."""
        if isinstance(self._output, Path):
            self._owned_file = open(self._output, "w", encoding="utf-8")
            self._stream = self._owned_file
        else:
            self._stream = self._output
        data = {
            "Initialize": {
                "command": command,
                "schemathesis_version": SCHEMATHESIS_VERSION,
                "seed": seed,
            }
        }
        self._stream.write(json.dumps(data, separators=(",", ":")))
        self._stream.write("\n")
        self._stream.flush()

    def write_event(self, event: events.EngineEvent) -> None:
        """Serialize and write one engine event as a NDJSON line."""
        stream = self._stream
        assert stream is not None
        event_name = type(event).__name__
        data = {event_name: serialize(event, sanitization=self._sanitization)}
        stream.write(json.dumps(data, separators=(",", ":")))
        stream.write("\n")
        stream.flush()

    def close(self) -> None:
        """Close the output file."""
        if self._owned_file is not None:
            self._owned_file.close()
            self._owned_file = None
        self._stream = None

    def __enter__(self) -> NdjsonWriter:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        self.close()
