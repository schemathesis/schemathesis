from contextlib import asynccontextmanager
from typing import Annotated

import pytest
from fastapi import Cookie, FastAPI, Header
from hypothesis import HealthCheck, Phase, given, settings
from pydantic import BaseModel

import schemathesis
from schemathesis.generation import GenerationConfig


@pytest.mark.hypothesis_nested
def test_cookies(fastapi_app):
    @fastapi_app.get("/cookies")
    def cookies(token: str = Cookie(None)):
        return {"token": token}

    schema = schemathesis.openapi.from_dict(
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
    ).configure(app=fastapi_app)

    strategy = schema["/cookies"]["GET"].as_strategy()

    @given(case=strategy)
    @settings(max_examples=3, suppress_health_check=[HealthCheck.filter_too_much], deadline=None)
    def test(case):
        response = case.call()
        assert response.status_code == 200
        assert response.json() == {"token": "test"}

    test()


@pytest.mark.hypothesis_nested
def test_null_byte(fastapi_app):
    class Payload(BaseModel):
        name: str

    @fastapi_app.post("/data")
    def post_create(payload: Payload):
        payload = payload.model_dump()
        assert "\x00" not in payload["name"]
        return {"success": True}

    schema = schemathesis.openapi.from_asgi("/openapi.json", app=fastapi_app).configure(
        generation=GenerationConfig(allow_x00=False)
    )

    strategy = schema["/data"]["POST"].as_strategy()

    @given(case=strategy)
    @settings(
        max_examples=50, suppress_health_check=[HealthCheck.filter_too_much], deadline=None, phases=[Phase.generate]
    )
    def test(case):
        response = case.call()
        assert response.status_code == 200
        assert response.json() == {"success": True}

    test()


@pytest.mark.hypothesis_nested
def test_null_byte_in_headers(fastapi_app):
    @fastapi_app.post("/data")
    def operation(x_header: Annotated[str, Header()], x_cookie: Annotated[str, Cookie()]):
        assert "\x00" not in x_header
        assert "\x00" not in x_cookie
        return {"success": True}

    schema = schemathesis.openapi.from_asgi("/openapi.json", app=fastapi_app).configure(
        generation=GenerationConfig(allow_x00=False)
    )

    strategy = schema["/data"]["POST"].as_strategy()

    @given(case=strategy)
    @settings(
        max_examples=50, suppress_health_check=[HealthCheck.filter_too_much], deadline=None, phases=[Phase.generate]
    )
    def test(case):
        response = case.call()
        assert response.status_code == 200
        assert response.json() == {"success": True}

    test()


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

    schema = schemathesis.openapi.from_dict(raw_schema).configure(app=app)
    strategy = schema["/foo"]["GET"].as_strategy()

    @given(case=strategy)
    @settings(max_examples=1, suppress_health_check=[HealthCheck.filter_too_much], deadline=None)
    def test(case):
        response = case.call()
        # Then the base path should be respected and calls should not lead to 404
        assert response.status_code == 200

    test()


def with_lifespan(data: dict):
    @asynccontextmanager
    async def lifespan(_: FastAPI):
        data.setdefault("startup", 0)
        data["startup"] += 1
        yield
        data.setdefault("shutdown", 0)
        data["shutdown"] += 1

    return FastAPI(lifespan=lifespan)


def with_on_event(data: dict):
    app = FastAPI()

    @app.on_event("startup")
    async def startup():
        data.setdefault("startup", 0)
        data["startup"] += 1

    @app.on_event("shutdown")
    async def shutdown():
        data.setdefault("shutdown", 0)
        data["shutdown"] += 1

    return app


@pytest.mark.parametrize("setup", [with_lifespan, with_on_event])
@pytest.mark.filterwarnings("ignore::DeprecationWarning")
def test_events(setup):
    data = {}
    app = setup(data)

    @app.get("/health")
    async def find_secret():
        return {"status": "OK"}

    schema = schemathesis.openapi.from_asgi("/openapi.json", app)

    @given(case=schema["/health"]["GET"].as_strategy())
    @settings(max_examples=3, deadline=None)
    def test(case):
        response = case.call()
        assert response.status_code == 200
        assert response.json() == {"status": "OK"}

    test()

    assert data["startup"] == 1
    assert data["shutdown"] == 1
