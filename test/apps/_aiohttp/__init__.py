from collections import defaultdict
from functools import wraps
from typing import Callable, Tuple

import yaml
from aiohttp import web

from . import handlers

try:
    from ..utils import Endpoint, OpenAPIVersion, make_openapi_schema
except (ImportError, ValueError):
    from utils import Endpoint, OpenAPIVersion, make_openapi_schema


def create_openapi_app(
    endpoints: Tuple[str, ...] = ("success", "failure"), version: OpenAPIVersion = OpenAPIVersion("2.0")
) -> web.Application:
    """Factory for aioHTTP app.

    Each handler except the one for schema saves requests in the list shared in the app instance and could be
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

    async def set_cookies(request: web.Request) -> web.Response:
        response = web.Response()
        response.set_cookie("foo", "bar")
        response.set_cookie("baz", "spam")
        return response

    def wrapper(handler_name: str) -> Callable:

        handler = getattr(handlers, handler_name)

        @wraps(handler)
        async def inner(request: web.Request) -> web.Response:
            await request.read()  # to introspect the payload in tests
            incoming_requests.append(request)
            return await handler(request)

        return inner

    app = web.Application()
    app.add_routes(
        [web.get("/schema.yaml", schema), web.get("/api/cookies", set_cookies)]
        + [web.route(item.value[0], item.value[1], wrapper(item.name)) for item in Endpoint if item.name != "all"]
    )
    app["users"] = {}
    app["incoming_requests"] = incoming_requests
    app["schema_requests"] = schema_requests
    app["config"] = {"should_fail": True, "schema_data": make_openapi_schema(endpoints, version)}
    return app


def reset_app(
    app: web.Application,
    endpoints: Tuple[str, ...] = ("success", "failure"),
    version: OpenAPIVersion = OpenAPIVersion("2.0"),
) -> None:
    """Clean up all internal containers of the application and resets its config."""
    app["users"].clear()
    app["incoming_requests"][:] = []
    app["schema_requests"][:] = []
    app["config"].update({"should_fail": True, "schema_data": make_openapi_schema(endpoints, version)})
