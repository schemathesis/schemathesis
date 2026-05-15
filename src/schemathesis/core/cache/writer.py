from __future__ import annotations

import threading
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from schemathesis.core import NOT_SET
from schemathesis.core.cache.models import Kind, Request

if TYPE_CHECKING:
    from schemathesis.generation.case import Case


@dataclass(slots=True)
class PendingEntry:
    kind: Kind
    operation: str
    request: Request
    observation_keys: list[str] = field(default_factory=list)


class CacheWriter:
    """Thread-safe buffer for cache entries; dedups by observation fingerprint."""

    __slots__ = ("_lock", "_pending", "_seen")

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._pending: list[PendingEntry] = []
        # Per-observation `(kind, operation, fingerprint)` keys for ERROR_FEEDBACK; singleton
        # `(kind, operation, None)` keys for AUTH_REQUIRED / METHOD_NOT_ALLOWED where there is
        # at most one entry per operation.
        self._seen: set[tuple[Kind, str, str | None]] = set()

    def record(
        self,
        kind: Kind,
        operation: str,
        request: Request,
        observation_keys: Iterable[str] = (),
    ) -> None:
        observation_keys = list(observation_keys)
        with self._lock:
            if observation_keys:
                # Keep only fingerprints not yet claimed by an earlier record call.
                new_keys = [key for key in observation_keys if (kind, operation, key) not in self._seen]
                if not new_keys:
                    return
                for key in new_keys:
                    self._seen.add((kind, operation, key))
                self._pending.append(
                    PendingEntry(kind=kind, operation=operation, request=request, observation_keys=new_keys)
                )
            else:
                # At most one entry per operation for auth_required / 405.
                singleton_key: tuple[Kind, str, str | None] = (kind, operation, None)
                if singleton_key in self._seen:
                    return
                self._seen.add(singleton_key)
                self._pending.append(PendingEntry(kind=kind, operation=operation, request=request))

    def drain(self) -> list[PendingEntry]:
        with self._lock:
            pending, self._pending = self._pending, []
            self._seen.clear()
            return pending

    @property
    def has_pending(self) -> bool:
        return bool(self._pending)


def request_from_case(case: Case) -> Request:
    """Extract a cache `Request` from a `Case`; `Case` typed-only to avoid runtime `generation/` dep."""
    body = case.body if case.body is not NOT_SET else None
    return Request(
        method=case.method,
        path_parameters=dict(case.path_parameters or {}),
        query=dict(case.query or {}),
        headers={str(k): str(v) for k, v in dict(case.headers or {}).items()},
        cookies=dict(case.cookies or {}),
        body=body,
    )
