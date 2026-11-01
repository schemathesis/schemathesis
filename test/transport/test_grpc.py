import base64
import contextlib
import json
import re
import socket
import threading

import pytest
import requests
from google.protobuf import json_format, message_factory
from google.rpc import error_details_pb2

import schemathesis
from schemathesis.config import OutputConfig
from schemathesis.core.error_feedback import ErrorFeedbackStore, Observation, ObservationKind
from schemathesis.core.error_feedback.collector import parse_observations
from schemathesis.core.errors import IncorrectUsage, SerializationError
from schemathesis.core.failures import FailureGroup
from schemathesis.core.output import BINARY_PAYLOAD, format_response_payload
from schemathesis.core.parameters import ParameterLocation
from schemathesis.core.transport import CallOutcome
from schemathesis.generation.feedback import FeedbackSources
from schemathesis.generation.hypothesis.examples import generate_many
from test.apps.catalog.grpc.descriptors import (
    accounts_descriptor_set,
    all_types_descriptor_set,
    load,
    load_descriptor_set,
    users_descriptor_set,
)

pytest.importorskip("grpc", reason="grpcio has no free-threaded build")

ANY_URL = "type.googleapis.com/"
NO_USER_INFO = base64.b64encode(error_details_pb2.ErrorInfo(reason="NO_USER").SerializeToString()).decode()


def get_user(server, user_id, config=None, **kwargs):
    case = load(server, **(config or {}))["pkg.Users"]["GetUser"].Case(body={"userId": user_id})
    return case, case.call(**kwargs)


def test_ok_response(ctx):
    server = ctx.grpc.apps.users()
    _, response = get_user(server, 7)
    assert (
        response.status_code,
        response.status_line,
        response.headers,
        json.loads(response.render_content(response.content)),
        response.request.method,
        response.request.url,
    ) == (
        200,
        "OK",
        {
            "x-served-by": ["users"],
            "x-trace-bin": [base64.b64encode(b"\x00\x01").decode()],
            "grpc-status": ["0"],
            "grpc-message": [""],
        },
        {"name": "u7"},
        "POST",
        f"{server.target}/pkg.Users/GetUser",
    )


def test_internal_error(ctx):
    _, response = get_user(ctx.grpc.apps.users(), -1)
    assert (response.status_code, response.status_line, response.headers, response.content) == (
        500,
        "INTERNAL: negative id",
        {"grpc-status": ["13"], "grpc-message": ["negative id"]},
        b"",
    )


def test_unparsable_status_leaves_later_responses_intact(ctx):
    operation = load(ctx.grpc.apps.echo_status())["pkg.Users"]["GetUser"]
    responses = [operation.Case(body={"userId": user_id}).call() for user_id in (-1, 5)]
    assert [(response.status_code, response.status_line) for response in responses] == [
        (500, "INTERNAL: Stream removed (Error parsing 'grpc-status' metadata)"),
        (404, "NOT_FOUND"),
    ]


def test_ok_without_a_message_is_an_internal_error(ctx):
    response = load(ctx.grpc.apps.echo_status())["pkg.Users"]["GetUser"].Case(body={"userId": 0}).call()
    assert (response.status_code, response.status_line, response.headers, response.content) == (
        500,
        "INTERNAL: cardinality violation: received no response message from non-server-streaming RPC",
        {
            "grpc-status": ["13"],
            "grpc-message": ["cardinality violation: received no response message from non-server-streaming RPC"],
        },
        b"",
    )


def test_large_error_trailers(ctx):
    _, response = get_user(ctx.grpc.apps.users(), 400)
    assert (response.status_code, response.status_line, response.headers) == (
        400,
        "INVALID_ARGUMENT: invalid",
        {"x-details": ["x" * 65536], "grpc-status": ["3"], "grpc-message": ["invalid"]},
    )


def test_large_ok_response(ctx):
    _, response = get_user(ctx.grpc.apps.users(), 413)
    assert (response.status_line, len(response.content)) == ("OK", 5 * 1024 * 1024 + 5)


def test_malformed_ok_body_is_reported_as_binary(ctx):
    case = load(ctx.grpc.apps.users())["pkg.Users"]["GetUser"].Case(body={"userId": 17})
    with pytest.raises(FailureGroup) as exc_info:
        case.call_and_validate()
    assert str(exc_info.value).partition("Reproduce with")[0] == (
        "Schemathesis found 1 distinct failure\n\n"
        "- ST117 Response does not decode as the declared message\n\n"
        "    Cannot decode the response as `pkg.User`: Error parsing message with type 'pkg.User': "
        "Wire format was corrupt\n\n"
        "OK\n\n"
        "    <BINARY>\n\n"
    )


