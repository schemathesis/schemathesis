import platform
from pathlib import Path
from unittest.mock import ANY

import pytest

from schemathesis.constants import USER_AGENT
from schemathesis.runner import probes
from schemathesis.runner.impl.core import canonicalize_error_message
from schemathesis.transports import RequestConfig


@pytest.fixture
def config_factory():
    def inner(base_url, request_proxy=None, request_tls_verify=False, request_cert=None, auth=None, headers=None):
        return probes.ProbeConfig(
            base_url=base_url,
            request=RequestConfig(
                proxy=request_proxy,
                tls_verify=request_tls_verify,
                cert=request_cert,
            ),
            auth=auth,
            auth_type=None,
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
    "kwargs, headers",
    (
        ({"request_cert": str(HERE.parent / "cli" / "cert.pem")}, {}),
        ({"auth": ("test", "test")}, {"Authorization": ["[Filtered]"]}),
    ),
)
def test_detect_null_byte_detected(openapi_30, config_factory, openapi3_base_url, kwargs, headers):
    config = config_factory(base_url=openapi3_base_url, **kwargs)
    results = probes.run(openapi_30, config)
    assert results == [
        probes.ProbeRun(
            probe=probes.NullByteInHeader(), outcome=probes.ProbeOutcome.FAILURE, request=ANY, response=ANY, error=None
        )
    ]
    assert results[0].serialize() == {
        "error": None,
        "name": "NULL_BYTE_IN_HEADER",
        "request": {
            "body": None,
            "body_size": None,
            "headers": {
                "X-Schemathesis-Probe": ["NULL_BYTE_IN_HEADER"],
                "X-Schemathesis-Probe-Null": ["\x00"],
                **DEFAULT_HEADERS,
                **headers,
            },
            "method": "GET",
            "uri": openapi3_base_url,
        },
        "response": None,
        "outcome": "failure",
    }


def test_detect_null_byte_with_response(openapi_30, config_factory, openapi3_base_url, response_factory):
    config = config_factory(base_url=openapi3_base_url)
    result = probes.run(openapi_30, config)[0]
    result.response = response_factory.requests(content=b'{"success": true}')
    assert result.serialize() == {
        "error": None,
        "name": "NULL_BYTE_IN_HEADER",
        "request": {
            "body": None,
            "body_size": None,
            "headers": {
                "X-Schemathesis-Probe": ["NULL_BYTE_IN_HEADER"],
                "X-Schemathesis-Probe-Null": ["\x00"],
                **DEFAULT_HEADERS,
            },
            "method": "GET",
            "uri": openapi3_base_url,
        },
        "response": {
            "body": "eyJzdWNjZXNzIjogdHJ1ZX0=",
            "body_size": 17,
            "elapsed": 0.0,
            "encoding": None,
            "headers": {
                "Content-Length": [
                    "17",
                ],
                "Content-Type": [
                    "application/json",
                ],
            },
            "http_version": "1.1",
            "message": None,
            "status_code": 200,
            "verify": True,
        },
        "outcome": "failure",
    }


def test_detect_null_byte_error(openapi_30, config_factory):
    config = config_factory(base_url="http://127.0.0.1:1")
    results = probes.run(openapi_30, config)
    assert results == [
        probes.ProbeRun(
            probe=probes.NullByteInHeader(), outcome=probes.ProbeOutcome.ERROR, request=ANY, response=None, error=ANY
        )
    ]
    serialized = results[0].serialize()
    serialized["error"] = canonicalize_error_message(results[0].error, False)
    system = platform.system()
    if system == "Windows":
        inner_error = "[WinError 10061] No connection could be made because the target machine actively refused it"
    elif system == "Darwin":
        inner_error = "[Errno 61] Connection refused"
    else:
        inner_error = "[Errno 111] Connection refused"
    assert serialized == {
        "error": (
            "requests.exceptions.ConnectionError: HTTPConnectionPool(host='127.0.0.1', port=1):  "
            "NewConnectionError('<urllib3.connection.HTTPConnection object at 0xbaaaaaaaaaad>: "
            f"Failed to establish a new connection: {inner_error}'))"
        ),
        "name": "NULL_BYTE_IN_HEADER",
        "request": {
            "body": None,
            "body_size": None,
            "headers": {
                "X-Schemathesis-Probe": ["NULL_BYTE_IN_HEADER"],
                "X-Schemathesis-Probe-Null": ["\x00"],
                **DEFAULT_HEADERS,
            },
            "method": "GET",
            "uri": "http://127.0.0.1:1/",
        },
        "response": None,
        "outcome": "error",
    }


def test_detect_null_byte_skipped(openapi_30, config_factory):
    config = config_factory(base_url=None)
    results = probes.run(openapi_30, config)
    assert results == [
        probes.ProbeRun(
            probe=probes.NullByteInHeader(), outcome=probes.ProbeOutcome.SKIP, request=None, response=None, error=None
        )
    ]
    assert results[0].serialize() == {
        "error": None,
        "name": "NULL_BYTE_IN_HEADER",
        "request": None,
        "response": None,
        "outcome": "skip",
    }
