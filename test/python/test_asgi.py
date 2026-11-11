import subprocess
import sys
import threading
from contextlib import asynccontextmanager
from typing import Annotated

import anyio
import pytest
import requests
from fastapi import Cookie, FastAPI, Header, Request
from fastapi.responses import PlainTextResponse
from hypothesis import Phase
from pydantic import BaseModel

import schemathesis
from schemathesis.core.errors import get_request_error_message
from schemathesis.python.asgi import ASGIClient, shutdown_lifespans
from test.utils import run_given


async def echo_scope_app(scope, receive, send):
    payload = repr({"server": scope["server"], "headers": scope["headers"]}).encode()
    await send({"type": "http.response.start", "status": 200, "headers": [(b"content-type", b"text/plain")]})
    await send({"type": "http.response.body", "body": payload})


async def streaming_app(scope, receive, send):
    await send({"type": "http.response.start", "status": 200, "headers": [(b"content-type", b"text/plain")]})
    await send({"type": "http.response.body", "body": b"first-", "more_body": True})
    await send({"type": "http.response.body", "body": b"second", "more_body": True})
    await send({"type": "http.response.body", "body": b""})


async def echo_body_app(scope, receive, send):
    body = b""
    while True:
        message = await receive()
        body += message.get("body", b"")
        if not message.get("more_body", False):
            break
    await send({"type": "http.response.start", "status": 200, "headers": [(b"content-type", b"text/plain")]})
    await send({"type": "http.response.body", "body": body})


async def echo_path_app(scope, receive, send):
    payload = f"{scope['path']}|{scope['query_string'].decode()}".encode()
    await send({"type": "http.response.start", "status": 200, "headers": [(b"content-type", b"text/plain")]})
    await send({"type": "http.response.body", "body": payload})


async def echo_header_app(scope, receive, send):
    value = dict(scope["headers"]).get(b"x-token", b"")
    await send({"type": "http.response.start", "status": 200, "headers": [(b"content-type", b"text/plain")]})
    await send({"type": "http.response.body", "body": value})


async def latin1_response_header_app(scope, receive, send):
    disposition = 'attachment; filename="café.pdf"'.encode("latin-1")
    headers = [(b"content-type", b"text/plain"), (b"content-disposition", disposition)]
    await send({"type": "http.response.start", "status": 200, "headers": headers})
    await send({"type": "http.response.body", "body": b""})


async def unknown_status_app(scope, receive, send):
    await send({"type": "http.response.start", "status": 599, "headers": []})
    await send({"type": "http.response.body", "body": b""})


async def set_cookie_app(scope, receive, send):
    headers = [(b"content-type", b"text/plain"), (b"set-cookie", b"a=1; Path=/"), (b"set-cookie", b"b=2; Path=/")]
    await send({"type": "http.response.start", "status": 200, "headers": headers})
    await send({"type": "http.response.body", "body": b""})


async def trailers_app(scope, receive, send):
    start = {
        "type": "http.response.start",
        "status": 200,
        "headers": [(b"content-type", b"text/plain")],
        "trailers": True,
    }
    await send(start)
    await send({"type": "http.response.body", "body": b"payload"})
    await send({"type": "http.response.trailers", "headers": [(b"x-checksum", b"abc")], "more_trailers": False})


def test_streaming_response():
    assert ASGIClient(streaming_app).get("/stream").text == "first-second"


@pytest.mark.parametrize(
    ("body", "expected"),
    [(b"raw-bytes", "raw-bytes"), ("text-body", "text-body"), (None, "")],
    ids=["bytes", "string", "empty"],
)
def test_request_body(body, expected):
    assert ASGIClient(echo_body_app).post("/echo", data=body).text == expected


@pytest.mark.parametrize("chunks", [[b"one-", b"two-", b"three"], ["one-", "two-", "three"]], ids=["bytes", "string"])
def test_generator_request_body(chunks):
    body = (chunk for chunk in chunks)
    assert ASGIClient(echo_body_app).post("/echo", data=body).text == "one-two-three"


def test_unknown_status_code_has_no_reason():
    response = ASGIClient(unknown_status_app).get("/odd")
    assert (response.status_code, response.reason) == (599, "")


def test_multiple_set_cookie_headers():
    assert ASGIClient(set_cookie_app).get("/cookies").cookies.get_dict() == {"a": "1", "b": "2"}


def test_response_trailers_are_ignored():
    assert ASGIClient(trailers_app).get("/trailers").text == "payload"


