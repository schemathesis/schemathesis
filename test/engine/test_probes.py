import _thread
import socket
import threading
from collections.abc import Callable
from pathlib import Path
from unittest.mock import ANY

import pytest
from flask import Flask, request
from requests import Session

import schemathesis
from schemathesis.core.transport import USER_AGENT
from schemathesis.engine import events
from schemathesis.engine.context import EngineContext
from schemathesis.engine.run import PhaseName, probes
from test.apps.catalog.grpc.descriptors import users_descriptor_set
from test.utils import EventStream, refusing_url, run_given


def _make_strict_server(app_runner, reject: Callable[[], object]) -> str:
    app = Flask(__name__)
    app.before_request(reject)

    @app.route("/", defaults={"_path": ""})
    @app.route("/<path:_path>")
    def _ok(_path: str):
        return "ok"

    port = app_runner.run_flask_app(app)
    return f"http://127.0.0.1:{port}/"


@pytest.fixture
def null_byte_strict_url(app_runner):
    # NullByteInHeader probe expects 400 when the server rejects the byte; Flask/Werkzeug accept it by default.
    def reject():
        for value in request.headers.values():
            if "\x00" in value:
                return ("rejected", 400)
        return None

    return _make_strict_server(app_runner, reject)


def _unsafe_path() -> bool:
    return "\\" in request.path or any(ord(c) < 0x20 for c in request.path)


@pytest.fixture
def path_decoder_strict_url(app_runner):
    # Tomcat-style strict URL decoder: 400 with empty body when path carries backslash/control chars.
    def reject():
        if _unsafe_path():
            return ("", 400)
        return None

    return _make_strict_server(app_runner, reject)


_TOMCAT_400_HTML = (
    '<!doctype html><html lang="en"><head>'
    "<title>HTTP Status 400 – Bad Request</title>"
    "</head><body><h1>HTTP Status 400 – Bad Request</h1></body></html>"
)


@pytest.fixture
def path_decoder_strict_tomcat_html_url(app_runner):
    # Tomcat ships a default HTML error page; the strict URL decoder still rejects before routing,
    # but the body is non-empty.
    def reject():
        if _unsafe_path():
            return (_TOMCAT_400_HTML, 400, {"Content-Type": "text/html;charset=utf-8"})
        return None

    return _make_strict_server(app_runner, reject)


@pytest.fixture
def engine_ctx(openapi_30):
    return EngineContext(schema=openapi_30, stop_event=threading.Event())


HERE = Path(__file__).absolute().parent


DEFAULT_HEADERS = {
    "User-Agent": [USER_AGENT],
    "Accept": ["*/*"],
    "Accept-Encoding": ["gzip, deflate"],
    "Connection": ["keep-alive"],
}


@pytest.mark.parametrize(
    "kwargs",
    [
        {"request_cert": str(HERE.parent / "cli" / "cert.pem")},
        {"basic_auth": ("test", "test")},
    ],
)
def test_detect_null_byte_detected(engine_ctx, null_byte_strict_url, kwargs):
    session = Session()
    engine_ctx.schema.config.update(base_url=null_byte_strict_url)
    if "auth" in kwargs:
        session.auth = kwargs["auth"]
    engine_ctx.schema.config.update(**kwargs)
    results = probes.run(engine_ctx)
    null_byte_result = next(r for r in results if isinstance(r.probe, probes.NullByteInHeader))
    assert null_byte_result == probes.ProbeRun(
        probe=probes.NullByteInHeader(),
        outcome=probes.ProbeOutcome.FAILURE,
        request=ANY,
        response=ANY,
        error=None,
    )


def test_detect_null_byte_with_response(engine_ctx, null_byte_strict_url, response_factory):
    engine_ctx.schema.config.update(base_url=null_byte_strict_url)
    result = probes.run(engine_ctx)[0]
    result.response = response_factory.requests(content=b'{"success": true}')


@pytest.fixture(params=["refused", "connect-timeout"])
def unreachable_url(request):
    if request.param == "refused":
        with refusing_url() as url:
            yield f"{url}/"
        return
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    port = server.getsockname()[1]
    # The only accept-queue slot is taken and never accepted, so the kernel drops further connection attempts.
    server.listen(0)
    queued = socket.create_connection(("127.0.0.1", port))
    yield f"http://127.0.0.1:{port}/"
    queued.close()
    server.close()


