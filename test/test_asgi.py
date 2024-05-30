import sys
from contextlib import asynccontextmanager

import pytest
from fastapi import Cookie, FastAPI, Header
from hypothesis import HealthCheck, Phase, given, settings
from pydantic import BaseModel

import schemathesis
from schemathesis.generation import GenerationConfig
from schemathesis.models import Case
from schemathesis.specs.openapi.loaders import from_asgi


@pytest.fixture()
def schema(fastapi_app):
    return from_asgi("/openapi.json", fastapi_app, force_schema_version="30")


@pytest.mark.parametrize("method", ("call", "call_asgi"))
@pytest.mark.hypothesis_nested
def test_cookies(fastapi_app, method):
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
        response = getattr(case, method)()
        assert response.status_code == 200
        assert response.json() == {"token": "test"}

    test()


@pytest.mark.hypothesis_nested
def test_null_byte(fastapi_app):
    schemathesis.experimental.OPEN_API_3_1.enable()

    class Payload(BaseModel):
        name: str

    @fastapi_app.post("/data")
    def post_create(payload: Payload):
        payload = payload.model_dump()
        assert "\x00" not in payload["name"]
        return {"success": True}

    schema = schemathesis.from_asgi(
        "/openapi.json", app=fastapi_app, generation_config=GenerationConfig(allow_x00=False)
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


@pytest.mark.skipif(sys.version_info < (3, 9), reason="typing.Annotated is not available in Python 3.8")
@pytest.mark.hypothesis_nested
def test_null_byte_in_headers(fastapi_app):
    from typing import Annotated

    schemathesis.experimental.OPEN_API_3_1.enable()

    @fastapi_app.post("/data")
    def operation(x_header: Annotated[str, Header()], x_cookie: Annotated[str, Cookie()]):
        assert "\x00" not in x_header
        assert "\x00" not in x_cookie
        return {"success": True}

    schema = schemathesis.from_asgi(
        "/openapi.json", app=fastapi_app, generation_config=GenerationConfig(allow_x00=False)
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


def test_not_app_with_asgi(schema):
    case = Case(schema["/users"]["GET"], generation_time=0.0)
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

    schema = schemathesis.from_dict(raw_schema, app=app)
    strategy = schema["/foo"]["GET"].as_strategy()

    @given(case=strategy)
    @settings(max_examples=1, suppress_health_check=[HealthCheck.filter_too_much], deadline=None)
    def test(case):
        response = case.call()
        # Then the base path should be respected and calls should not lead to 404
        assert response.status_code == 200

    test()


class FastAPIExtended(FastAPI):
    pass


@pytest.mark.parametrize("app_factory", (FastAPI, FastAPIExtended))
@pytest.mark.parametrize("with_existing_fixup", (True, False))
def test_automatic_fixup(empty_open_api_3_schema, with_existing_fixup, app_factory):
    if with_existing_fixup:
        # Install everything
        schemathesis.fixups.install()
    else:
        assert not schemathesis.fixups.is_installed("fast_api")
    # When it is possible to detect Fast API
    empty_open_api_3_schema["paths"] = {
        "/foo": {
            "get": {
                "parameters": [
                    {
                        "name": "data",
                        "in": "body",
                        "required": True,
                        "schema": {"type": "integer", "exclusiveMaximum": 5},
                    }
                ],
                "responses": {"200": {"description": "OK"}},
            }
        }
    }

    app = app_factory()

    schema = schemathesis.from_dict(empty_open_api_3_schema, app=app)
    # Then its respective fixup is loaded automatically
    assert schema.raw_schema["paths"]["/foo"]["get"]["parameters"][0]["schema"] == {
        "type": "integer",
        "exclusiveMaximum": True,
        "maximum": 5,
    }


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


@pytest.mark.parametrize("setup", (with_lifespan, with_on_event))
@pytest.mark.filterwarnings("ignore::DeprecationWarning")
def test_events(setup):
    data = {}
    app = setup(data)

    @app.get("/health")
    async def find_secret():
        return {"status": "OK"}

    schema = schemathesis.from_asgi("/openapi.json", app, force_schema_version="30")

    @given(case=schema["/health"]["GET"].as_strategy())
    @settings(max_examples=3, deadline=None)
    def test(case):
        response = case.call()
        assert response.status_code == 200
        assert response.json() == {"status": "OK"}

    test()

    assert data["startup"] == 1
    assert data["shutdown"] == 1
