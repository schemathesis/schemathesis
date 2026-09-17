"""Detecting a server that stopped accepting connections in the middle of a run."""

from __future__ import annotations

import socket
import textwrap
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from schemathesis.engine import events
from schemathesis.engine.errors import (
    ServerUnavailable,
    build_code_sample,
    is_connection_refused,
    is_unrecoverable_network_error,
)

if TYPE_CHECKING:
    import requests

    from schemathesis.core.transport import Response
    from schemathesis.engine.run import PhaseName
    from schemathesis.generation.case import Case

SERVER_LABEL = "Server"
CONFIRMATION_ATTEMPTS = 3
CONFIRMATION_INTERVAL = 0.5
CONFIRMATION_TIMEOUT = 1.0
# A crash can lag behind the request that caused it; anything older is only in `--report=har`.
REQUESTS_PER_WORKER = 5

Address = tuple[str, int]


@dataclass(slots=True)
class SentRequest:
    case: Case
    request: requests.PreparedRequest | requests.Request | None
    transport_kwargs: dict[str, Any]
    address: Address
    order: int

    def as_curl_command(self) -> str:
        return build_code_sample(self.case, self.request, self.transport_kwargs)


@dataclass(slots=True)
class ServerOutage:
    origin: str
    candidates: list[SentRequest]

    def render(self) -> str:
        noun = "request" if len(self.candidates) == 1 else "requests"
        commands = "\n\n".join(textwrap.indent(item.as_curl_command(), "    ") for item in self.candidates)
        return f"{self.origin} stopped accepting connections. Last {noun} before it went away:\n\n{commands}"


class ServerMonitor:
    """Tracks what each worker last sent, to tell a dead server from a refused request."""

    __slots__ = ("_workers", "_order", "_lock", "_confirmation_lock", "_outage", "_is_reported")

    def __init__(self) -> None:
        # Per worker thread, requests that reached the server: answered, or failed after connecting.
        self._workers: dict[str, deque[SentRequest]] = {}
        self._order = 0
        self._lock = threading.Lock()
        self._confirmation_lock = threading.Lock()
        self._outage: ServerOutage | None = None
        self._is_reported = False

    def track(self, case: Case, send: Callable[[], Response], *, transport_kwargs: dict[str, Any]) -> Response:
        """Send a request and remember it when it reached the server."""
        import requests

        try:
            response = send()
        except requests.RequestException as exc:
            if is_unrecoverable_network_error(exc):
                self.record(case, exc.request, transport_kwargs=transport_kwargs)
            raise
        self.record(case, response.request, transport_kwargs=transport_kwargs)
        return response

    def record(
        self,
        case: Case,
        request: requests.PreparedRequest | requests.Request | None,
        *,
        transport_kwargs: dict[str, Any],
    ) -> None:
        url = str(request.url) if request is not None else case.operation.base_url
        address = _address(url) if url is not None else None
        if address is None:
            return
        name = threading.current_thread().name
        with self._lock:
            self._order += 1
            sent = SentRequest(
                case=case, request=request, transport_kwargs=transport_kwargs, address=address, order=self._order
            )
            self._workers.setdefault(name, deque(maxlen=REQUESTS_PER_WORKER)).append(sent)

    def is_down(self, exc: requests.ConnectionError, *, workers: int) -> bool:
        """Whether a refused connection means the server is gone for the rest of the run."""
        if exc.request is None or not is_connection_refused(exc):
            return False
        url = str(exc.request.url)
        address = _address(url)
        if address is None:
            return False
        with self._lock:
            histories = [[item for item in sent if item.address == address] for sent in self._workers.values()]
        # Most recently active workers first.
        histories = sorted(filter(None, histories), key=lambda sent: sent[-1].order, reverse=True)[:workers]
        # A server that never answered was not running to begin with; that stays a per-operation error.
        if not histories:
            return False
        # Workers refused meanwhile wait here for one verdict instead of probing the server in parallel.
        with self._confirmation_lock:
            if self._outage is not None:
                return True
            if accepts_connections(address):
                return False
            parts = urlsplit(url)
            candidates = sorted(
                (item for sent in histories for item in sent),
                key=lambda item: item.order,
                reverse=True,
            )
            self._outage = ServerOutage(origin=f"{parts.scheme}://{parts.netloc}", candidates=candidates)
        return True

    def take_report(self, phase: PhaseName) -> events.NonFatalError | None:
        """The outage error, handed out once for the whole run."""
        with self._lock:
            outage = self._outage
            if outage is None or self._is_reported:
                return None
            self._is_reported = True
        return events.NonFatalError(
            error=ServerUnavailable(outage.render()), phase=phase, label=SERVER_LABEL, related_to_operation=False
        )


def accepts_connections(address: Address) -> bool:
    for attempt in range(CONFIRMATION_ATTEMPTS):
        if attempt:
            time.sleep(CONFIRMATION_INTERVAL)
        try:
            socket.create_connection(address, timeout=CONFIRMATION_TIMEOUT).close()
        except OSError:
            continue
        return True
    return False


def _address(url: str) -> Address | None:
    parts = urlsplit(url)
    if parts.hostname is None:
        return None
    return parts.hostname, parts.port or (443 if parts.scheme == "https" else 80)
