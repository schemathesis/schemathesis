import os
import platform
import socket
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
import trustme
from google.protobuf import any_pb2

import schemathesis
from schemathesis.core.errors import IncorrectUsage, LoaderError, LoaderErrorKind
from schemathesis.grpc.checks import UndeclaredGrpcFields

pytest.importorskip("grpc", reason="grpcio has no free-threaded build")

# In the order the server lists its services.
LABELS = ["pkg.Admin/Reset", "pkg.Users/GetUser"]


def labels(schema):
    return [result.ok().label for result in schema.get_all_operations()]


@pytest.mark.parametrize("suffix", ["", "/"], ids=["bare", "trailing-slash"])
@pytest.mark.parametrize("version", ["v1", "v1alpha"])
def test_from_url_lists_operations(ctx, version, suffix):
    server = ctx.grpc.apps.users(reflection=version)
    schema = schemathesis.grpc.from_url(f"{server.target}{suffix}")
    assert (labels(schema), schema.location, schema.get_base_url()) == (
        LABELS,
        f"{server.target}{suffix}",
        server.target,
    )


def test_from_url_over_tls(ctx):
    server = ctx.grpc.apps.users(tls=True)
    assert labels(schemathesis.grpc.from_url(server.target, verify=str(server.ca_path))) == LABELS


@pytest.mark.filterwarnings("error")
def test_from_url_sends_headers_and_auth(ctx):
    server = ctx.grpc.apps.users()
    schemathesis.grpc.from_url(
        server.target,
        headers={
            "X-Token": "t",
            "X-Trace-Bin": "AAE=",
            "X-Unpadded-Bin": "AAE",
            "X-Url-Safe-Bin": "-_8",
            "X-Standard-Bin": "+/8=",
            "Connection": "keep-alive",
            "grpc-timeout": "1S",
        },
        auth=("user", "pass"),
    )
    assert {
        tuple((key, value) for key, value in metadata if key != "user-agent") for metadata in server.reflection_calls
    } == {
        (
            ("x-token", "t"),
            ("x-trace-bin", b"\x00\x01"),
            ("x-unpadded-bin", b"\x00\x01"),
            ("x-url-safe-bin", b"\xfb\xff"),
            ("x-standard-bin", b"\xfb\xff"),
            ("authorization", "Basic dXNlcjpwYXNz"),
        )
    }


def test_from_url_without_reflection(ctx):
    server = ctx.grpc.apps.users(reflection=None)
    with pytest.raises(LoaderError) as exc_info:
        schemathesis.grpc.from_url(server.target)
    assert (exc_info.value.kind, exc_info.value.message) == (
        LoaderErrorKind.GRPC_REFLECTION_UNAVAILABLE,
        "The server does not support reflection. Build a descriptor set with `buf build -o api.binpb` and run "
        f"`schemathesis run api.binpb --url {server.target}`",
    )


@pytest.mark.parametrize(
    ("options", "subject"),
    [
        ({"undescribed_services": ("pkg.Missing",)}, "pkg.Missing"),
        ({"unserved_files": ("google/protobuf/timestamp.proto",)}, "google/protobuf/timestamp.proto"),
    ],
    ids=["service", "import-with-message-types"],
)
def test_from_url_reports_undescribed_types(ctx, options, subject):
    server = ctx.grpc.apps.users(**options)
    with pytest.raises(LoaderError) as exc_info:
        schemathesis.grpc.from_url(server.target)
    assert (exc_info.value.kind, exc_info.value.message) == (
        LoaderErrorKind.GRPC_REFLECTION_UNAVAILABLE,
        f"The server cannot describe `{subject}` through reflection: not found. Build a descriptor set with "
        f"`buf build -o api.binpb` and run `schemathesis run api.binpb --url {server.target}`",
    )


