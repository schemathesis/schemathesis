import pytest
from fastapi import FastAPI
from starlette.applications import Starlette
from starlette.routing import Host, Mount, Route, Router, WebSocketRoute

from schemathesis.python._constants.orchestrator import extract_all
from schemathesis.python._constants.registry import SourceRegistry
from schemathesis.python.application import ApplicationCode, application_code
from test.python._constants.fixtures import starlette_routes
from test.python._constants.helpers import pool_values
from test.utils import HERE


def _extract(app):
    registry = SourceRegistry()
    registry.register(lambda: app)
    return extract_all(registry=registry)


@pytest.mark.parametrize(
    "app",
    [
        Starlette(routes=[Host("api.example.com", app=Router(routes=[Route("/x", starlette_routes.endpoint)]))]),
        Starlette(routes=[Mount("/sub", routes=[Route("/x", starlette_routes.endpoint)])]),
        Starlette(routes=[WebSocketRoute("/ws", starlette_routes.websocket_endpoint)]),
    ],
    ids=["host", "mount", "websocket"],
)
def test_starlette_nested_routes_reach_handler_constants(app):
    assert starlette_routes.TOKEN in pool_values(_extract(app), "string")


def _fastapi_app():
    app = FastAPI()
    app.add_api_route("/x", starlette_routes.endpoint)
    return app


@pytest.mark.parametrize(
    "app",
    [
        Starlette(routes=[Host("api.example.com", app=Router(routes=[Route("/x", starlette_routes.endpoint)]))]),
        Starlette(routes=[Mount("/sub", routes=[Route("/x", starlette_routes.endpoint)])]),
        Starlette(routes=[Mount("/sub", app=Starlette(routes=[Route("/x", starlette_routes.endpoint)]))]),
        _fastapi_app(),
    ],
    ids=["host", "mount", "mounted-app", "fastapi"],
)
def test_starlette_application_code_is_located_through_nested_routes(app):
    assert application_code(app) == ApplicationCode(
        framework="starlette",
        handlers=(starlette_routes.endpoint,),
        modules=(starlette_routes.__name__,),
        roots=(HERE,),
    )
