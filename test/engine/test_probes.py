import threading
from pathlib import Path
from unittest.mock import ANY

import pytest
from requests import Session

from schemathesis.core.transport import USER_AGENT
from schemathesis.engine.context import EngineContext
from schemathesis.engine.phases import probes


@pytest.fixture()
def ctx(openapi_30):
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
def test_detect_null_byte_detected(ctx, openapi3_base_url, kwargs):
    session = Session()
    ctx.schema.config.update(base_url=openapi3_base_url)
    if "auth" in kwargs:
        session.auth = kwargs["auth"]
    ctx.schema.config.update(**kwargs)
    results = probes.run(ctx)
    assert results == [
        probes.ProbeRun(
            probe=probes.NullByteInHeader(),
            outcome=probes.ProbeOutcome.FAILURE,
            request=ANY,
            response=ANY,
            error=None,
        )
    ]


def test_detect_null_byte_with_response(ctx, openapi3_base_url, response_factory):
    ctx.schema.config.update(base_url=openapi3_base_url)
    result = probes.run(ctx)[0]
    result.response = response_factory.requests(content=b'{"success": true}')


def test_detect_null_byte_error(ctx):
    ctx.schema.config.update(base_url="http://127.0.0.1:1")
    results = probes.run(ctx)
    assert results == [
        probes.ProbeRun(
            probe=probes.NullByteInHeader(),
            outcome=probes.ProbeOutcome.FAILURE,
            request=ANY,
            response=None,
            error=ANY,
        )
    ]


def test_detect_null_byte_skipped(ctx):
    results = probes.run(ctx)
    assert results == [
        probes.ProbeRun(
            probe=probes.NullByteInHeader(),
            outcome=probes.ProbeOutcome.SKIP,
            request=None,
            response=None,
            error=None,
        )
    ]


def test_ctrl_c(cli, mocker, openapi3_schema_url, snapshot_cli):
    mocker.patch("schemathesis.engine.phases.probes.send", side_effect=KeyboardInterrupt)
    assert cli.run(openapi3_schema_url) == snapshot_cli