def test_from_url_reports_types_missing_from_described_imports(ctx):
    server = ctx.grpc.apps.users(emptied_files=("google/protobuf/timestamp.proto",))
    with pytest.raises(LoaderError) as exc_info:
        schemathesis.grpc.from_url(server.target)
    assert (exc_info.value.kind, exc_info.value.message) == (
        LoaderErrorKind.GRPC_INVALID_DESCRIPTOR_SET,
        "The descriptor set does not load: couldn't resolve name '.google.protobuf.Timestamp'",
    )


def test_from_url_skips_undescribed_options_import(ctx):
    server = ctx.grpc.apps.users(annotated=True, unserved_files=("annotations.proto",))
    assert labels(schemathesis.grpc.from_url(server.target)) == LABELS


@pytest.mark.parametrize("version", ["v1", "v1alpha"])
def test_from_url_requests_missing_imports(ctx, version):
    server = ctx.grpc.apps.users(reflection=version, reflection_imports=False)
    assert labels(schemathesis.grpc.from_url(server.target)) == LABELS


@pytest.mark.parametrize("wait_for_schema", [None, 0.2], ids=["no-wait", "wait"])
def test_from_url_unreachable(ctx, wait_for_schema):
    server = ctx.grpc.apps.users()
    server.stop()
    with pytest.raises(LoaderError) as exc_info:
        schemathesis.grpc.from_url(server.target, wait_for_schema=wait_for_schema)
    assert exc_info.value.kind == LoaderErrorKind.CONNECTION_OTHER
    assert exc_info.value.message.startswith(f"Failed to connect to {server.target.removeprefix('grpc://')}: ")


def test_from_url_tls_untrusted_certificate(ctx, tmp_path):
    server = ctx.grpc.apps.users(tls=True)
    other_ca = tmp_path / "other-ca.pem"
    trustme.CA().cert_pem.write_to_path(str(other_ca))
    with pytest.raises(LoaderError) as exc_info:
        schemathesis.grpc.from_url(server.target, verify=str(other_ca))
    port = server.target.rsplit(":", 1)[1]
    assert (exc_info.value.kind, exc_info.value.message) == (
        LoaderErrorKind.INVALID_CERTIFICATE,
        f"TLS handshake with 127.0.0.1:{port} failed: failed to connect to all addresses; last error: UNKNOWN: "
        f"ipv4:127.0.0.1:{port}: Tls handshake failed (TSI_PROTOCOL_FAILURE): SSL_ERROR_SSL: error:1000007d:"
        "SSL routines:OPENSSL_internal:CERTIFICATE_VERIFY_FAILED: unable to get local issuer certificate: OK",
    )


@pytest.mark.parametrize(
    ("denial", "status", "details"),
    [
        ("1", "PERMISSION_DENIED", "denied"),
        ("authentication", "UNAUTHENTICATED", "missing credentials"),
    ],
    ids=["permission", "authentication"],
)
def test_from_url_reports_refused_reflection(ctx, denial, status, details):
    server = ctx.grpc.apps.users()
    with pytest.raises(LoaderError) as exc_info:
        schemathesis.grpc.from_url(server.target, headers={"x-deny": denial})
    assert (exc_info.value.kind, exc_info.value.message) == (
        LoaderErrorKind.HTTP_FORBIDDEN,
        f"Reflection failed with {status}: {details}",
    )


def test_from_url_reports_reflection_timeout(ctx):
    server = ctx.grpc.apps.users()
    with pytest.raises(LoaderError) as exc_info:
        schemathesis.grpc.from_url(server.target, headers={"x-stall": "1"}, timeout=0.01)
    assert (exc_info.value.kind, exc_info.value.message) == (
        LoaderErrorKind.NETWORK_OTHER,
        "Read timed out after 0.01 seconds",
    )


def test_from_url_reports_no_application_services(ctx):
    server = ctx.grpc.apps.users(
        reflection_services=("grpc.reflection.v1alpha.ServerReflection", "grpc.health.v1.Health")
    )
    with pytest.raises(LoaderError) as exc_info:
        schemathesis.grpc.from_url(server.target)
    assert (exc_info.value.kind, exc_info.value.message) == (
        LoaderErrorKind.GRPC_REFLECTION_UNAVAILABLE,
        "The server lists no services besides reflection and health. Build a descriptor set with "
        f"`buf build -o api.binpb` and run `schemathesis run api.binpb --url {server.target}`",
    )


