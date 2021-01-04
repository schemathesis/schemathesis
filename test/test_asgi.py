import pytest
from fastapi import Cookie
from hypothesis import HealthCheck, given, settings

import schemathesis
from schemathesis import Case
from schemathesis.loaders import from_asgi

from .apps._fastapi import create_app


@pytest.fixture()
def schema():
    return from_asgi("/openapi.json", create_app())


@pytest.mark.hypothesis_nested
def test_call(schema, simple_schema):
    strategy = schema["/users"]["GET"].as_strategy()

    @given(case=strategy)
    def test(case):
        response = case.call_asgi()
        assert response.status_code == 200
        assert response.json() == {"success": True}

    test()


@pytest.mark.hypothesis_nested
def test_cookies(fastapi_app):
    @fastapi_app.get("/cookies")
    def cookies(token: str = Cookie(None)):
        return {"token": token}

    schema = schemathesis.from_dict(
        {
            "openapi": "3.0.2",
            "info": {"title": "Test", "description": "Test", "version": "0.1.0"},
            "paths": {
                "/cookies": {
                    "get": {
                        "parameters": [
                            {
                                "name": "token",
                                "in": "cookie",
                                "required": True,
                                "schema": {"type": "string", "enum": ["test"]},
                            }
                        ],
                        "responses": {"200": {"description": "OK"}},
                    }
                }
            },
        },
        app=fastapi_app,
    )

    strategy = schema["/cookies"]["GET"].as_strategy()

    @given(case=strategy)
    @settings(max_examples=3, suppress_health_check=[HealthCheck.filter_too_much], deadline=None)
    def test(case):
        response = case.call_asgi()
        assert response.status_code == 200
        assert response.json() == {"token": "test"}

    test()


def test_not_app_with_asgi(schema):
    case = Case(schema["/users"]["GET"])
    case.operation.app = None
    with pytest.raises(
        RuntimeError,
        match="ASGI application instance is required. "
        "Please, set `app` argument in the schema constructor or pass it to `call_asgi`",
    ):
        case.call_asgi()
