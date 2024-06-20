import warnings
from functools import wraps
from typing import Callable, Tuple

import yaml
from aiohttp import web
from aiohttp.web_exceptions import NotAppKeyWarning

from ..schema import OpenAPIVersion, Operation, make_openapi_schema
from . import handlers


def create_app(
    operations: Tuple[str, ...] = ("success", "failure"), version: OpenAPIVersion = OpenAPIVersion("2.0")
) -> web.Application:
    """Factory for aioHTTP app.

    Each handler except the one for schema saves requests in the list shared in the app instance and could be
    used to verify generated requests.

    >>> def test_something(app, server):
    >>>     # make some request to the app here
    >>>     assert app["incoming_requests"][0].method == "GET"
    """
    warnings.simplefilter("ignore", NotAppKeyWarning)
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
            response = await handler(request)
            if app["config"]["chunked"]:
                response.headers["Transfer-Encoding"] = "chunked"
            return response

        return inner

    app = web.Application()
    app.add_routes(
        [
            web.get("/schema.yaml", schema),
            web.get("/api/cookies", set_cookies),
            web.get("/api/binary", handlers.binary),
            web.get("/api/long", handlers.long),
        ]
        + [web.route(item.value[0], item.value[1], wrapper(item.name)) for item in Operation if item.name != "all"]
    )

    async def answer(request: web.Request) -> web.Response:
        return web.json_response(42)

    app.add_routes([web.get("/answer.json", answer)])
    app["users"] = {}
    app["incoming_requests"] = incoming_requests
    app["schema_requests"] = schema_requests
    app["config"] = {
        "should_fail": True,
        "schema_data": make_openapi_schema(operations, version),
        "prefix_with_bom": False,
        "chunked": False,
    }
    return app


def reset_app(
    app: web.Application,
    operations: Tuple[str, ...] = ("success", "failure"),
    version: OpenAPIVersion = OpenAPIVersion("2.0"),
) -> None:
    """Clean up all internal containers of the application and resets its config."""
    app["users"].clear()
    app["incoming_requests"][:] = []
    app["schema_requests"][:] = []
    app["config"].update(
        {
            "should_fail": True,
            "schema_data": make_openapi_schema(operations, version),
            "prefix_with_bom": False,
            "chunked": False,
        }
    )