@pytest.mark.parametrize(
    "service",
    ["grpc.channelz.v1.Channelz", "envoy.service.status.v3.ClientStatusDiscoveryService"],
    ids=["channelz", "csds"],
)
def test_from_url_skips_infrastructure_services(ctx, service):
    server = ctx.grpc.apps.users(reflection_services=("pkg.Users", "pkg.Admin", "grpc.health.v1.Health", service))
    assert labels(schemathesis.grpc.from_url(server.target)) == LABELS


@pytest.mark.parametrize(
    ("url", "verify", "message"),
    [
        ("grpcs://127.0.0.1:1", False, "`tls-verify=false` is not supported for gRPC targets"),
        ("grpc://127.0.0.1:1", ".", "gRPC targets need a CA bundle file, got a directory"),
        ("http://127.0.0.1:1", True, "gRPC targets need a `grpc://` or `grpcs://` base URL"),
        ("grpc://127.0.0.1:1/api", True, "gRPC targets need a base URL without a path"),
    ],
    ids=["tls-verify-false", "ca-directory", "http", "path"],
)
def test_from_url_rejects_unusable_targets(url, verify, message):
    with pytest.raises(IncorrectUsage, match=message):
        schemathesis.grpc.from_url(url, verify=verify)


def test_from_url_waits_for_schema(ctx):
    server = ctx.grpc.apps.users(start=False)
    threading.Timer(0.2, server.server.start).start()
    assert labels(schemathesis.grpc.from_url(server.target, wait_for_schema=5)) == LABELS


def load_while_server_starts(ctx, scheme, *, tls=False):
    # The port refuses connections until the server binds it, after the first reconnect attempt.
    with socket.socket() as reserved, ThreadPoolExecutor(max_workers=1) as executor:
        reserved.bind(("127.0.0.1", 0))
        port = reserved.getsockname()[1]
        loading = executor.submit(schemathesis.grpc.from_url, f"{scheme}://127.0.0.1:{port}", wait_for_schema=2)
        time.sleep(1.5)
        reserved.close()
        ctx.grpc.apps.users(port=port, tls=tls)
        return loading.result()


@pytest.mark.skipif(platform.system() == "Windows", reason="Unused ports do not refuse connections")
def test_from_url_waits_for_refusing_server_to_start(ctx):
    assert labels(load_while_server_starts(ctx, "grpc")) == LABELS


@pytest.mark.skipif(platform.system() == "Windows", reason="Unused ports do not refuse connections")
def test_from_url_reports_tls_failure_of_server_starting_during_wait(ctx):
    with pytest.raises(LoaderError, match=r"^TLS handshake with 127\.0\.0\.1:\d+ failed: ") as exc_info:
        load_while_server_starts(ctx, "grpcs", tls=True)
    assert exc_info.value.kind == LoaderErrorKind.INVALID_CERTIFICATE


def test_known_fields_pass_for_reflection_schemas(ctx, response_factory):
    operation = schemathesis.grpc.from_url(ctx.grpc.apps.users().target)["pkg.Users"]["GetUser"]
    response = response_factory.core(content=b"\x0a\x02u7", content_type=None, headers={"grpc-status": ["0"]})
    assert operation.validate_response(response, case=operation.Case(body={})) is None


UNKNOWN_FIELDS = [b"\x0a\x02u7\x98\x06\x01", b"\x0a\x02u7\x12\x03\x98\x06\x01"]


PACKED_USER = any_pb2.Any(type_url="type.googleapis.com/pkg.User", value=b"\x98\x06\x01").SerializeToString()
# `google.longrunning.Operation` whose `response` holds a `pkg.User` with field 99.
PACKED_UNKNOWN_FIELD = b"\x2a" + bytes([len(PACKED_USER)]) + PACKED_USER


