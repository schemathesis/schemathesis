import shlex

import pytest

import schemathesis
from schemathesis.config import SchemathesisConfig
from test.apps.catalog.grpc.descriptors import users_descriptor_set, write_descriptor_set


@pytest.mark.parametrize(
    ("base_url", "verify", "config", "body", "expected", "authorization"),
    [
        ("grpc://127.0.0.1:50051", True, {}, {"userId": 7}, "-plaintext -protoset {protoset}", "[Filtered]"),
        (
            "grpcs://127.0.0.1:50051",
            "/certs/ca.pem",
            {},
            {"userId": 7},
            "-protoset {protoset} -cacert /certs/ca.pem",
            "[Filtered]",
        ),
        (
            "grpcs://127.0.0.1:50051",
            True,
            {"tls-verify": "/certs/ca.pem"},
            {"userId": 7},
            "-protoset {protoset} -cacert /certs/ca.pem",
            "[Filtered]",
        ),
        ("grpc://127.0.0.1:50051", True, {}, b"\x08\x07", "-plaintext -protoset {protoset}", "[Filtered]"),
        (
            "grpc://127.0.0.1:50051",
            True,
            {"output": {"sanitization": {"enabled": False}}},
            {"userId": 7},
            "-plaintext -protoset {protoset}",
            "Bearer secret",
        ),
        (
            "grpcs://127.0.0.1:50051",
            True,
            {
                "tls-verify": "/certs/ca.pem",
                "request-cert": "/certs/client.pem",
                "request-cert-key": "/certs/client.key",
            },
            {"userId": 7},
            "-protoset {protoset} -cacert /certs/ca.pem -cert /certs/client.pem -key /certs/client.key",
            "[Filtered]",
        ),
        (
            "grpcs://127.0.0.1:50051",
            True,
            {"request-cert": "/certs/client.pem"},
            {"userId": 7},
            "-protoset {protoset} -cert /certs/client.pem -key /certs/client.pem",
            "[Filtered]",
        ),
        (
            "grpc://127.0.0.1:50051",
            True,
            {"tls-verify": "/certs/ca.pem", "request-cert": "/certs/client.pem"},
            {"userId": 7},
            "-plaintext -protoset {protoset}",
            "[Filtered]",
        ),
    ],
    ids=[
        "plaintext",
        "tls",
        "tls-from-config",
        "bytes-body",
        "no-sanitization",
        "client-certificate",
        "client-certificate-single-file",
        "plaintext-ignores-tls-settings",
    ],
)
def test_reproduction_command(tmp_path, base_url, verify, config, body, expected, authorization):
    path = write_descriptor_set(tmp_path, users_descriptor_set())
    schema = schemathesis.grpc.from_path(path, config=SchemathesisConfig.from_dict(config))
    case = schema["pkg.Users"]["GetUser"].Case(body=body, headers={"X-Request-Id": "r1"})
    # Headers as the sent request carries them, `content-length` included.
    headers = {
        "x-request-id": "r1",
        "Authorization": "Bearer secret",
        "x-schemathesis-testcaseid": "abc",
        "Content-Length": "2",
    }
    assert case.as_reproduction_command(headers=headers, verify=verify, base_url=base_url) == (
        f"grpcurl {expected.format(protoset=shlex.quote(str(path)))} -H 'x-request-id: r1' -H 'x-schemathesis-testcaseid: abc' "
        f"-H 'authorization: {authorization}' -d '{{\"userId\": 7}}' 127.0.0.1:50051 pkg.Users/GetUser"
    )
