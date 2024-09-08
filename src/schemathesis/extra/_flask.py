from __future__ import annotations

from typing import TYPE_CHECKING

from . import _server

if TYPE_CHECKING:
    from flask import Flask


def run_server(app: Flask, port: int | None = None, timeout: float = 0.05) -> int:
    """Start a thread with the given aiohttp application."""
    return _server.run(app.run, port=port, timeout=timeout)
