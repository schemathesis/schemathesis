from __future__ import annotations

import os
import socket
import subprocess
import sys
import threading
from collections.abc import Callable
from pathlib import Path
from time import monotonic, sleep, time
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import pytest
import requests
import uvicorn

if TYPE_CHECKING:
    from fastapi import FastAPI
    from flask import Flask


COVERAGE_ENV_VARS = ("COVERAGE_PROCESS_START", "COVERAGE_FILE")


def unused_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def run(target: Callable, port: int | None = None, timeout: float = 0.05, **kwargs: Any) -> int:
    """Start a daemon thread running the given server target on a free port."""
    if port is None:
        port = unused_port()
    server_thread = threading.Thread(target=target, kwargs={"port": port, **kwargs})
    server_thread.daemon = True
    server_thread.start()
    sleep(timeout)
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


def run_flask_app(app: Flask, port: int | None = None, timeout: float = 0.05) -> int:
    """Start a thread with the given Flask application."""
    return run(app.run, port=port, timeout=timeout)


def openapi_url(app: Flask, *, path: str = "/openapi.json") -> str:
    """Start `app` on a free port and return the URL where the OpenAPI schema is served."""
    port = run_flask_app(app)
    return f"http://127.0.0.1:{port}{path}"


def run_asgi_app(app: FastAPI, port: int | None = None, timeout: float = 0.05) -> int:
    """Start a daemon thread running uvicorn against the given ASGI application."""
    if port is None:
        port = unused_port()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error", lifespan="off")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = monotonic() + 5.0
    while monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=timeout):
                return port
        except OSError:
            sleep(timeout)
    raise RuntimeError(f"uvicorn did not bind to 127.0.0.1:{port}")


@pytest.fixture(scope="session")
def app_runner():
    return SimpleNamespace(
        run_flask_app=run_flask_app,
        run_asgi_app=run_asgi_app,
        unused_port=unused_port,
        openapi_url=openapi_url,
    )