# A probe that never reached a server says nothing about what the API accepts.
@pytest.mark.hypothesis_nested
def test_unreachable_api_does_not_narrow_generation(ctx, unreachable_url):
    schema = ctx.openapi.load_schema(
        {
            "/items/{name}": {
                "get": {
                    "parameters": [
                        {"name": "name", "in": "path", "required": True, "schema": {"type": "string", "enum": ["a\\b"]}}
                    ],
                    "responses": {"200": {"description": "OK"}},
                }
            }
        }
    )
    schema.config.update(base_url=unreachable_url)
    stream = EventStream(schema, phases=[PhaseName.EXAMPLES], request_timeout=0.3).execute()
    probing = stream.find(events.PhaseFinished, phase=lambda phase: phase.name == PhaseName.PROBING)
    assert [(type(run.probe), run.outcome) for run in probing.payload.ok().probes] == [
        (probes.NullByteInHeader, probes.ProbeOutcome.SKIP),
        (probes.UnsafePathDecoder, probes.ProbeOutcome.SKIP),
    ]
    operation = schema["/items/{name}"]["GET"]

    @run_given(case=operation.as_strategy(), max_examples=1)
    def inner(case):
        assert case.path_parameters == {"name": "a%5Cb"}


# A server that accepts the connection and then drops it has rejected the probe.
def test_reset_after_connecting_is_a_failure(engine_ctx, app_runner):
    engine_ctx.schema.config.update(base_url=f"http://127.0.0.1:{app_runner.run_resetting_server()}/")
    assert [(type(run.probe), run.outcome) for run in probes.run(engine_ctx)] == [
        (probes.NullByteInHeader, probes.ProbeOutcome.FAILURE),
        (probes.UnsafePathDecoder, probes.ProbeOutcome.FAILURE),
    ]


def test_detect_null_byte_skipped(engine_ctx):
    results = probes.run(engine_ctx)
    null_byte_result = next(r for r in results if isinstance(r.probe, probes.NullByteInHeader))
    assert null_byte_result == probes.ProbeRun(
        probe=probes.NullByteInHeader(),
        outcome=probes.ProbeOutcome.SKIP,
        request=None,
        response=None,
        error=None,
    )


def test_detect_unsafe_path_decoder_failure(engine_ctx, path_decoder_strict_url):
    engine_ctx.schema.config.update(base_url=path_decoder_strict_url)
    results = probes.run(engine_ctx)
    path_result = next(r for r in results if isinstance(r.probe, probes.UnsafePathDecoder))
    assert path_result == probes.ProbeRun(
        probe=probes.UnsafePathDecoder(),
        outcome=probes.ProbeOutcome.FAILURE,
        request=ANY,
        response=ANY,
        error=None,
    )


def test_detect_unsafe_path_decoder_failure_tomcat_html(engine_ctx, path_decoder_strict_tomcat_html_url):
    engine_ctx.schema.config.update(base_url=path_decoder_strict_tomcat_html_url)
    results = probes.run(engine_ctx)
    path_result = next(r for r in results if isinstance(r.probe, probes.UnsafePathDecoder))
    assert path_result == probes.ProbeRun(
        probe=probes.UnsafePathDecoder(),
        outcome=probes.ProbeOutcome.FAILURE,
        request=ANY,
        response=ANY,
        error=None,
    )


def test_detect_unsafe_path_decoder_success(engine_ctx, null_byte_strict_url):
    # Same fixture as the null-byte test: tolerates `\` and control chars in path; only headers strict.
    engine_ctx.schema.config.update(base_url=null_byte_strict_url)
    results = probes.run(engine_ctx)
    path_result = next(r for r in results if isinstance(r.probe, probes.UnsafePathDecoder))
    assert path_result.outcome == probes.ProbeOutcome.SUCCESS


# Ctrl-C lands while a probe waits for the base URL to respond.
def test_ctrl_c(ctx, cli, snapshot_cli):
    app, _ = ctx.openapi.make_flask_app({"/api/success": {"get": {"responses": {"200": {"description": "OK"}}}}})

    @app.route("/")
    def root():
        _thread.interrupt_main()
        return ""

    assert cli.run_openapi_app(app) == snapshot_cli


def test_grpc_schemas_send_no_http_probes(tmp_path):
    path = tmp_path / "api.binpb"
    path.write_bytes(users_descriptor_set())
    schema = schemathesis.grpc.from_path(path)
    assert probes.run(EngineContext(schema=schema, stop_event=threading.Event())) == []