@pytest.mark.parametrize(
    ("user_id", "expected"),
    [(503, (503, "UNAVAILABLE")), (504, (504, "DEADLINE_EXCEEDED: upstream timed out"))],
    ids=["unavailable", "deadline-exceeded"],
)
def test_server_sent_status_is_a_response(ctx, user_id, expected):
    _, response = get_user(ctx.grpc.apps.users(), user_id)
    assert (response.status_code, response.status_line) == expected


def relay(source, target):
    with source, contextlib.suppress(OSError):
        while data := source.recv(65536):
            target.sendall(data)
        target.shutdown(socket.SHUT_WR)


def forward_first_connection(port):
    listener = socket.create_server(("127.0.0.1", 0))

    def accept():
        with listener:
            client, _ = listener.accept()
        upstream = socket.create_connection(("127.0.0.1", port))
        for source, target in ((client, upstream), (upstream, client)):
            threading.Thread(target=relay, args=(source, target), daemon=True).start()

    threading.Thread(target=accept, daemon=True).start()
    return listener.getsockname()[1]


# The connection the call used stays open, so only a new one shows that the target went away.
def test_server_unavailable_once_the_target_refuses_connections_is_a_connection_error(ctx):
    server = ctx.grpc.apps.users()
    port = forward_first_connection(int(server.target.rsplit(":", 1)[1]))
    operation = load(server, **{"base-url": f"grpc://127.0.0.1:{port}"})["pkg.Users"]["GetUser"]
    operation.Case(body={"userId": 7}).call()

    with pytest.raises(requests.exceptions.ConnectionError) as exc_info:
        operation.Case(body={"userId": 503}).call()
    assert isinstance(exc_info.value.__cause__, ConnectionAbortedError)


def test_status_code_agrees_with_call_outcome(ctx):
    case, response = get_user(ctx.grpc.apps.users(), 9)
    assert (response.status_code, case.operation.schema.classify_call_outcome(case, response)) == (
        409,
        CallOutcome.CONFLICT,
    )


def test_metadata(ctx):
    server = ctx.grpc.apps.users()
    case, _ = get_user(
        server,
        7,
        headers={
            "Authorization": "Bearer x",
            "Connection": "keep-alive",
            "Content-Type": "application/json",
            "TE": "trailers",
            "grpc-timeout": "1S",
            "X-Trace-Bin": base64.b64encode(b"\x00\x01").decode(),
        },
    )
    assert [(key, value) for key, value in server.calls[-1].metadata if key != "user-agent"] == [
        ("x-schemathesis-testcaseid", case.id),
        ("authorization", "Bearer x"),
        ("x-trace-bin", b"\x00\x01"),
    ]


def test_case_header_keeps_its_test_case_id(ctx):
    server = ctx.grpc.apps.users()
    load(server)["pkg.Users"]["GetUser"].Case(body={}, headers={"X-Schemathesis-TestCaseId": "mine"}).call()
    assert ("x-schemathesis-testcaseid", "mine") in server.calls[-1].metadata


def test_basic_auth_from_session(ctx):
    server = ctx.grpc.apps.users()
    session = requests.Session()
    session.auth = ("user", "pass")
    get_user(server, 7, session=session)
    assert ("authorization", "Basic dXNlcjpwYXNz") in server.calls[-1].metadata


def test_deadline_raises_timeout(ctx):
    with pytest.raises(requests.exceptions.ReadTimeout):
        get_user(ctx.grpc.apps.users(), 13, timeout=0.1)


# A channel that failed to connect waits before trying again and fails calls in the meantime without sending them.
def test_stopped_server_raises_connection_error_until_it_restarts(ctx):
    server = ctx.grpc.apps.users()
    server.stop()
    operation = load(server)["pkg.Users"]["GetUser"]
    with pytest.raises(requests.exceptions.ConnectionError) as exc_info:
        operation.Case(body={}).call()
    assert isinstance(exc_info.value.__cause__, ConnectionRefusedError)
    server.held_port.close()
    ctx.grpc.apps.users(port=int(server.target.rsplit(":", 1)[1]))
    assert operation.Case(body={"userId": 7}).call().status_line == "OK"


@pytest.mark.parametrize(
    "base_url", ["grpc://nonexistent-host.invalid:50051", "grpc://127.0.0.1:port"], ids=["host", "port"]
)
def test_unresolvable_host_raises_connection_refused(tmp_path, base_url):
    schema = load_descriptor_set(tmp_path, users_descriptor_set(), **{"base-url": base_url})
    case = schema["pkg.Users"]["GetUser"].Case(body={})
    with pytest.raises(requests.exceptions.ConnectionError) as exc_info:
        case.call()
    assert isinstance(exc_info.value.__cause__, ConnectionRefusedError)