@pytest.mark.parametrize(
    ("base_url", "expected_host", "expected_port"),
    [("http://testserver", b"testserver", 80), ("http://testserver:8080", b"testserver:8080", 8080)],
    ids=["default-port", "explicit-port"],
)
def test_host_header(base_url, expected_host, expected_port):
    body = ASGIClient(echo_scope_app, base_url=base_url).get("/x").text
    assert f"(b'host', {expected_host!r})" in body
    assert f"'server': ['testserver', {expected_port}]" in body


def test_caller_supplied_host_header_wins():
    body = ASGIClient(echo_scope_app).get("/x", headers={"Host": "example.com"}).text
    assert "(b'host', b'example.com')" in body
    assert "b'testserver'" not in body


def test_header_value_uses_the_wire_encoding(app_runner):
    headers = {"X-Token": "clé"}
    port = app_runner.run_asgi_app(echo_header_app)
    over_http = requests.get(f"http://127.0.0.1:{port}/x", headers=headers).content
    assert ASGIClient(echo_header_app).get("/x", headers=headers).content == over_http == b"cl\xe9"


def test_header_value_outside_the_wire_encoding_is_rejected(app_runner):
    headers = {"X-Token": "日本"}
    port = app_runner.run_asgi_app(echo_header_app)
    with pytest.raises(UnicodeEncodeError):
        requests.get(f"http://127.0.0.1:{port}/x", headers=headers)
    with pytest.raises(UnicodeEncodeError):
        ASGIClient(echo_header_app).get("/x", headers=headers)


def test_response_header_value_uses_the_wire_encoding(app_runner):
    port = app_runner.run_asgi_app(latin1_response_header_app)
    over_http = requests.get(f"http://127.0.0.1:{port}/x").headers["Content-Disposition"]
    in_process = ASGIClient(latin1_response_header_app).get("/x").headers["Content-Disposition"]
    assert in_process == over_http == 'attachment; filename="café.pdf"'


@pytest.mark.parametrize("url", ["/echo?a=1", b"/echo?a=1"], ids=["string", "bytes"])
def test_relative_url_resolved_against_base_url(url):
    assert ASGIClient(echo_path_app).request("GET", url).text == "/echo|a=1"


def test_close_does_not_fail():
    client = ASGIClient(echo_body_app)
    client.get("/echo")
    client.close()


def test_receive_after_response_reports_disconnect():
    seen = []

    async def app(scope, receive, send):
        seen.append((await receive())["type"])
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})
        seen.append((await receive())["type"])

    ASGIClient(app).get("/x")
    assert seen == ["http.request", "http.disconnect"]


def test_application_without_response_is_reported():
    async def app(scope, receive, send):
        return

    with pytest.raises(AssertionError, match="did not return a response"):
        ASGIClient(app).get("/x")


def test_startup_failure_reported_to_every_caller():
    attempts = []

    async def app(scope, receive, send):
        message = await receive()
        if message["type"] == "lifespan.startup":
            attempts.append(1)
            await send({"type": "lifespan.startup.failed", "message": "nope"})
            raise RuntimeError("startup exploded")

    for _ in range(3):
        with pytest.raises(RuntimeError, match="startup exploded"), ASGIClient(app):
            pass
    assert attempts == [1]


def test_shutdown_failure_at_interpreter_exit_is_silent():
    source = """
from contextlib import asynccontextmanager
from fastapi import FastAPI
from schemathesis.core.errors import get_request_error_message
from schemathesis.python.asgi import ASGIClient, shutdown_lifespans

@asynccontextmanager
async def lifespan(_: FastAPI):
    yield
    raise RuntimeError("shutdown exploded")

app = FastAPI(lifespan=lifespan)

@app.get("/ping")
async def ping():
    return {"ok": True}

with ASGIClient(app) as client:
    assert client.get("/ping").status_code == 200
print("done")
"""
    result = subprocess.run([sys.executable, "-c", source], capture_output=True, text=True)
    assert result.returncode == 0
    assert result.stdout.strip() == "done"
    assert "shutdown exploded" not in result.stderr


def test_truncated_body_reports_a_transport_error():
    async def app(scope, receive, send):
        headers = [(b"content-type", b"text/plain"), (b"content-length", b"100")]
        await send({"type": "http.response.start", "status": 200, "headers": headers})
        await send({"type": "http.response.body", "body": b"short"})

    with pytest.raises(requests.exceptions.RequestException):
        ASGIClient(app).get("/truncated")


