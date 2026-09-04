from __future__ import annotations

import atexit
import http
import io
import math
import threading
import types
from collections.abc import Awaitable, Callable
from concurrent.futures import TimeoutError as FutureTimeoutError
from contextlib import ExitStack
from typing import TYPE_CHECKING, Any
from urllib.parse import unquote, urljoin, urlsplit

import anyio
import requests
import requests.adapters
import urllib3
from anyio.from_thread import start_blocking_portal
from anyio.streams.stapled import StapledObjectStream
from urllib3.exceptions import ReadTimeoutError

from schemathesis.core.compat import BaseExceptionGroup

if TYPE_CHECKING:
    from concurrent.futures import Future

    from anyio.abc import BlockingPortal

Message = dict[str, Any]
Scope = dict[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]

# A lifespan task ends as soon as the application returns, so waiting on it is a formality.
_TASK_RESULT_TIMEOUT = 5

_LOCK = threading.Lock()
_STACK = ExitStack()
_PORTAL: BlockingPortal | None = None
# Keyed by `id(app)` so applications that are unhashable or reject weak references still work. A started
# lifespan keeps its application alive for the rest of the process, which is what makes the key stable.
_LIFESPANS: dict[int, _Lifespan] = {}
_STARTUP_LOCKS: dict[int, threading.Lock] = {}


def _get_portal() -> BlockingPortal:
    # A single event loop thread is shared by every application, so requests do not pay for starting one.
    global _PORTAL

    portal = _PORTAL
    if portal is not None:
        return portal
    with _LOCK:
        if _PORTAL is None:
            _PORTAL = _STACK.enter_context(start_blocking_portal())
            atexit.register(_shutdown)
        return _PORTAL


def shutdown_lifespans() -> None:
    with _LOCK:
        # Dropped from the registry first, so a failing shutdown cannot be retried or repeated.
        lifespans = [lifespan for lifespan in _LIFESPANS.values() if lifespan.error is None]
        _LIFESPANS.clear()

    errors = []
    for lifespan in lifespans:
        try:
            lifespan.stop()
        except Exception as exc:
            errors.append(exc)
    if len(errors) == 1:
        raise errors[0]
    if errors:
        raise BaseExceptionGroup("ASGI lifespan shutdown failed", errors)


def _shutdown() -> None:
    global _PORTAL

    try:
        shutdown_lifespans()
    except Exception:
        # Nothing can act on a shutdown failure at interpreter exit, and raising here would only
        # replace the process exit status with an unattributable traceback.
        pass
    finally:
        with _LOCK:
            _STACK.close()
            _PORTAL = None


class _Lifespan:
    __slots__ = ("app", "portal", "receive_stream", "send_stream", "task", "error", "state")

    def __init__(self, app: ASGIApp, portal: BlockingPortal) -> None:
        self.app = app
        self.portal = portal
        # `receive_stream` carries messages to the application, `send_stream` carries its replies back.
        self.receive_stream: StapledObjectStream = StapledObjectStream(*anyio.create_memory_object_stream(math.inf))
        self.send_stream: StapledObjectStream = StapledObjectStream(*anyio.create_memory_object_stream(math.inf))
        self.task: Future[None] | None = None
        self.error: BaseException | None = None
        # Filled in by the application during startup; each request gets a shallow copy.
        self.state: dict[str, Any] = {}

    def start(self) -> None:
        self.task = self.portal.start_task_soon(self._run)
        self.portal.call(self._wait_startup)

    def stop(self) -> None:
        # An application that stopped handling lifespan messages will never reply to a shutdown.
        if self.task is None or not self.task.done():
            self.portal.call(self._wait_shutdown)
        if self.task is not None:
            try:
                # Re-raises a failure the application hit after startup or after reporting shutdown
                self.task.result(timeout=_TASK_RESULT_TIMEOUT)
            except FutureTimeoutError:
                pass

    async def _run(self) -> None:
        scope: Scope = {"type": "lifespan", "asgi": {"version": "3.0"}, "state": self.state}
        try:
            await self.app(scope, self.receive_stream.receive, self.send_stream.send)
        finally:
            await self.send_stream.send(None)

    async def _receive(self) -> Message | None:
        # `None` means the application stopped handling lifespan messages; it may have failed doing so.
        message = await self.send_stream.receive()
        if message is None:
            assert self.task is not None
            # Re-raises whatever the application failed with
            self.task.result()
        return message

    async def _wait_startup(self) -> None:
        await self.receive_stream.send({"type": "lifespan.startup"})
        message = await self._receive()
        if message is None:
            return
        assert message["type"] in ("lifespan.startup.complete", "lifespan.startup.failed")
        if message["type"] == "lifespan.startup.failed":
            await self._receive()
            raise RuntimeError(message.get("message") or "ASGI application failed to start")

    async def _wait_shutdown(self) -> None:
        await self.receive_stream.send({"type": "lifespan.shutdown"})
        message = await self._receive()
        if message is None:
            return
        assert message["type"] in ("lifespan.shutdown.complete", "lifespan.shutdown.failed")
        await self._receive()


