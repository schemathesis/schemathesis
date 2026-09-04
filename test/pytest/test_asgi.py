from contextlib import asynccontextmanager
from typing import Annotated

import pytest
from fastapi import Cookie, FastAPI, Header, Request
from fastapi.responses import PlainTextResponse
from hypothesis import HealthCheck, Phase, given, settings
from pydantic import BaseModel

import schemathesis
from schemathesis.python.asgi import ASGIClient


def test_code_sample(testdir):
    testdir.makepyfile(
        """
from fastapi import FastAPI, Depends, HTTPException, Security

app = FastAPI()

@app.get("/fail")
async def fail():
    1 / 0

from hypothesis import settings
import schemathesis
from schemathesis import GenerationMode
from schemathesis.specs.openapi.checks import ignored_auth

schema = schemathesis.openapi.from_asgi("/openapi.json", app)

@schema.parametrize()
@settings(max_examples=3)
def test_api(case):
    case.call_and_validate()
""",
    )
    result = testdir.runpytest("-v", "-s")
    result.assert_outcomes(failed=1)
    assert "ZeroDivisionError: division by zero" in result.stdout.str()
    assert "Reproduce with" in result.stdout.str()
    assert "curl -X GET http://localhost/fail" in result.stdout.str()


@pytest.mark.hypothesis_nested
def test_cookies(ctx):
    app = FastAPI()

    @app.get("/cookies")
    def cookies(token: str = Cookie(None)):
        return {"token": token}

    schema = ctx.openapi.load_schema(
        {
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
        }
    )

    strategy = schema["/cookies"]["GET"].as_strategy()

    @given(case=strategy)
    @settings(max_examples=3, suppress_health_check=[HealthCheck.filter_too_much], deadline=None)
    def test(case):
        response = case.call(app=app)
        assert response.status_code == 200
        assert response.json() == {"token": "test"}

    test()


@pytest.mark.hypothesis_nested
def test_null_byte():
    app = FastAPI()

    class Payload(BaseModel):
        name: str

    @app.post("/data")
    def post_create(payload: Payload):
        payload = payload.model_dump()
        assert "\x00" not in payload["name"]
        return {"success": True}

    schema = schemathesis.openapi.from_asgi("/openapi.json", app=app)
    schema.config.generation.update(allow_x00=False)

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
def test_null_byte_in_headers():
    app = FastAPI()

    @app.post("/data")
    def operation(x_header: Annotated[str, Header()], x_cookie: Annotated[str, Cookie()]):
        assert "\x00" not in x_header
        assert "\x00" not in x_cookie
        return {"success": True}

    schema = schemathesis.openapi.from_asgi("/openapi.json", app=app)
    schema.config.generation.update(allow_x00=False)

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


def test_base_url(ctx):
    # See GH-1366
    # When base URL has non-empty base path
    schema = ctx.openapi.load_schema(
        {"/foo": {"get": {"responses": {"200": {"description": "OK"}}}}},
        version="3.0.3",
        servers=[{"url": "https://example.org/v1"}],
    )

    # And is used for an ASGI app
    app = FastAPI()

    @app.get("/v1/foo")
    def read_root():
        return {"Hello": "World"}

    strategy = schema["/foo"]["GET"].as_strategy()

    @given(case=strategy)
    @settings(max_examples=1, suppress_health_check=[HealthCheck.filter_too_much], deadline=None)
    def test(case):
        response = case.call(app=app)
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

    # The query parameter is what makes Hypothesis generate more than one distinct request.
    @app.get("/health")
    async def find_secret(name: str):
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


@pytest.mark.parametrize(
    "setup",
    [
        """
@asynccontextmanager
async def lifespan(_: FastAPI):
    record("startup")
    yield
    record("shutdown")

app = FastAPI(lifespan=lifespan)
""",
        """
app = FastAPI()

@app.on_event("startup")
async def startup():
    record("startup")

@app.on_event("shutdown")
async def shutdown():
    record("shutdown")
""",
    ],
    ids=["with_lifespan", "with_on_event"],
)
def test_lifespan_runs_once_per_test(testdir, tmp_path, setup):
    events = tmp_path / "events.txt"
    testdir.makepyfile(
        f"""
from contextlib import asynccontextmanager
from fastapi import FastAPI
from hypothesis import settings
import schemathesis

def record(name):
    with open({str(events)!r}, "a") as fd:
        fd.write(name + "\\n")

{setup}

@app.get("/health")
async def health(name: str):
    return {{"status": "OK"}}

schema = schemathesis.openapi.from_asgi("/openapi.json", app)

@schema.parametrize()
@settings(max_examples=3)
def test_api(case):
    case.call_and_validate()
"""
    )
    testdir.runpytest_subprocess("-v").assert_outcomes(passed=1)
    assert events.read_text().split() == ["startup", "shutdown"]


