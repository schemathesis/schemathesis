import pytest
from starlette.applications import Starlette
from starlette.routing import Host, Mount, Route, Router, WebSocketRoute

from schemathesis.python._constants.adapters import default_adapters
from schemathesis.python._constants.orchestrator import extract_all
from schemathesis.python._constants.registry import SourceRegistry
from test.python._constants.fixtures import starlette_routes
from test.python._constants.helpers import pool_values


def _extract(app):
    registry = SourceRegistry()
    registry.register(lambda: app)
    return extract_all(registry=registry, adapters=default_adapters())


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