@pytest.mark.parametrize("combined", [False, True], ids=["cert-and-key", "single-file"])
def test_tls_client_certificate(ctx, tmp_path, combined):
    server = ctx.grpc.apps.users(tls=True, client_auth=True)
    if combined:
        path = tmp_path / "combined.pem"
        path.write_bytes(server.client_key_path.read_bytes() + server.client_cert_path.read_bytes())
        cert = str(path)
    else:
        cert = (str(server.client_cert_path), str(server.client_key_path))
    get_user(server, 7, verify=str(server.ca_path), cert=cert)
    assert server.calls[-1].peer_names == [b"client"]


def test_settings_from_config(ctx):
    server = ctx.grpc.apps.users(tls=True, client_auth=True)
    config = {
        "tls-verify": str(server.ca_path),
        "request-cert": str(server.client_cert_path),
        "request-cert-key": str(server.client_key_path),
        "request-timeout": 0.1,
    }
    operation = load(server, **config)["pkg.Users"]["GetUser"]
    # The TLS handshake can outlast the short timeout on slow runners, so a first call opens the channel.
    operation.Case(body={"userId": 7}).call(timeout=10)
    assert server.calls[-1].peer_names == [b"client"]
    with pytest.raises(requests.exceptions.ReadTimeout):
        operation.Case(body={"userId": 13}).call()


def test_call_takes_a_rate_limit_slot(ctx):
    operation = load(ctx.grpc.apps.users(), **{"rate-limit": "1/h"})["pkg.Users"]["GetUser"]
    operation.Case(body={"userId": 7}).call()
    assert not operation.schema.config.rate_limit_for(operation=operation).try_acquire(blocking=False)


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        (
            {"verify": False},
            "`tls-verify=false` is not supported for gRPC targets; pass a CA bundle path instead",
        ),
        ({"verify": "."}, "gRPC targets need a CA bundle file, got a directory: `.`"),
        ({"proxies": {"all": "http://127.0.0.1:8080"}}, "Proxies are not supported for gRPC targets"),
        ({"config": {"proxy": "http://127.0.0.1:8080"}}, "Proxies are not supported for gRPC targets"),
    ],
    ids=["no-verification", "ca-directory", "proxies", "proxy-from-config"],
)
def test_unsupported_settings(ctx, kwargs, message):
    server = ctx.grpc.apps.users(tls=True)
    with pytest.raises(IncorrectUsage, match=re.escape(message)):
        get_user(server, 7, **kwargs)


def test_missing_base_url(ctx):
    server = ctx.grpc.apps.users()
    case = schemathesis.grpc.from_path(server.descriptor_set_path)["pkg.Users"]["GetUser"].Case(body={})
    with pytest.raises(IncorrectUsage, match="gRPC targets need a `grpc://` or `grpcs://` base URL"):
        case.call()


def test_bytes_body_is_sent_as_is(ctx):
    server = ctx.grpc.apps.users()
    case = load(server)["pkg.Users"]["GetUser"].Case(body=b"\x08\x07")
    response = case.call()
    assert json.loads(response.render_content(response.content)) == {"name": "u7"}


@pytest.mark.parametrize(
    ("limit", "expected"), [(3, BINARY_PAYLOAD), (4, '{\n  "name": "u7"\n}')], ids=["prefix", "whole"]
)
def test_truncated_response_goes_through_renderer(ctx, limit, expected):
    _, response = get_user(ctx.grpc.apps.users(), 7)
    assert format_response_payload(response.truncated(limit), config=OutputConfig()) == expected


def test_case_without_body_sends_default_message(ctx):
    response = load(ctx.grpc.apps.users())["pkg.Users"]["GetUser"].Case().call()
    assert json.loads(response.render_content(response.content)) == {"name": "u0"}


def test_calls_share_a_connection(ctx):
    server = ctx.grpc.apps.users()
    operation = load(server)["pkg.Users"]["GetUser"]
    operation.Case(body={"userId": 1}).call()
    operation.Case(body={"userId": 2}).call()
    assert server.calls[0].peer == server.calls[1].peer


def test_any_field_packed_with_schema_type_is_encoded(ctx, tmp_path):
    server = ctx.grpc.apps.users()
    schema = load_descriptor_set(tmp_path, all_types_descriptor_set(), **{"base-url": server.target})
    body = {"payload": {"@type": "type.googleapis.com/types.Node", "name": "n"}}
    response = schema["types.Types"]["Envelope"].Case(body=body).call()
    assert (response.status_line, response.request.body) == (
        "UNIMPLEMENTED: Method not found!",
        b"\x12%\n\x1etype.googleapis.com/types.Node\x12\x03\n\x01n",
    )


