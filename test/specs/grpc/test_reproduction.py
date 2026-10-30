import json
import shlex

import pytest

import schemathesis
from schemathesis.config import SchemathesisConfig
from test.apps.catalog.grpc.descriptors import all_types_descriptor_set, users_descriptor_set, write_descriptor_set


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


@pytest.mark.parametrize(
    ("method", "body", "expected"),
    [
        (
            "WellKnown",
            {"fieldMask": "a,bC", "timestamp": "1970-01-01T00:00:01Z"},
            {"fieldMask": {"paths": ["a", "b_c"]}, "timestamp": "1970-01-01T00:00:01Z"},
        ),
        ("WellKnown", {"fieldMask": ""}, {"fieldMask": {"paths": []}}),
        ("WellKnown", b"\x1a\x00", {"fieldMask": {"paths": []}}),
        ("Node", {"name": "a", "children": [{"name": "b"}]}, {"name": "a", "children": [{"name": "b"}]}),
        ("Maps", {"byInt32": {"1": "a"}}, {"byInt32": {"1": "a"}}),
        ("Envelope", {"extras": {"k": {"@type": "x"}}}, {"extras": {"k": {"@type": "x"}}}),
    ],
    ids=["field-mask", "empty-field-mask", "empty-field-mask-bytes", "repeated", "map", "map-of-messages"],
)
def test_reproduction_command_field_mask_as_object(tmp_path, method, body, expected):
    path = write_descriptor_set(tmp_path, all_types_descriptor_set())
    case = schemathesis.grpc.from_path(path)["types.Types"][method].Case(body=body)
    assert case.as_reproduction_command(headers={}, verify=True, base_url="grpc://127.0.0.1:50051") == (
        f"grpcurl -plaintext -protoset {shlex.quote(str(path))} -H 'x-schemathesis-testcaseid: {case.id}' "
        f"-d {shlex.quote(json.dumps(expected))} 127.0.0.1:50051 types.Types/{method}"
    )


UNKNOWN_ANY = {"@type": "type.googleapis.com/x.X", "value": "CAE="}


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        (
            b"\x12%\n\x1etype.googleapis.com/types.Node\x12\x03\n\x01n",
            {"payload": {"@type": "type.googleapis.com/types.Node", "name": "n"}},
        ),
        (
            b'\x12\x1d\n\x17type.googleapis.com/x.X\x12\x02\x08\x01\x1a"\n\x01k\x12\x1d\n\x17type.googleapis.com/x.X'
            b'\x12\x02\x08\x01"\x1f\n\x1d\n\x17type.googleapis.com/x.X\x12\x02\x08\x01',
            {"payload": UNKNOWN_ANY, "extras": {"k": UNKNOWN_ANY}, "first": {"extra": UNKNOWN_ANY}},
        ),
    ],
    ids=["schema-type", "unknown-type"],
)
def test_reproduction_command_decodes_any(tmp_path, body, expected):
    path = write_descriptor_set(tmp_path, all_types_descriptor_set())
    case = schemathesis.grpc.from_path(path)["types.Types"]["Envelope"].Case(body=body)
    assert case.as_reproduction_command(
        headers={"x-schemathesis-testcaseid": "abc"}, verify=True, base_url="grpc://127.0.0.1:50051"
    ) == (
        f"grpcurl -plaintext -protoset {shlex.quote(str(path))} -H 'x-schemathesis-testcaseid: abc' "
        f"-d {shlex.quote(json.dumps(expected))} 127.0.0.1:50051 types.Types/Envelope"
    )
