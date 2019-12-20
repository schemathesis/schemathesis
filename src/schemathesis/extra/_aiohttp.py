import asyncio
import threading
from time import sleep
from typing import Optional

from aiohttp import web  # pylint: disable=import-error
from aiohttp.test_utils import unused_port  # pylint: disable=import-error


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


def run_server(app: web.Application, port: Optional[int] = None, timeout: float = 0.05) -> int:
    """Start a thread with the given aiohttp application."""
    if port is None:
        port = unused_port()
    server_thread = threading.Thread(target=_run_server, args=(app, port))
    server_thread.daemon = True
    server_thread.start()
    sleep(timeout)
    return port
