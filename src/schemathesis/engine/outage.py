"""Detecting a server that stopped responding in the middle of a run."""

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
from schemathesis.engine.health import HealthState

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
    # A reset reaches whatever accepted the connection, which may be a proxy; only a response proves the server ran.
    answered: bool = False

    def as_curl_command(self) -> str:
        return build_code_sample(self.case, self.request, self.transport_kwargs)


class ServerMonitor:
    """Tracks recently sent requests, to tell a dead server from a single refused request."""

    __slots__ = ("_recent", "_lock", "_confirmation_lock", "_message", "_confirmed_by", "_health")

    def __init__(self, health: HealthState | None = None) -> None:
        self._health = health if health is not None else HealthState()
        # In send order, so the newest entries are the last ones a dying server saw.
        self._recent: deque[SentRequest] = deque(maxlen=RECENT_LIMIT)
        self._lock = threading.Lock()
        self._confirmation_lock = threading.Lock()
        self._message: str | None = None
        self._confirmed_by: requests.ConnectionError | None = None

    def track(self, case: Case, send: Callable[[], Response], *, transport_kwargs: dict[str, Any]) -> Response:
        """Send a request, remembering it for as long as it could explain an outage."""
        import requests

        sent = self._start(case, transport_kwargs=transport_kwargs)
        try:
            response = send()
        except requests.RequestException as exc:
            # Breaking after connecting still proves the server was there.
            reached_server = is_unrecoverable_network_error(exc)
            self._finish(sent, exc.request, reached_server=reached_server)
            if reached_server:
                self._health.record_transport_failure(operation_label=case.operation.label, now=time.monotonic())
            raise
        self._finish(sent, response.request, reached_server=True, answered=True)
        self._health.record_completion(
            operation_label=case.operation.label, now=time.monotonic(), elapsed=response.elapsed
        )
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
        answered: bool = False,
    ) -> None:
        if sent is None:
            return
        with self._lock:
            sent.request = request
            sent.reached_server = reached_server
            sent.answered = answered

    def is_down(self, exc: requests.ConnectionError) -> bool:
        """Whether a refused or reset connection means the server is gone for the rest of the run."""
        refused = is_connection_refused(exc)
        if not self._recent or exc.request is None or not (refused or _is_reset(exc)):
            return False
        url = str(exc.request.url)
        # An unparsable address matches nothing, since every tracked request has one.
        address = _address(url)
        with self._lock:
            recent = [item for item in self._recent if item.address == address]
        newest = list(reversed(recent))
        # A proxy may be the one resetting, and a probe cannot reach the server past it.
        if not refused and newest and _through_proxy(newest[0], url):
            return False
        answered = [item for item in newest if item.answered]
        # A server that never answered was not running to begin with; that stays a per-operation error. A refusal
        # rules out a proxy in front, so an earlier reset proves the server ran; after a reset only a response does.
        if not (answered or (refused and any(item.reached_server for item in newest))):
            return False
        # Workers refused meanwhile wait here for one verdict instead of probing the server in parallel.
        with self._confirmation_lock:
            if self._message is not None:
                return True
            if refused:
                alive = accepts_connections(newest[0].address)
            else:
                alive = answers_after_reset(newest[0].address, https=urlsplit(url).scheme == "https")
            if alive:
                return False
            # A request still in flight is the likeliest culprit, so it leads the list. Only one of them can
            # be it, and the remaining room goes to what the server is known to have seen - otherwise workers
            # left hanging by the same outage fill the list and hide the payload that caused it.
            in_flight = [item for item in newest if item.reached_server is None][:1]
            # Behind a proxy every request after the outage is reset too; the first of them is the one to show.
            first_reset = [item for item in recent if item.reached_server and not item.answered][:1]
            self._message = _render_outage(url, (in_flight + first_reset + answered)[:REPORTED_REQUESTS])
            self._confirmed_by = exc
        return True

    def confirmed_by(self, exc: BaseException) -> bool:
        """Whether this error is the one that revealed the outage, rather than one that came after it."""
        return self._confirmed_by is exc

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
    return f"{parts.scheme}://{parts.netloc} stopped responding. Last {noun} before it went away:\n\n{commands}"


def _is_reset(exc: requests.ConnectionError) -> bool:
    # A port proxy (e.g. `docker run -p`) in front of a dead process resets connections instead of refusing them.
    import requests

    return not isinstance(exc, requests.Timeout) and is_unrecoverable_network_error(exc)


def _through_proxy(sent: SentRequest, url: str) -> bool:
    import requests

    return bool(sent.transport_kwargs.get("proxies")) or bool(requests.utils.get_environ_proxies(url))


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


def answers_after_reset(address: Address, *, https: bool) -> bool:
    """Whether something behind the accepted connection is alive, since a port proxy accepts it either way."""
    for attempt in range(CONFIRMATION_ATTEMPTS):
        if attempt:
            time.sleep(CONFIRMATION_INTERVAL)
        try:
            sock = socket.create_connection(address, timeout=CONFIRMATION_TIMEOUT)
        except OSError:
            continue
        with sock:
            try:
                if https:
                    import ssl

                    context = ssl.create_default_context()
                    context.check_hostname = False
                    context.verify_mode = ssl.CERT_NONE
                    context.wrap_socket(sock, server_hostname=address[0]).close()
                    return True
                sock.sendall(b"HEAD / HTTP/1.0\r\n\r\n")
                if sock.recv(1):
                    return True
            except TimeoutError:
                # Holding the connection open without a word is a busy server; a proxy with nothing behind resets.
                return True
            except OSError:
                pass
    return False


def _address(url: str) -> Address | None:
    parts = urlsplit(url)
    if parts.hostname is None:
        return None
    return parts.hostname, parts.port or (443 if parts.scheme == "https" else 80)
