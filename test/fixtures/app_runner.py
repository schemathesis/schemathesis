from __future__ import annotations

import os
import socket
import ssl
import subprocess
import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from time import monotonic, sleep, time
from typing import TYPE_CHECKING, Any

import pytest
import requests
import trustme
import uvicorn
from werkzeug.serving import BaseWSGIServer, make_server

if TYPE_CHECKING:
    from flask import Flask
    from starlette.types import ASGIApp


COVERAGE_ENV_VARS = ("COVERAGE_PROCESS_START", "COVERAGE_FILE")
# Every live server keeps a thread, and free-threaded builds hold megabytes per thread, so servers stop with their test.
_SERVERS: list[BaseWSGIServer | uvicorn.Server] = []


def _serve(server: BaseWSGIServer) -> int:
    _SERVERS.append(server)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server.server_port


def _shutdown(server: BaseWSGIServer) -> None:
    server.shutdown()
    server.server_close()


def _stop(server: BaseWSGIServer | uvicorn.Server) -> None:
    if isinstance(server, uvicorn.Server):
        server.should_exit = True
    else:
        # `shutdown` waits up to the server's poll interval; doing it in the background keeps teardown instant.
        threading.Thread(target=_shutdown, args=(server,), daemon=True).start()


@pytest.fixture(autouse=True)
def stop_test_servers():
    # Servers from wider-scoped fixtures were registered before this point and keep running.
    started = len(_SERVERS)
    yield
    for server in _SERVERS[started:]:
        _stop(server)
    del _SERVERS[started:]


def unused_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_for_port(port: int, *, timeout: float = 5.0, interval: float = 0.01) -> None:
    deadline = monotonic() + timeout
    while monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=interval):
                return
        except OSError:
            sleep(interval)
    raise RuntimeError(f"Server on port {port} failed to start within {timeout}s")


def run(target: Callable, port: int | None = None, timeout: float = 5.0, wait: bool = True, **kwargs: Any) -> int:
    """Start a daemon thread running the given server target on a free port."""
    if port is None:
        port = unused_port()
    server_thread = threading.Thread(target=target, kwargs={"port": port, **kwargs})
    server_thread.daemon = True
    server_thread.start()
    if wait:
        wait_for_port(port, timeout=timeout)
    return port


class SubprocessRunner:
    def __init__(self, tmp_path: Path):
        self.tmp_path = tmp_path
        self._processes: list[subprocess.Popen] = []

    def run_app(
        self,
        content: str,
        port: int | None = None,
        timeout: float = 5.0,
        wait_for_server: bool = True,
        filename: str = "app.py",
        env: dict[str, str] | None = None,
    ) -> int:
        if port is None:
            port = unused_port()

        filepath = self.tmp_path / filename
        filepath.write_text(content)

        process_env = {key: value for key, value in os.environ.items() if key not in COVERAGE_ENV_VARS}
        process_env["PORT"] = str(port)
        if env:
            process_env.update(env)

        process = subprocess.Popen(
            [sys.executable, str(filepath)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=process_env,
        )

        self._processes.append(process)

        if wait_for_server:
            self._wait_for_server(port, timeout)

        return port

    def _wait_for_server(self, port: int, timeout: float) -> None:
        """Wait for server to be ready to accept connections."""
        start_time = time()
        while time() - start_time < timeout:
            try:
                requests.get(f"http://127.0.0.1:{port}/", timeout=1)
                return
            except requests.exceptions.RequestException:
                if time() - start_time >= timeout:
                    raise
                sleep(0.1)

        if self._processes:
            process = self._processes[-1]
            if process.poll() is not None:
                stdout, stderr = process.communicate()
                raise RuntimeError(f"Server process exited early. Stdout: {stdout}, Stderr: {stderr}")

        raise RuntimeError(f"Server on port {port} failed to start within {timeout}s")

    def cleanup(self) -> None:
        """Cleanup all subprocess resources."""
        for process in self._processes:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
        self._processes.clear()


@pytest.fixture
def subprocess_runner(tmp_path):
    runner = SubprocessRunner(tmp_path)
    yield runner
    runner.cleanup()


def run_flask_app(app: Flask, port: int | None = None, timeout: float = 5.0, wait: bool = True) -> int:
    """Start a thread with the given Flask application."""
    if port is not None or not wait:
        return run(app.run, port=port, timeout=timeout, wait=wait)
    # Binding port 0 inside the server leaves no window for another socket to take a pre-picked port.
    return _serve(make_server("127.0.0.1", 0, app, threaded=True))


def run_https_flask_app(app: Flask) -> int:
    """Start `app` over HTTPS with a certificate from an untrusted CA, so only unverified clients get through."""
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    trustme.CA().issue_cert("127.0.0.1").configure_cert(context)
    return _serve(make_server("127.0.0.1", 0, app, threaded=True, ssl_context=context))


def run_client_cert_flask_app(app: Flask, ca: trustme.CA) -> int:
    """Start `app` over HTTPS with a certificate from `ca`, accepting only clients presenting a certificate from it."""
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ca.issue_cert("127.0.0.1").configure_cert(context)
    ca.configure_trust(context)
    context.verify_mode = ssl.CERT_REQUIRED
    return _serve(make_server("127.0.0.1", 0, app, threaded=True, ssl_context=context))


def openapi_url(app: Flask, *, path: str = "/openapi.json", wait: bool = True) -> str:
    """Start `app` on a free port and return the URL where the OpenAPI schema is served."""
    port = run_flask_app(app, wait=wait)
    return f"http://127.0.0.1:{port}{path}"


def run_asgi_app(app: ASGIApp, port: int | None = None, timeout: float = 5.0, wait: bool = True) -> int:
    """Start a daemon thread running uvicorn against the given ASGI application."""
    sockets = []
    if port is None:
        # Keep the bound socket so no other socket can take the port before uvicorn listens on it.
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        sockets.append(sock)
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error", lifespan="off")
    server = uvicorn.Server(config)
    _SERVERS.append(server)
    thread = threading.Thread(target=server.run, kwargs={"sockets": sockets or None}, daemon=True)
    thread.start()
    if wait:
        wait_for_port(port, timeout=timeout)
    return port


@dataclass(frozen=True)
class AppRunner:
    run_flask_app: Callable[..., int]
    run_https_flask_app: Callable[[Flask], int]
    run_client_cert_flask_app: Callable[[Flask, trustme.CA], int]
    run_asgi_app: Callable[..., int]
    unused_port: Callable[[], int]
    openapi_url: Callable[..., str]


@pytest.fixture(scope="session")
def app_runner():
    return AppRunner(
        run_flask_app=run_flask_app,
        run_https_flask_app=run_https_flask_app,
        run_client_cert_flask_app=run_client_cert_flask_app,
        run_asgi_app=run_asgi_app,
        unused_port=unused_port,
        openapi_url=openapi_url,
    )