def _lifespan_state(app: ASGIApp) -> dict[str, Any]:
    lifespan = _LIFESPANS.get(id(app))
    return dict(lifespan.state) if lifespan is not None else {}


def _start_lifespan(app: ASGIApp) -> None:
    key = id(app)

    started = _LIFESPANS.get(key)
    if started is None:
        portal = _get_portal()
        with _LOCK:
            started = _LIFESPANS.get(key)
            # Startup runs outside `_LOCK` so a slow application does not block requests to other ones.
            startup_lock = _STARTUP_LOCKS.setdefault(key, threading.Lock())
    if started is not None:
        if started.error is not None:
            raise started.error
        return

    with startup_lock:
        started = _LIFESPANS.get(key)
        if started is not None:
            if started.error is not None:
                raise started.error
            return
        lifespan = _Lifespan(app, portal)
        # Recorded before startup runs, so a failure is reported to every caller instead of being retried
        # once per request, and a half-started application is not left running past exit.
        _LIFESPANS[key] = lifespan
        try:
            lifespan.start()
        except BaseException as exc:
            lifespan.error = exc
            raise


def _reason_phrase(status_code: int) -> str:
    try:
        return http.HTTPStatus(status_code).phrase
    except ValueError:
        return ""


class _Headers:
    __slots__ = ("headers",)

    def __init__(self, headers: list[tuple[str, str]]) -> None:
        self.headers = headers

    def get_all(self, name: str, default: list[str] | None = None) -> list[str] | None:
        matches = [value for key, value in self.headers if key.lower() == name.lower()]
        return matches or default


class _OriginalResponse:
    # `requests` extracts cookies through the underlying `urllib3` response, and `urllib3` closes it
    # whenever reading the body fails.
    __slots__ = ("msg", "closed")

    def __init__(self, headers: list[tuple[str, str]]) -> None:
        self.msg = _Headers(headers)
        self.closed = False

    def isclosed(self) -> bool:
        return self.closed

    def close(self) -> None:
        self.closed = True


