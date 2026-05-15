from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

FORMAT_VERSION = 1


class Kind(str, Enum):
    ERROR_FEEDBACK = "error_feedback"
    AUTH_REQUIRED = "auth_required"
    METHOD_NOT_ALLOWED = "method_not_allowed"


@dataclass(slots=True)
class Request:
    """Components needed to reconstruct an `operation.Case(...)` for replay."""

    method: str
    path_parameters: dict[str, Any] = field(default_factory=dict)
    query: dict[str, Any] = field(default_factory=dict)
    headers: dict[str, str] = field(default_factory=dict)
    cookies: dict[str, Any] = field(default_factory=dict)
    body: Any = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "method": self.method,
            "path_parameters": self.path_parameters,
            "query": self.query,
            "headers": self.headers,
            "cookies": self.cookies,
            "body": self.body,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Request:
        return cls(
            method=data["method"],
            path_parameters=data.get("path_parameters", {}),
            query=data.get("query", {}),
            headers=data.get("headers", {}),
            cookies=data.get("cookies", {}),
            body=data.get("body"),
        )


@dataclass(slots=True)
class Entry:
    id: int
    kind: Kind
    operation: str
    request: Request
    # Observation fingerprints this entry's request covers. Empty for AUTH_REQUIRED /
    # METHOD_NOT_ALLOWED (at most one entry per operation); populated for ERROR_FEEDBACK
    # where one request can elicit multiple observations.
    observation_keys: list[str] = field(default_factory=list)
    # Run-counter snapshot when this entry was last replayed; 0 means never. Replay picks
    # the lowest values first, rotating tail entries to the front on the next run.
    last_replayed_run: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind.value,
            "operation": self.operation,
            "observation_keys": list(self.observation_keys),
            "last_replayed_run": self.last_replayed_run,
            "request": self.request.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Entry:
        """Raises `ValueError` on unknown `kind`; callers may catch to skip forward-compat entries."""
        kind = Kind(data["kind"])
        return cls(
            id=data["id"],
            kind=kind,
            operation=data["operation"],
            request=Request.from_dict(data["request"]),
            observation_keys=data["observation_keys"],
            last_replayed_run=data["last_replayed_run"],
        )


@dataclass(slots=True)
class Manifest:
    format_version: int
    schemathesis_version: str
    schema_location: str
    base_url: str
    created_at: str
    # Monotonic counter; bumped each time `cache.run()` rewrites the file.
    next_run_id: int = 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "format_version": self.format_version,
            "schemathesis_version": self.schemathesis_version,
            "schema_location": self.schema_location,
            "base_url": self.base_url,
            "created_at": self.created_at,
            "next_run_id": self.next_run_id,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Manifest:
        """Raises `ValueError` if `format_version` is missing or unsupported."""
        format_version = data["format_version"]
        if not isinstance(format_version, int) or format_version != FORMAT_VERSION:
            raise ValueError(f"Unsupported format_version: {format_version!r}")
        return cls(
            format_version=format_version,
            schemathesis_version=data["schemathesis_version"],
            schema_location=data["schema_location"],
            base_url=data["base_url"],
            created_at=data["created_at"],
            next_run_id=data["next_run_id"],
        )
