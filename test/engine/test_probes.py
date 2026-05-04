import threading
from pathlib import Path
from unittest.mock import ANY

import pytest
from flask import Flask, request
from requests import Session

from schemathesis.core.transport import USER_AGENT
from schemathesis.engine.context import EngineContext
from schemathesis.engine.run import probes


@pytest.fixture
def null_byte_strict_url(app_runner):
    # NullByteInHeader probe expects 400 when the server rejects the byte; Flask/Werkzeug accept it by default.
    app = Flask(__name__)

    @app.before_request
    def _reject_null_bytes():
        for value in request.headers.values():
            if "\x00" in value:
                return ("rejected", 400)
        return None

    @app.route("/", defaults={"_path": ""})
    @app.route("/<path:_path>")
    def _ok(_path: str):
        return "ok"

    port = app_runner.run_flask_app(app)
    return f"http://127.0.0.1:{port}/"


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
    assert results == [
        probes.ProbeRun(
            probe=probes.NullByteInHeader(),
            outcome=probes.ProbeOutcome.FAILURE,
            request=ANY,
            response=ANY,
            error=None,
        )
    ]


def test_detect_null_byte_with_response(engine_ctx, null_byte_strict_url, response_factory):
    engine_ctx.schema.config.update(base_url=null_byte_strict_url)
    result = probes.run(engine_ctx)[0]
    result.response = response_factory.requests(content=b'{"success": true}')


def test_detect_null_byte_error(engine_ctx):
    engine_ctx.schema.config.update(base_url="http://127.0.0.1:1")
    results = probes.run(engine_ctx)
    assert results == [
        probes.ProbeRun(
            probe=probes.NullByteInHeader(),
            outcome=probes.ProbeOutcome.FAILURE,
            request=ANY,
            response=None,
            error=ANY,
        )
    ]


def test_detect_null_byte_skipped(engine_ctx):
    results = probes.run(engine_ctx)
    assert results == [
        probes.ProbeRun(
            probe=probes.NullByteInHeader(),
            outcome=probes.ProbeOutcome.SKIP,
            request=None,
            response=None,
            error=None,
        )
    ]


def test_ctrl_c(ctx, cli, mocker, snapshot_cli):
    api = ctx.openapi.apps.success()
    mocker.patch("schemathesis.engine.run.probes.send", side_effect=KeyboardInterrupt)
    assert cli.run(api.schema_url) == snapshot_cli
