import asyncio
import logging
import threading
from enum import Enum
from functools import wraps
from time import sleep
from typing import Any, Callable, Dict, List, Tuple

import click
import yaml
from aiohttp import web

from schemathesis.cli import CSVOption


async def success(request: web.Request) -> web.Response:
    return web.json_response({"success": True})


async def failure(request: web.Request) -> web.Response:
    raise web.HTTPInternalServerError


async def slow(request: web.Request) -> web.Response:
    await asyncio.sleep(0.25)
    return web.json_response({"slow": True})


async def unsatisfiable(request: web.Request) -> web.Response:
    return web.json_response({"result": "IMPOSSIBLE!"})


class Endpoint(Enum):
    success = ("GET", "/api/success", success)
    failure = ("GET", "/api/failure", failure)
    slow = ("GET", "/api/slow", slow)
    unsatisfiable = ("POST", "/api/unsatisfiable", unsatisfiable)


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

    schema_data = make_schema(endpoints)

    async def schema(request: web.Request) -> web.Response:
        content = yaml.dump(schema_data)
        schema_requests.append(request)
        return web.Response(body=content)

    def wrapper(handler: Callable) -> Callable:
        @wraps(handler)
        async def inner(request: web.Request) -> web.Response:
            incoming_requests.append(request)
            return await handler(request)

        return inner

    app = web.Application()
    app.add_routes(
        [web.get("/swagger.yaml", schema)]
        + [
            web.route(item.value[0], item.value[1], wrapper(item.value[2]))
            for item in Endpoint
            if item.name in endpoints
        ]
    )
    app["incoming_requests"] = incoming_requests
    app["schema_requests"] = schema_requests
    return app


def make_schema(endpoints: Tuple[str, ...]) -> Dict:
    """Generate a Swagger 2.0 schema with the given endpoints.

    Example:
        If `endpoints` is ("success", "failure")
        then the app will contain GET /success and GET /failure
    """
    template: Dict[str, Any] = {
        "swagger": "2.0",
        "info": {"title": "Example API", "description": "An API to test Schemathesis", "version": "1.0.0"},
        "host": "127.0.0.1:8888",
        "basePath": "/api",
        "schemes": ["http"],
        "paths": {},
    }
    for endpoint in endpoints:
        method = Endpoint[endpoint].value[0].lower()
        if endpoint == "unsatisfiable":
            schema = {
                "parameters": [
                    {
                        "name": "id",
                        "in": "body",
                        "required": True,
                        # Impossible to satisfy
                        "schema": {"allOf": [{"type": "integer"}, {"type": "string"}]},
                    }
                ]
            }
        else:
            schema = {"produces": ["application/json"], "responses": {200: {"description": "OK"}}}
        template["paths"][f"/{endpoint}"] = {method: schema}
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
def run_app(port: int, endpoints: List[Endpoint]) -> None:
    if endpoints is not None:
        prepared_endpoints = tuple(endpoint.name for endpoint in endpoints)
    else:
        prepared_endpoints = ("success", "failure")
    app = create_app(prepared_endpoints)
    web.run_app(app, port=port)


if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG)
    run_app()