def test_lifespan_starts_once_under_concurrent_access():
    startups = []
    barrier = threading.Barrier(8)

    async def app(scope, receive, send):
        message = await receive()
        if message["type"] == "lifespan.startup":
            startups.append(1)
            await send({"type": "lifespan.startup.complete"})
            return
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    def worker():
        barrier.wait()
        with ASGIClient(app) as client:
            assert client.get("/x").status_code == 200

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert startups == [1]


def test_lifespan_task_ending_after_startup_shuts_down_cleanly():
    async def app(scope, receive, send):
        message = await receive()
        if message["type"] == "lifespan.startup":
            await send({"type": "lifespan.startup.complete"})
            return
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    with ASGIClient(app) as client:
        assert client.get("/x").status_code == 200
    shutdown_lifespans()


def test_application_without_lifespan_support():
    async def app(scope, receive, send):
        if scope["type"] != "http":
            return
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    with ASGIClient(app) as client:
        assert client.get("/x").text == "ok"
    shutdown_lifespans()


def test_application_refusing_the_lifespan_scope_is_served():
    async def app(scope, receive, send):
        if scope["type"] != "http":
            raise ValueError("Django can only handle ASGI/HTTP connections, not lifespan.")
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    with ASGIClient(app) as client:
        assert client.get("/x").text == "ok"
    shutdown_lifespans()


def test_startup_crash_after_the_first_lifespan_message_is_reported():
    async def app(scope, receive, send):
        if scope["type"] == "lifespan":
            await receive()
            raise RuntimeError("db unavailable")
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"served"})

    with pytest.raises(RuntimeError, match="db unavailable"), ASGIClient(app):
        pass


def test_startup_failure_message_stops_the_client():
    async def app(scope, receive, send):
        if scope["type"] == "lifespan":
            await receive()
            await send({"type": "lifespan.startup.failed", "message": "db unavailable"})
            return
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"served"})

    with pytest.raises(RuntimeError, match="db unavailable"), ASGIClient(app):
        pass


def test_lifespan_crash_after_startup_is_reported():
    async def app(scope, receive, send):
        if scope["type"] == "lifespan":
            await receive()
            await send({"type": "lifespan.startup.complete"})
            raise RuntimeError("background lifespan crash")
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    with ASGIClient(app):
        pass
    with pytest.raises(RuntimeError, match="background lifespan crash"):
        shutdown_lifespans()


def test_failure_after_shutdown_complete_is_reported():
    async def app(scope, receive, send):
        if scope["type"] == "lifespan":
            await receive()
            await send({"type": "lifespan.startup.complete"})
            await receive()
            await send({"type": "lifespan.shutdown.complete"})
            raise RuntimeError("post-complete cleanup failure")
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    with ASGIClient(app):
        pass
    with pytest.raises(RuntimeError, match="post-complete cleanup failure"):
        shutdown_lifespans()


def test_lifespan_ending_on_shutdown_without_reply_is_silent():
    shutdowns = []

    async def app(scope, receive, send):
        if scope["type"] == "lifespan":
            await receive()
            await send({"type": "lifespan.startup.complete"})
            shutdowns.append((await receive())["type"])
            return
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    with ASGIClient(app) as client:
        assert client.get("/x").text == "ok"
    shutdown_lifespans()
    assert shutdowns == ["lifespan.shutdown"]


@pytest.mark.parametrize("base_url", ["http://[::1]:8000", "http://user:pass@testserver"], ids=["ipv6", "userinfo"])
def test_unusual_netloc(base_url):
    assert ASGIClient(echo_path_app, base_url=base_url).get("/x?a=1").text == "/x|a=1"


def test_entered_client_survives_an_external_shutdown():
    async def app(scope, receive, send):
        if scope["type"] == "lifespan":
            while True:
                message = await receive()
                if message["type"] == "lifespan.startup":
                    scope["state"]["db"] = "connected"
                    await send({"type": "lifespan.startup.complete"})
                else:
                    await send({"type": "lifespan.shutdown.complete"})
                    return
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": scope["state"].get("db", "MISSING").encode()})

    with ASGIClient(app) as client:
        assert client.get("/x").text == "connected"
        shutdown_lifespans()
        assert client.get("/x").text == "connected"


def test_every_failing_shutdown_is_reported():
    def make(name):
        async def app(scope, receive, send):
            if scope["type"] == "lifespan":
                await receive()
                await send({"type": "lifespan.startup.complete"})
                await receive()
                raise RuntimeError(f"{name} shutdown failed")
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b""})

        return app

    for app in (make("first"), make("second")):
        with ASGIClient(app):
            pass

    with pytest.raises(BaseExceptionGroup) as info:
        shutdown_lifespans()
    assert sorted(str(error) for error in info.value.exceptions) == [
        "first shutdown failed",
        "second shutdown failed",
    ]


