from pathlib import Path
from unittest.mock import ANY

import pytest
from requests import Session

from schemathesis.core.transport import USER_AGENT
from schemathesis.engine.config import NetworkConfig
from schemathesis.engine.phases import probes


@pytest.fixture
def config_factory():
    def inner(request_proxy=None, request_tls_verify=False, request_cert=None, auth=None, headers=None):
        return NetworkConfig(
            proxy=request_proxy,
            tls_verify=request_tls_verify,
            cert=request_cert,
            auth=auth,
            headers=headers,
        )

    return inner


HERE = Path(__file__).absolute().parent


DEFAULT_HEADERS = {
    "User-Agent": [USER_AGENT],
    "Accept": ["*/*"],
    "Accept-Encoding": ["gzip, deflate"],
    "Connection": ["keep-alive"],
}


@pytest.mark.parametrize(
    ("kwargs", "headers"),
    [
        ({"request_cert": str(HERE.parent / "cli" / "cert.pem")}, {}),
        ({"auth": ("test", "test")}, {"Authorization": ["[Filtered]"]}),
    ],
)
def test_detect_null_byte_detected(openapi_30, config_factory, openapi3_base_url, kwargs, headers):
    config = config_factory(**kwargs)
    openapi_30.base_url = openapi3_base_url
    session = Session()
    if "auth" in kwargs:
        session.auth = kwargs["auth"]
    results = probes.run(openapi_30, session, config)
    assert results == [
        probes.ProbeRun(
            probe=probes.NullByteInHeader(),
            outcome=probes.ProbeOutcome.FAILURE,
            request=ANY,
            response=ANY,
            error=None,
        )
    ]


def test_detect_null_byte_with_response(openapi_30, config_factory, openapi3_base_url, response_factory):
    config = config_factory()
    openapi_30.base_url = openapi3_base_url
    result = probes.run(openapi_30, Session(), config)[0]
    result.response = response_factory.requests(content=b'{"success": true}')


def test_detect_null_byte_error(openapi_30, config_factory):
    config = config_factory()
    openapi_30.base_url = "http://127.0.0.1:1"
    results = probes.run(openapi_30, Session(), config)
    assert results == [
        probes.ProbeRun(
            probe=probes.NullByteInHeader(),
            outcome=probes.ProbeOutcome.ERROR,
            request=ANY,
            response=None,
            error=ANY,
        )
    ]


def test_detect_null_byte_skipped(openapi_30, config_factory):
    config = config_factory()
    results = probes.run(openapi_30, Session(), config)
    assert results == [
        probes.ProbeRun(
            probe=probes.NullByteInHeader(),
            outcome=probes.ProbeOutcome.SKIP,
            request=None,
            response=None,
            error=None,
        )
    ]