@pytest.mark.parametrize(
    ("label", "content", "message"),
    [
        ("pkg.Users/GetUser", UNKNOWN_FIELDS[0], "`pkg.User` does not declare: `99`"),
        ("pkg.Users/GetUser", UNKNOWN_FIELDS[1], "`pkg.User` does not declare: `created_at.99`"),
        (
            "pkg.Users/GetUser",
            b"\x98\x06\x01\x98\x06\x02\x12\x06\x98\x06\x01\xa0\x06\x01",
            "`pkg.User` does not declare: `99`, `created_at.99`, `created_at.100`",
        ),
        ("pkg.Admin/Reset", PACKED_UNKNOWN_FIELD, "`google.longrunning.Operation` does not declare: `response.99`"),
    ],
    ids=["top-level", "nested", "several", "packed"],
)
def test_unknown_fields_fail_for_reflection_schemas(ctx, response_factory, label, content, message):
    operation = schemathesis.grpc.from_url(ctx.grpc.apps.users().target).find_operation_by_label(label)
    response = response_factory.core(content=content, content_type=None, headers={"grpc-status": ["0"]})
    with pytest.raises(UndeclaredGrpcFields) as exc_info:
        operation.validate_response(response, case=operation.Case(body={}))
    assert exc_info.value.message == f"The response carries fields that {message}"


# Extensions can be declared in files that reflection does not return.
def test_unknown_extension_numbers_pass_for_reflection_schemas(ctx, response_factory):
    operation = schemathesis.grpc.from_url(ctx.grpc.apps.envelope().target)["types.Types"]["Extendable"]
    response = response_factory.core(content=b"\xb0\x09\x01", content_type=None, headers={"grpc-status": ["0"]})
    assert operation.validate_response(response, case=operation.Case(body={})) is None


CALL_AND_VALIDATE = """
import sys
import schemathesis
operation = schemathesis.grpc.from_url(sys.argv[1])["pkg.Users"]["GetUser"]
case = operation.Case(body={"userId": 1}, headers={"x-undeclared-field": "1"})
try:
    operation.validate_response(case.call(), case=case)
except AssertionError as exc:
    print(type(exc).__name__)
    print(exc.message)
"""


# Without a native build for the interpreter, protobuf runs pure Python.
@pytest.mark.parametrize("implementation", ["upb", "python"])
def test_unknown_fields_fail_on_every_protobuf_runtime(ctx, implementation):
    result = subprocess.run(
        [sys.executable, "-c", CALL_AND_VALIDATE, ctx.grpc.apps.users().target],
        env={**os.environ, "PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION": implementation},
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout == "UndeclaredGrpcFields\nThe response carries fields that `pkg.User` does not declare: `99`\n"


def test_unknown_fields_report_once_per_operation(ctx, response_factory):
    operation = schemathesis.grpc.from_url(ctx.grpc.apps.users().target)["pkg.Users"]["GetUser"]
    failures = []
    for content in UNKNOWN_FIELDS:
        response = response_factory.core(content=content, content_type=None, headers={"grpc-status": ["0"]})
        with pytest.raises(UndeclaredGrpcFields) as exc_info:
            operation.validate_response(response, case=operation.Case(body={}))
        failures.append(exc_info.value)
    assert failures[0] == failures[1]


def test_reproduction_command_uses_reflection(ctx):
    server = ctx.grpc.apps.users()
    case = schemathesis.grpc.from_url(server.target)["pkg.Users"]["GetUser"].Case(body={"userId": 7})
    assert case.as_reproduction_command(headers={"x-schemathesis-testcaseid": "abc"}, verify=True) == (
        f"grpcurl -plaintext -H 'x-schemathesis-testcaseid: abc' -d '{{\"userId\": 7}}' "
        f"{server.target.removeprefix('grpc://')} pkg.Users/GetUser"
    )
