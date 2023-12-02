from __future__ import annotations

from flask import Flask

from . import _server


def run_server(app: Flask, port: int | None = None, timeout: float = 0.05) -> int:
    """Start a thread with the given aiohttp application."""
    return _server.run(app.run, port=port, timeout=timeout)
