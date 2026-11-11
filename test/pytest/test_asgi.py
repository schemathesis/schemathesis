import gc
import warnings
from contextlib import asynccontextmanager

import pytest
from fastapi import FastAPI

import schemathesis
from test.utils import run_given


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
    assert "curl -X GET http://testserver/fail" in result.stdout.str()


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

    @run_given(case=schema["/health"]["GET"].as_strategy(), max_examples=3)
    def test(case):
        response = case.call()
        assert response.status_code == 200
        assert response.json() == {"status": "OK"}

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
    testdir.runpytest("-v").assert_outcomes(passed=1)
    assert events.read_text().split() == ["startup", "shutdown"]


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
    testdir.runpytest("-v").assert_outcomes(passed=1)
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
    result = testdir.runpytest("-v")
    result.assert_outcomes(passed=1, errors=1)
    assert "shutdown exploded" in result.stdout.str()


def test_lifespan_shutdown_leaves_no_unclosed_streams(testdir):
    testdir.makeini(
        """
[pytest]
filterwarnings = error
"""
    )
    testdir.makepyfile(
        """
import gc

import schemathesis
from fastapi import FastAPI

app = FastAPI()

@app.get("/ping")
def ping():
    return {"ok": "yes"}

schema = schemathesis.openapi.from_asgi("/openapi.json", app)

def test_first():
    pass

def test_second():
    gc.collect()
"""
    )
    # The inner session runs in this process; garbage from earlier tests must not surface in its `gc.collect()`.
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        gc.collect()
    testdir.runpytest("-v").assert_outcomes(passed=2)