# Newer Pythons drop standard-alphabet characters from URL-safe base64 and warn about it first.
@pytest.mark.filterwarnings("error::FutureWarning")
@pytest.mark.parametrize(
    ("method", "body"),
    [
        ("Scalars", {"bytes": "+/8="}),
        ("WellKnown", {"bytesValue": "+/8="}),
        (
            "Envelope",
            {
                "payload": {
                    "@type": f"{ANY_URL}types.Blobs",
                    "chunks": ["+/8=", "/+8="],
                    "byName": {"k": "+/8="},
                    "scalars": {"bytes": "+/8="},
                }
            },
        ),
        ("Envelope", {"extras": {"k": {"@type": f"{ANY_URL}types.Scalars", "bytes": "+/8="}}}),
        ("Envelope", {"payload": {"@type": f"{ANY_URL}google.protobuf.BytesValue", "value": "+/8="}}),
        ("Envelope", {"payload": {"@type": f"{ANY_URL}google.protobuf.StringValue", "value": "a+b/c"}}),
        ("Envelope", {"payload": {"@type": f"{ANY_URL}legacy.Extendable", "[legacy.blob]": "+/8="}}),
    ],
    ids=["field", "wrapper", "packed-message", "packed-map-values", "packed-wrapper", "packed-string", "extension"],
)
def test_json_body_reaches_the_server_unchanged(ctx, tmp_path, method, body):
    server = ctx.grpc.apps.side_effects()
    schema = load_descriptor_set(tmp_path, all_types_descriptor_set(), **{"base-url": server.target})
    input_type = schema["types.Types"][method].definition.raw.input_type
    schema["types.Types"][method].Case(body=body).call()
    received = message_factory.GetMessageClass(input_type).FromString(server.calls[-1].payload)
    assert json_format.MessageToDict(received, descriptor_pool=input_type.file.pool) == body


def nested_nodes(depth):
    body = {"name": "n"}
    for _ in range(depth):
        body = {"name": "n", "children": [body]}
    return body


@pytest.mark.parametrize(
    ("method", "body", "message"),
    [
        ("Node", nested_nodes(400), "Message too deep"),
        ("Node", {"children": ["n"]}, "Failed to parse children field"),
        (
            "Envelope",
            {"payload": {"@type": f"{ANY_URL}legacy.Extendable", "[legacy.missing]": "+/8="}},
            r'has no field named "\[legacy.missing\]"',
        ),
    ],
    ids=["too-deep", "not-an-object", "unknown-extension"],
)
def test_unparsable_json_body_is_rejected(ctx, tmp_path, method, body, message):
    case = load_descriptor_set(tmp_path, all_types_descriptor_set())["types.Types"][method].Case(body=body)
    with pytest.raises(SerializationError, match=message):
        case.call(base_url=ctx.grpc.apps.side_effects().target)


@pytest.mark.parametrize(
    ("user_id", "expected"),
    [
        (7, {"response": {"@type": f"{ANY_URL}pkg.User", "name": "u7"}}),
        (
            -1,
            {"error": {"code": 5, "details": [{"@type": f"{ANY_URL}google.rpc.ErrorInfo", "value": NO_USER_INFO}]}},
        ),
    ],
    ids=["schema-type", "unknown-type"],
)
def test_any_field_is_rendered(ctx, user_id, expected):
    response = load(ctx.grpc.apps.users())["pkg.Admin"]["Reset"].Case(body={"userId": user_id}).call()
    assert json.loads(response.render_content(response.content)) == expected


def create_account(ctx, tmp_path):
    server = ctx.grpc.apps.users()
    schema = load_descriptor_set(tmp_path, accounts_descriptor_set(), **{"base-url": server.target})
    return schema["pkg.Accounts"]["CreateAccount"]


def missing(*path):
    return Observation(
        operation_label="pkg.Accounts/CreateAccount",
        location=ParameterLocation.BODY,
        parameter_path=path,
        kind=ObservationKind.MUST_NOT_BE_BLANK,
        raw_message="must not be empty",
    )


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ({}, (missing("displayName"), missing("profile", "contactEmail"))),
        ({"displayName": "", "profile": {}}, (missing("displayName"), missing("profile", "contactEmail"))),
        ({"displayName": "admin", "profile": {"contactEmail": "a"}}, ()),
    ],
    ids=["omitted", "empty", "reserved"],
)
def test_rejected_fields_are_observed(ctx, tmp_path, body, expected):
    case = create_account(ctx, tmp_path).Case(body=body)
    assert parse_observations(case.operation, case, case.call()) == expected


def test_observed_fields_are_filled(ctx, tmp_path):
    operation = create_account(ctx, tmp_path)
    store = ErrorFeedbackStore()
    rejected = operation.Case(body={})
    for observation in parse_observations(operation, rejected, rejected.call()):
        store.record(observation)
    cases = generate_many(operation.as_strategy(feedback=FeedbackSources(error_feedback=store)), 20)
    assert [case.call().status_code for case in cases] == [200] * 20
