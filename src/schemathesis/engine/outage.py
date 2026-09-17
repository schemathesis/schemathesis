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
# Enough to cover every worker's last few requests without pinning their payloads for the whole run.
RECENT_LIMIT = 50
# A crash can lag behind the request that caused it; anything older is only in `--report=har`.
REPORTED_REQUESTS = 5

Address = tuple[str, int]


@dataclass(slots=True)
class SentRequest:
    case: Case
    transport_kwargs: dict[str, Any]
    address: Address
    request: requests.PreparedRequest | requests.Request | None = None
    # `None` while in flight, `False` once the request failed without ever reaching the server.
    reached_server: bool | None = None

    def as_curl_command(self) -> str:
        return build_code_sample(self.case, self.request, self.transport_kwargs)


class ServerMonitor:
    """Tracks recently sent requests, to tell a dead server from a single refused request."""

    __slots__ = ("_recent", "_lock", "_confirmation_lock", "_message")

    def __init__(self) -> None:
        # In send order, so the newest entries are the last ones a dying server saw.
        self._recent: deque[SentRequest] = deque(maxlen=RECENT_LIMIT)
        self._lock = threading.Lock()
        self._confirmation_lock = threading.Lock()
        self._message: str | None = None

    def track(self, case: Case, send: Callable[[], Response], *, transport_kwargs: dict[str, Any]) -> Response:
        """Send a request, remembering it for as long as it could explain an outage."""
        import requests

        sent = self._start(case, transport_kwargs=transport_kwargs)
        try:
            response = send()
        except requests.RequestException as exc:
            # Breaking after connecting still proves the server was there.
            self._finish(sent, exc.request, reached_server=is_unrecoverable_network_error(exc))
            raise
        self._finish(sent, response.request, reached_server=True)
        return response

    def _start(self, case: Case, *, transport_kwargs: dict[str, Any]) -> SentRequest | None:
        """Remember a request before it goes out, so the one that never comes back is remembered too."""
        base_url = case.operation.base_url
        address = _address(base_url) if base_url is not None else None
        if address is None:
            return None
        sent = SentRequest(case=case, transport_kwargs=transport_kwargs, address=address)
        with self._lock:
            self._recent.append(sent)
        return sent

    def _finish(
        self,
        sent: SentRequest | None,
        request: requests.PreparedRequest | requests.Request | None,
        *,
        reached_server: bool,
    ) -> None:
        if sent is None:
            return
        with self._lock:
            sent.request = request
            sent.reached_server = reached_server

    def is_down(self, exc: requests.ConnectionError) -> bool:
        """Whether a refused connection means the server is gone for the rest of the run."""
        if not self._recent or exc.request is None or not is_connection_refused(exc):
            return False
        url = str(exc.request.url)
        # An unparsable address matches nothing, since every tracked request has one.
        refused = _address(url)
        with self._lock:
            recent = [item for item in self._recent if item.address == refused]
        newest = list(reversed(recent))
        answered = [item for item in newest if item.reached_server]
        # A server that never answered was not running to begin with; that stays a per-operation error.
        if not answered:
            return False
        # Workers refused meanwhile wait here for one verdict instead of probing the server in parallel.
        with self._confirmation_lock:
            if self._message is not None:
                return True
            if accepts_connections(answered[0].address):
                return False
            # A request still in flight is the likeliest culprit, so it leads the list. Only one of them can
            # be it, and the remaining room goes to what the server is known to have seen - otherwise workers
            # left hanging by the same outage fill the list and hide the payload that caused it.
            in_flight = [item for item in newest if item.reached_server is None][:1]
            self._message = _render_outage(url, (in_flight + answered)[:REPORTED_REQUESTS])
        return True

    def take_report(self, phase: PhaseName) -> events.NonFatalError | None:
        """The outage error, or nothing while the server is still answering."""
        if self._message is None:
            return None
        return events.NonFatalError(
            error=ServerUnavailable(self._message), phase=phase, label=SERVER_LABEL, related_to_operation=False
        )


def _render_outage(url: str, candidates: list[SentRequest]) -> str:
    parts = urlsplit(url)
    noun = "request" if len(candidates) == 1 else "requests"
    commands = "\n\n".join(textwrap.indent(item.as_curl_command(), "    ") for item in candidates)
    return (
        f"{parts.scheme}://{parts.netloc} stopped accepting connections. Last {noun} before it went away:\n\n{commands}"
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
