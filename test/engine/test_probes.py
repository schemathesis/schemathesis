import threading
from collections.abc import Callable
from pathlib import Path
from unittest.mock import ANY

import pytest
from flask import Flask, request
from requests import Session

from schemathesis.core.transport import USER_AGENT
from schemathesis.engine.context import EngineContext
from schemathesis.engine.run import probes


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


def test_detect_null_byte_error(engine_ctx):
    engine_ctx.schema.config.update(base_url="http://127.0.0.1:1")
    results = probes.run(engine_ctx)
    null_byte_result = next(r for r in results if isinstance(r.probe, probes.NullByteInHeader))
    assert null_byte_result == probes.ProbeRun(
        probe=probes.NullByteInHeader(),
        outcome=probes.ProbeOutcome.FAILURE,
        request=ANY,
        response=None,
        error=ANY,
    )


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


def test_ctrl_c(ctx, cli, mocker, snapshot_cli):
    api = ctx.openapi.apps.success()
    mocker.patch("schemathesis.engine.run.probes.send", side_effect=KeyboardInterrupt)
    assert cli.run(api.schema_url) == snapshot_cli
