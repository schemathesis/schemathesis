import asyncio
import logging
import threading
from enum import Enum
from functools import wraps
from time import sleep
from typing import Dict, Tuple

import click
import yaml
from aiohttp import web

from schemathesis.cli import CSVOption


async def success(request):
    return web.json_response({"success": True})


async def failure(request):
    raise web.HTTPInternalServerError


async def slow(request):
    await asyncio.sleep(0.25)
    return web.json_response({"slow": True})


class Endpoint(Enum):
    success = ("/api/success", success)
    failure = ("/api/failure", failure)
    slow = ("/api/slow", slow)


def create_app(endpoints=("success", "failure")) -> web.Application:
    """Factory for aioHTTP app.

    Each endpoint except the one for schema saves requests in the list shared in the app instance and could be
    used to verify generated requests.

    >>> def test_something(app, server):
    >>>     # make some request to the app here
    >>>     assert app["incoming_requests"][0].method == "GET"
    """
    incoming_requests = []
    schema_requests = []

    schema_data = make_schema(endpoints)

    async def schema(request):
        content = yaml.dump(schema_data)
        schema_requests.append(request)
        return web.Response(body=content)

    def wrapper(handler):
        @wraps(handler)
        async def inner(request):
            incoming_requests.append(request)
            return await handler(request)

        return inner

    app = web.Application()
    app.add_routes(
        [web.get("/swagger.yaml", schema)]
        + [web.get(item.value[0], wrapper(item.value[1])) for item in Endpoint if item.name in endpoints]
    )
    app["incoming_requests"] = incoming_requests
    app["schema_requests"] = schema_requests
    return app


def make_schema(endpoints: Tuple[str]) -> Dict:
    """Generate a Swagger 2.0 schema with the given endpoints.

    Example:
        If `endpoints` is ("success", "failure")
        then the app will contain GET /success and GET /failure
    """
    template = {
        "swagger": "2.0",
        "info": {"title": "Example API", "description": "An API to test Schemathesis", "version": "1.0.0"},
        "host": "127.0.0.1:8888",
        "basePath": "/api",
        "schemes": ["http"],
        "paths": {},
    }
    for endpoint in endpoints:
        template["paths"][f"/{endpoint}"] = {
            "get": {"summary": "Endpoint", "produces": ["application/json"], "responses": {200: {"description": "OK"}}}
        }
    return template


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


@click.command()
@click.argument("port", type=int)
@click.option("--endpoints", type=CSVOption(Endpoint))
def run_app(port, endpoints):
    if endpoints is not None:
        endpoints = tuple(endpoint.name for endpoint in endpoints)
    else:
        endpoints = ("success", "failure")
    app = create_app(endpoints)
    web.run_app(app, port=port)


if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG)
    run_app()
