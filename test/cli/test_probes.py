import platform
from pathlib import Path
from unittest.mock import ANY

import pytest

from schemathesis.cli import LoaderConfig, probes
from schemathesis.constants import USER_AGENT
from schemathesis.runner.impl.core import canonicalize_error_message


@pytest.fixture
def config_factory():
    def inner(base_url, request_proxy=None, request_tls_verify=False, request_cert=None, auth=None, headers=None):
        return LoaderConfig(
            schema_or_location="http://127.0.0.1/openapi.json",
            app=None,
            base_url=base_url,
            validate_schema=False,
            skip_deprecated_operations=False,
            data_generation_methods=(),
            force_schema_version=None,
            request_proxy=request_proxy,
            request_tls_verify=request_tls_verify,
            request_cert=request_cert,
            wait_for_schema=None,
            rate_limit=None,
            auth=auth,
            auth_type=None,
            headers=headers,
            endpoint=None,
            method=None,
            tag=None,
            operation_id=None,
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
        ({"request_cert": str(HERE / "cert.pem")}, {}),
        ({"auth": ("test", "test")}, {"Authorization": ["[Filtered]"]}),
    ),
)
def test_detect_null_byte_detected(openapi_30, config_factory, openapi3_base_url, kwargs, headers):
    config = config_factory(base_url=openapi3_base_url, **kwargs)
    results = probes.run(openapi_30, config)
    assert results == [
        probes.ProbeResult(
            probe=probes.NullByteInHeader(), type=probes.ProbeResultType.FAILURE, request=ANY, response=ANY, error=None
        )
    ]
    assert results[0].serialize() == {
        "error": None,
        "name": "NULL_BYTE_IN_HEADER",
        "request": {
            "body": None,
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
        "type": "failure",
    }


def test_detect_null_byte_error(openapi_30, config_factory):
    config = config_factory(base_url="http://127.0.0.1:1")
    results = probes.run(openapi_30, config)
    assert results == [
        probes.ProbeResult(
            probe=probes.NullByteInHeader(), type=probes.ProbeResultType.ERROR, request=ANY, response=None, error=ANY
        )
    ]
    serialized = results[0].serialize()
    serialized["error"] = canonicalize_error_message(results[0].error, False)
    if platform.system() == "Windows":
        inner_error = "[WinError 10061] No connection could be made because the target machine actively refused it"
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
            "headers": {
                "X-Schemathesis-Probe": ["NULL_BYTE_IN_HEADER"],
                "X-Schemathesis-Probe-Null": ["\x00"],
                **DEFAULT_HEADERS,
            },
            "method": "GET",
            "uri": "http://127.0.0.1:1/",
        },
        "response": None,
        "type": "error",
    }


def test_detect_null_byte_skipped(openapi_30, config_factory):
    config = config_factory(base_url=None)
    results = probes.run(openapi_30, config)
    assert results == [
        probes.ProbeResult(
            probe=probes.NullByteInHeader(), type=probes.ProbeResultType.SKIP, request=None, response=None, error=None
        )
    ]
    assert results[0].serialize() == {
        "error": None,
        "name": "NULL_BYTE_IN_HEADER",
        "request": None,
        "response": None,
        "type": "skip",
    }