class _ASGIAdapter(requests.adapters.HTTPAdapter):
    def __init__(self, app: ASGIApp) -> None:
        self.app = app
        self.entered = False

    def close(self) -> None:
        pass

    def send(
        self, request: requests.PreparedRequest, *args: Any, timeout: Any = None, **kwargs: Any
    ) -> requests.Response:
        if self.entered and id(self.app) not in _LIFESPANS:
            # The lifespan was ended by something outside this `with` block; the block still owns one.
            _start_lifespan(self.app)

        parts = urlsplit(str(request.url))
        scheme, path, query = parts.scheme, parts.path, parts.query
        default_port = {"http": 80, "https": 443}[scheme]
        host = parts.hostname or ""
        port = parts.port or default_port
        # IPv6 literals keep their brackets in a `Host` header but not in the ASGI scope.
        header_host = f"[{host}]" if ":" in host else host

        if "host" in request.headers:
            headers: list[tuple[bytes, bytes]] = []
        elif port == default_port:
            headers = [(b"host", header_host.encode())]
        else:
            headers = [(b"host", f"{header_host}:{port}".encode())]
        headers += [(key.lower().encode(), value.encode()) for key, value in request.headers.items()]

        scope: Scope = {
            "type": "http",
            "http_version": "1.1",
            "method": request.method,
            "path": unquote(path),
            "raw_path": path.encode(),
            "root_path": "",
            "scheme": scheme,
            "query_string": query.encode(),
            "headers": headers,
            "client": ["testclient", 50000],
            "server": [host, port],
            "state": _lifespan_state(self.app),
        }

        request_complete = False
        response_started = False
        response_complete: anyio.Event
        # `request_method` lets `urllib3` skip content-length enforcement for the empty HEAD body.
        raw_kwargs: dict[str, Any] = {"body": io.BytesIO(), "request_method": request.method}

        async def receive() -> Message:
            nonlocal request_complete

            if request_complete:
                if not response_complete.is_set():
                    await response_complete.wait()
                return {"type": "http.disconnect"}

            body: Any = request.body
            if isinstance(body, str):
                body_bytes: bytes = body.encode("utf-8")
            elif body is None:
                body_bytes = b""
            elif isinstance(body, types.GeneratorType):
                try:
                    chunk = body.send(None)
                    if isinstance(chunk, str):
                        chunk = chunk.encode("utf-8")
                    return {"type": "http.request", "body": chunk, "more_body": True}
                except StopIteration:
                    request_complete = True
                    return {"type": "http.request", "body": b""}
            else:
                body_bytes = body

            request_complete = True
            return {"type": "http.request", "body": body_bytes}

        async def send(message: Message) -> None:
            nonlocal response_started

            if message["type"] == "http.response.start":
                assert not response_started, 'Received multiple "http.response.start" messages.'
                raw_kwargs["version"] = 11
                raw_kwargs["status"] = message["status"]
                raw_kwargs["reason"] = _reason_phrase(message["status"])
                raw_kwargs["headers"] = [(key.decode(), value.decode()) for key, value in message.get("headers", [])]
                raw_kwargs["preload_content"] = False
                raw_kwargs["original_response"] = _OriginalResponse(raw_kwargs["headers"])
                response_started = True
            elif message["type"] == "http.response.body":
                assert response_started, 'Received "http.response.body" without "http.response.start".'
                assert not response_complete.is_set(), 'Received "http.response.body" after response completed.'
                if request.method != "HEAD":
                    raw_kwargs["body"].write(message.get("body", b""))
                if not message.get("more_body", False):
                    raw_kwargs["body"].seek(0)
                    response_complete.set()

        # `requests` uses a (connect, read) pair; only the read half means anything without a socket.
        read_timeout = timeout[1] if isinstance(timeout, tuple) else timeout

        async def run() -> None:
            if read_timeout is None:
                await self.app(scope, receive, send)
                return
            with anyio.fail_after(read_timeout):
                await self.app(scope, receive, send)

        portal = _get_portal()
        response_complete = portal.call(anyio.Event)
        try:
            portal.call(run)
        except TimeoutError:
            error = ReadTimeoutError(
                f"{scheme}://{host}:{port}",  # type: ignore[arg-type]
                request.url,
                f"Read timed out. (read timeout={read_timeout})",
            )
            raise requests.exceptions.ReadTimeout(error, request=request) from None

        assert response_started, "The application did not return a response."

        raw = urllib3.HTTPResponse(**raw_kwargs)
        return self.build_response(request, raw)


class ASGIClient(requests.Session):
    def __init__(self, app: ASGIApp, base_url: str = "http://testserver") -> None:
        super().__init__()
        self.adapter = _ASGIAdapter(app)
        self.mount("http://", self.adapter)
        self.mount("https://", self.adapter)
        self.headers.update({"user-agent": "testclient"})
        self.app = app
        self.base_url = base_url

    def request(self, method: str | bytes, url: str | bytes, *args: Any, **kwargs: Any) -> requests.Response:
        if isinstance(url, bytes):
            url = url.decode("utf-8")
        return super().request(method, urljoin(self.base_url, url), *args, **kwargs)

    def __enter__(self) -> ASGIClient:
        _start_lifespan(self.app)
        self.adapter.entered = True
        return self

    def __exit__(self, *args: Any) -> None:
        self.adapter.entered = False
        super().__exit__(*args)


def get_client(app: ASGIApp) -> ASGIClient:
    return ASGIClient(app)
