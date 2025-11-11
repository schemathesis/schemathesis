from __future__ import annotations

import asyncio
import os
import socket
import subprocess
import sys
import threading
from collections.abc import Callable
from pathlib import Path
from time import sleep, time
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import pytest
import requests
from aiohttp import web

if TYPE_CHECKING:
    from flask import Flask


def unused_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def run(target: Callable, port: int | None = None, timeout: float = 0.05, **kwargs: Any) -> int:
    """Start a thread with the given aiohttp application."""
    if port is None:
        port = unused_port()
    server_thread = threading.Thread(target=target, kwargs={"port": port, **kwargs})
    server_thread.daemon = True
    server_thread.start()
    sleep(timeout)
    return port


def _run_server(app: web.Application, port: int) -> None:
    """Run the given app on the given port.

    Intended to be called as a target for a separate thread.
    NOTE. `aiohttp.web.run_app` works only in the main thread and can't be used here (or maybe can we some tuning)
    """
    # Set a loop for a new thread (there is no by default for non-main threads)
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    runner = web.AppRunner(app)
    loop.run_until_complete(runner.setup())
    site = web.TCPSite(runner, "127.0.0.1", port)
    loop.run_until_complete(site.start())
    loop.run_forever()


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

        process_env = os.environ.copy()
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


def run_aiohttp_app(app: web.Application, port: int | None = None, timeout: float = 0.05) -> int:
    """Start a thread with the given aiohttp application."""
    return run(_run_server, app=app, port=port, timeout=timeout)


def run_flask_app(app: Flask, port: int | None = None, timeout: float = 0.05) -> int:
    """Start a thread with the given aiohttp application."""
    return run(app.run, port=port, timeout=timeout)


@pytest.fixture(scope="session")
def app_runner():
    return SimpleNamespace(run_flask_app=run_flask_app, run_aiohttp_app=run_aiohttp_app)
