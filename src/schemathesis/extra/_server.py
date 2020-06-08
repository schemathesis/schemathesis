import threading
from time import sleep
from typing import Any, Callable, Optional

from aiohttp.test_utils import unused_port


def run(target: Callable, port: Optional[int] = None, timeout: float = 0.05, **kwargs: Any) -> int:
    """Start a thread with the given aiohttp application."""
    if port is None:
        port = unused_port()
    server_thread = threading.Thread(target=target, kwargs={"port": port, **kwargs})
    server_thread.daemon = True
    server_thread.start()
    sleep(timeout)
    return port
