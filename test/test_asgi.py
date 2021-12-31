import pytest
from fastapi import Cookie, FastAPI
from hypothesis import HealthCheck, given, settings

import schemathesis
from schemathesis import Case
from schemathesis.specs.openapi.loaders import from_asgi


@pytest.fixture()
def schema(fastapi_app):
    return from_asgi("/openapi.json", fastapi_app)


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


def test_base_url():
    # See GH-1366
    # When base URL has non-empty base path
    raw_schema = {
        "openapi": "3.0.3",
        "info": {"version": "0.0.1", "title": "foo"},
        "servers": [{"url": "https://example.org/v1"}],
        "paths": {"/foo": {"get": {"responses": {"200": {"description": "OK"}}}}},
    }

    # And is used for an ASGI app
    app = FastAPI()

    @app.get("/v1/foo")
    def read_root():
        return {"Hello": "World"}

    schema = schemathesis.from_dict(raw_schema)
    strategy = schema["/foo"]["GET"].as_strategy()

    @given(case=strategy)
    @settings(max_examples=1, suppress_health_check=[HealthCheck.filter_too_much], deadline=None)
    def test(case):
        response = case.call_asgi(app)
        # Then the base path should be respected and calls should not lead to 404
        assert response.status_code == 200

    test()