def test_slow_application_times_out():
    async def app(scope, receive, send):
        await anyio.sleep(30)

    with pytest.raises(requests.exceptions.ReadTimeout) as info:
        ASGIClient(app).get("/slow", timeout=0.25)
    assert get_request_error_message(info.value) == "Read timed out after 0.25 seconds"


@pytest.mark.hypothesis_nested
def test_cookies(ctx):
    app = FastAPI()

    @app.get("/cookies")
    def cookies(token: str = Cookie(None)):
        return {"token": token}

    operation = ctx.openapi.load_operation(
        "GET /cookies",
        {
            "parameters": [
                {
                    "name": "token",
                    "in": "cookie",
                    "required": True,
                    "schema": {"type": "string", "enum": ["test"]},
                }
            ],
            "responses": {"200": {"description": "OK"}},
        },
    )

    strategy = operation.as_strategy()

    @run_given(case=strategy, max_examples=3)
    def test(case):
        response = case.call(app=app)
        assert response.status_code == 200
        assert response.json() == {"token": "test"}


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

    @run_given(case=strategy, max_examples=50, phases=[Phase.generate])
    def test(case):
        response = case.call()
        assert response.status_code == 200
        assert response.json() == {"success": True}


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

    @run_given(case=strategy, max_examples=50, phases=[Phase.generate])
    def test(case):
        response = case.call()
        assert response.status_code == 200
        assert response.json() == {"success": True}


def test_base_url(ctx):
    # See GH-1366
    # When base URL has non-empty base path
    operation = ctx.openapi.load_operation(
        "GET /foo",
        {"responses": {"200": {"description": "OK"}}},
        version="3.0.3",
        servers=[{"url": "https://example.org/v1"}],
    )

    # And is used for an ASGI app
    app = FastAPI()

    @app.get("/v1/foo")
    def read_root():
        return {"Hello": "World"}

    strategy = operation.as_strategy()

    @run_given(case=strategy, max_examples=1)
    def test(case):
        response = case.call(app=app)
        # Then the base path should be respected and calls should not lead to 404
        assert response.status_code == 200


def test_schema_and_generated_requests_share_one_host():
    # A `Host`-validating app must not have to allow one host for loading and another for the run.
    app = FastAPI()
    seen = []

    @app.middleware("http")
    async def record_host(request, call_next):
        seen.append(request.headers["host"])
        return await call_next(request)

    @app.get("/ping")
    async def ping():
        return {"ok": True}

    schema = schemathesis.openapi.from_asgi("/openapi.json", app)

    @run_given(case=schema["/ping"]["GET"].as_strategy(), max_examples=1, phases=[Phase.generate])
    def test(case):
        assert case.call().status_code == 200

    assert seen == ["testserver", "testserver"]


def test_head_operation():
    app = FastAPI()

    @app.head("/ping")
    async def ping():
        return PlainTextResponse("pong")

    schema = schemathesis.openapi.from_asgi("/openapi.json", app)

    @run_given(case=schema["/ping"]["HEAD"].as_strategy(), max_examples=1)
    def test(case):
        response = case.call()
        assert response.status_code == 200
        assert response.content == b""


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

    @run_given(case=schema["/state/{name}"]["GET"].as_strategy(), max_examples=2, phases=[Phase.generate])
    def test(case):
        assert case.call().json() == {"db": "connected"}


@pytest.mark.hypothesis_nested
def test_caller_supplied_session_is_used():
    app = FastAPI()

    @app.get("/echo")
    async def echo(x_custom: Annotated[str, Header()] = "MISSING"):
        return {"header": x_custom}

    schema = schemathesis.openapi.from_asgi("/openapi.json", app)
    client = ASGIClient(app)
    client.headers["x-custom"] = "from-session"

    @run_given(case=schema["/echo"]["GET"].as_strategy(), max_examples=1, phases=[Phase.generate])
    def test(case):
        assert case.call(session=client).json() == {"header": "from-session"}


def test_curl_command_names_the_host_the_request_used():
    # A reproduction naming a host the run never used cannot reproduce a `Host`-validating rejection.
    app = FastAPI()

    @app.get("/ping")
    async def ping():
        return {"ok": True}

    schema = schemathesis.openapi.from_asgi("/openapi.json", app)

    @run_given(case=schema["/ping"]["GET"].as_strategy(), max_examples=1, phases=[Phase.generate])
    def test(case):
        response = case.call()
        assert case.as_curl_command() == f"curl -X GET {response.request.url}"