def test_head_operation():
    app = FastAPI()

    @app.head("/ping")
    async def ping():
        return PlainTextResponse("pong")

    schema = schemathesis.openapi.from_asgi("/openapi.json", app)

    @given(case=schema["/ping"]["HEAD"].as_strategy())
    @settings(max_examples=1, deadline=None)
    def test(case):
        response = case.call()
        assert response.status_code == 200
        assert response.content == b""

    test()


def test_application_rejecting_weak_references():
    # Hand-written ASGI apps may use `__slots__`, which makes them unusable as weak-reference targets.
    class Application:
        __slots__ = ()

        async def __call__(self, scope, receive, send):
            if scope["type"] == "lifespan":
                while True:
                    message = await receive()
                    if message["type"] == "lifespan.startup":
                        await send({"type": "lifespan.startup.complete"})
                    elif message["type"] == "lifespan.shutdown":
                        await send({"type": "lifespan.shutdown.complete"})
                        return
            await send({"type": "http.response.start", "status": 200, "headers": [(b"content-type", b"text/plain")]})
            await send({"type": "http.response.body", "body": b"ok"})

    with ASGIClient(Application()) as client:
        assert client.get("http://testserver/health").text == "ok"


def test_lifespan_shutdown_runs_before_fixture_teardown(testdir, tmp_path):
    # A shutdown handler that flushes to a fixture-owned resource must still find it open.
    marker = tmp_path / "marker.txt"
    testdir.makepyfile(
        f"""
from contextlib import asynccontextmanager

import pytest
from fastapi import FastAPI
from hypothesis import Phase, given, settings
import schemathesis

@pytest.fixture
def sink(tmp_path):
    handle = open(tmp_path / "audit.log", "w")
    yield handle
    handle.close()

def test_app(sink):
    @asynccontextmanager
    async def lifespan(_: FastAPI):
        yield
        sink.write("audit\\n")
        with open({str(marker)!r}, "a") as fd:
            fd.write("shutdown finished\\n")

    app = FastAPI(lifespan=lifespan)

    @app.get("/ping/{{name}}")
    async def ping(name: str):
        return {{"ok": name}}

    schema = schemathesis.openapi.from_asgi("/openapi.json", app)

    @given(case=schema["/ping/{{name}}"]["GET"].as_strategy())
    @settings(max_examples=2, deadline=None, phases=[Phase.generate])
    def inner(case):
        assert case.call().status_code == 200

    inner()
"""
    )
    testdir.runpytest_subprocess("-v").assert_outcomes(passed=1)
    assert marker.read_text() == "shutdown finished\n"


def test_lifespan_shutdown_error_fails_the_test(testdir):
    testdir.makepyfile(
        """
from contextlib import asynccontextmanager

from fastapi import FastAPI
from hypothesis import Phase, given, settings
import schemathesis

@asynccontextmanager
async def lifespan(_: FastAPI):
    yield
    raise RuntimeError("shutdown exploded")

app = FastAPI(lifespan=lifespan)

@app.get("/ping/{name}")
async def ping(name: str):
    return {"ok": name}

schema = schemathesis.openapi.from_asgi("/openapi.json", app)

def test_app():
    @given(case=schema["/ping/{name}"]["GET"].as_strategy())
    @settings(max_examples=2, deadline=None, phases=[Phase.generate])
    def inner(case):
        assert case.call().status_code == 200

    inner()
"""
    )
    result = testdir.runpytest_subprocess("-v")
    result.assert_outcomes(passed=1, errors=1)
    assert "shutdown exploded" in result.stdout.str()


@pytest.mark.hypothesis_nested
def test_lifespan_state():
    @asynccontextmanager
    async def lifespan(_: FastAPI):
        yield {"db": "connected"}

    app = FastAPI(lifespan=lifespan)

    @app.get("/state/{name}")
    async def read_state(request: Request, name: str):
        return {"db": request.state.db}

    schema = schemathesis.openapi.from_asgi("/openapi.json", app)

    @given(case=schema["/state/{name}"]["GET"].as_strategy())
    @settings(max_examples=2, deadline=None, phases=[Phase.generate])
    def test(case):
        assert case.call().json() == {"db": "connected"}

    test()


@pytest.mark.hypothesis_nested
def test_caller_supplied_session_is_used():
    app = FastAPI()

    @app.get("/echo")
    async def echo(x_custom: Annotated[str, Header()] = "MISSING"):
        return {"header": x_custom}

    schema = schemathesis.openapi.from_asgi("/openapi.json", app)
    client = ASGIClient(app)
    client.headers["x-custom"] = "from-session"

    @given(case=schema["/echo"]["GET"].as_strategy())
    @settings(max_examples=1, deadline=None, phases=[Phase.generate])
    def test(case):
        assert case.call(session=client).json() == {"header": "from-session"}

    test()
