import asyncio
import threading
from functools import wraps
from time import sleep
from typing import Callable, Tuple

import yaml
from aiohttp import web

try:
    from . import handlers
    from ..utils import make_schema, Endpoint
except (ImportError, ValueError):
    from utils import make_schema, Endpoint


def create_app(endpoints: Tuple[str, ...] = ("success", "failure")) -> web.Application:
    """Factory for aioHTTP app.

    Each endpoint except the one for schema saves requests in the list shared in the app instance and could be
    used to verify generated requests.

    >>> def test_something(app, server):
    >>>     # make some request to the app here
    >>>     assert app["incoming_requests"][0].method == "GET"
    """
    incoming_requests = []
    schema_requests = []

    async def schema(request: web.Request) -> web.Response:
        schema_data = request.app["config"]["schema_data"]
        content = yaml.dump(schema_data)
        schema_requests.append(request)
        return web.Response(body=content)

    def wrapper(handler_name: str) -> Callable:

        handler = getattr(handlers, handler_name)

        @wraps(handler)
        async def inner(request: web.Request) -> web.Response:
            if "Content-Type" in request.headers and not request.headers["Content-Type"].startswith("multipart/"):
                await request.read()
            incoming_requests.append(request)
            return await handler(request)

        return inner

    app = web.Application()
    app.add_routes(
        [web.get("/swagger.yaml", schema)]
        + [web.route(item.value[0], item.value[1], wrapper(item.name)) for item in Endpoint]
    )
    app["incoming_requests"] = incoming_requests
    app["schema_requests"] = schema_requests
    app["config"] = {"should_fail": True, "schema_data": make_schema(endpoints)}
    return app


def reset_app(app: web.Application, endpoints: Tuple[str, ...] = ("success", "failure")) -> None:
    """Clean up all internal containers of the application and resets its config."""
    app["incoming_requests"][:] = []
    app["schema_requests"][:] = []
    app["config"].update({"should_fail": True, "schema_data": make_schema(endpoints)})


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


def run_server(app: web.Application, port: int, timeout: float = 0.05) -> None:
    """Start a thread with the given aiohttp application."""
    server_thread = threading.Thread(target=_run_server, args=(app, port))
    server_thread.daemon = True
    server_thread.start()
    sleep(timeout)
