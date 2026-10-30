import base64

import pytest
from google.protobuf import any_pb2
from google.rpc import error_details_pb2, status_pb2

import schemathesis
from schemathesis.checks import not_a_server_error
from schemathesis.core.error_feedback import Observation, ObservationKind
from schemathesis.core.error_feedback.collector import parse_observations
from schemathesis.core.failures import ServerError, Severity
from schemathesis.core.parameters import ParameterLocation
from schemathesis.core.transport import CallOutcome
from schemathesis.grpc.checks import MalformedGrpcMessage, UndecodableGrpcRequest
from schemathesis.specs.openapi.checks import ignored_auth
from test.apps.catalog.grpc.descriptors import all_types_descriptor_set, users_descriptor_set, write_descriptor_set
from test.utils import check_context


@pytest.fixture(scope="module")
def operation(tmp_path_factory):
    path = write_descriptor_set(tmp_path_factory.mktemp("grpc"), users_descriptor_set())
    return schemathesis.grpc.from_path(path)["pkg.Users"]["GetUser"]


def grpc_response(response_factory, *, status, content=b"", headers=None):
    return response_factory.core(
        content=content,
        content_type=None,
        status_code=200 if status == 0 else 500,
        headers={"grpc-status": [str(status)], **(headers or {})},
    )


def server_error(operation, response_factory, status, status_line, headers=None, body=None):
    response = response_factory.core(
        content=b"",
        content_type=None,
        status_code=500,
        headers={"grpc-status": [str(status)], **(headers or {})},
        status_line=status_line,
    )
    with pytest.raises(AssertionError) as exc_info:
        not_a_server_error(check_context(), response, operation.Case(body={} if body is None else body))
    return exc_info.value


@pytest.mark.parametrize(
    ("first", "second", "same"),
    [
        ((2, "UNKNOWN: no user 1"), (2, "UNKNOWN: no user 2"), True),
        ((13, "INTERNAL: corrupt"), (15, "DATA_LOSS: corrupt"), False),
    ],
    ids=["same-status", "different-statuses"],
)
def test_server_errors_are_told_apart_by_status(operation, response_factory, first, second, same):
    assert (
        server_error(operation, response_factory, *first) == server_error(operation, response_factory, *second)
    ) is same


@pytest.mark.parametrize(
    ("status", "outcome"),
    [
        (0, CallOutcome.ACCEPTED),
        (5, CallOutcome.UNREACHABLE),
        (6, CallOutcome.CONFLICT),
        (10, CallOutcome.CONFLICT),
        (9, CallOutcome.CONFLICT),
        (11, CallOutcome.CONFLICT),
        (3, CallOutcome.REJECTED),
        (13, CallOutcome.UNINFORMATIVE),
        (16, CallOutcome.UNINFORMATIVE),
        (12, CallOutcome.NOT_SERVED),
    ],
    ids=[
        "ok",
        "not-found",
        "already-exists",
        "aborted",
        "failed-precondition",
        "out-of-range",
        "invalid-argument",
        "internal",
        "unauthenticated",
        "unimplemented",
    ],
)
def test_call_outcome(operation, response_factory, status, outcome):
    case = operation.Case(body={})
    response = grpc_response(response_factory, status=status)
    assert operation.schema.classify_call_outcome(case, response) == outcome


@pytest.mark.parametrize(
    "details",
    [
        "grpc: failed to unmarshal the received message: unmarshal: invalid SpanID length",
        "grpc: error unmarshalling request: unexpected EOF",
        "Exception deserializing request!",
        "Error deserializing request: index out of range",
    ],
    ids=["go", "go-legacy", "python", "node"],
)
def test_library_decode_error(operation, response_factory, details):
    failure = server_error(operation, response_factory, 13, "INTERNAL", headers={"grpc-message": [details]})
    assert (failure, failure.severity) == (
        UndecodableGrpcRequest(operation=operation.label, status_code=500, status_name="INTERNAL"),
        Severity.CRITICAL,
    )


# Messages a client library produces when a response does not decode, so a handler may pass them on.
@pytest.mark.parametrize(
    ("status", "status_line", "details", "body"),
    [
        (13, "INTERNAL", "negative id", None),
        (2, "UNKNOWN", "Exception deserializing request!", None),
        (13, "INTERNAL", None, None),
        (13, "INTERNAL", "Invalid protobuf byte sequence", None),
        (13, "INTERNAL", "failed to decode Protobuf message: invalid varint", None),
        (13, "INTERNAL", "Exception deserializing request!", b"\xff"),
    ],
    ids=["handler-internal", "unknown", "no-message", "java-client", "rust-client", "raw-bytes-body"],
)
def test_handler_errors_stay_server_errors(operation, response_factory, status, status_line, details, body):
    headers = {} if details is None else {"grpc-message": [details]}
    assert server_error(operation, response_factory, status, status_line, headers=headers, body=body) == ServerError(
        operation=operation.label, status_code=500, status_name=status_line
    )


def test_decodable_responses_pass(operation, response_factory):
    details = base64.b64encode(status_pb2.Status(code=13, message="boom").SerializeToString()).decode()
    for response in (
        grpc_response(response_factory, status=0, content=b"\x0a\x02u7"),
        grpc_response(response_factory, status=13),
        grpc_response(response_factory, status=13, headers={"grpc-status-details-bin": [details]}),
    ):
        assert operation.validate_response(response, case=operation.Case(body={})) is None


def test_undecodable_ok_response(operation, response_factory):
    response = grpc_response(response_factory, status=0, content=b"\xff")
    with pytest.raises(MalformedGrpcMessage, match="Cannot decode the response as `pkg.User`"):
        operation.validate_response(response, case=operation.Case(body={}))


def test_bad_status_details(operation, response_factory):
    response = grpc_response(response_factory, status=13, headers={"grpc-status-details-bin": ["/w=="]})
    with pytest.raises(MalformedGrpcMessage, match="Cannot decode the status details as `google.rpc.Status`"):
        operation.validate_response(response, case=operation.Case(body={}))


# Without a run to remember the verdict, every successful call is resent without credentials.
def test_ignored_auth_outside_a_run(ctx):
    pytest.importorskip("grpc", reason="grpcio has no free-threaded build")
    server = ctx.grpc.apps.users()
    operation = schemathesis.grpc.from_url(server.target)["pkg.Users"]["GetUser"]
    case = operation.Case(body={"userId": 403}, headers={"authorization": "Bearer x"})
    case.call_and_validate(checks=[ignored_auth])
    case.call_and_validate(checks=[ignored_auth])
    assert [dict(call.metadata).get("authorization") for call in server.calls] == ["Bearer x", None, "Bearer x", None]


def test_failures_group_by_message(operation, response_factory):
    responses = [
        grpc_response(response_factory, status=0, content=b"\xff"),
        grpc_response(response_factory, status=0, content=b"\x0a"),
        grpc_response(response_factory, status=13, headers={"grpc-status-details-bin": ["/w=="]}),
    ]
    failures = set()
    for response in responses:
        with pytest.raises(MalformedGrpcMessage) as exc_info:
            operation.validate_response(response, case=operation.Case(body={}))
        failures.add(exc_info.value)
    assert sorted(failure.type_name for failure in failures) == ["google.rpc.Status", "pkg.User"]


def test_unknown_fields_pass_for_descriptor_sets(operation, response_factory):
    response = grpc_response(response_factory, status=0, content=b"\x0a\x02u7\x98\x06\x01")
    assert operation.validate_response(response, case=operation.Case(body={})) is None


def test_unimplemented_is_not_a_server_error(ctx):
    pytest.importorskip("grpc", reason="grpcio has no free-threaded build")
    operation = schemathesis.grpc.from_url(ctx.grpc.apps.users().target)["pkg.Users"]["GetUser"]
    assert operation.Case(body={"userId": 501}).call_and_validate().status_line == "UNIMPLEMENTED"


def rejection(response_factory, *fields):
    details = status_pb2.Status(code=3)
    details.details.append(any_pb2.Any(type_url="type.googleapis.com/example.Unknown", value=b"\x08\x01"))
    details.details.add().Pack(
        error_details_pb2.BadRequest(
            field_violations=[
                error_details_pb2.BadRequest.FieldViolation(field=field, description="invalid") for field in fields
            ]
        )
    )
    encoded = base64.b64encode(details.SerializeToString()).decode()
    return response_factory.core(
        content=b"",
        content_type=None,
        status_code=400,
        headers={"grpc-status": ["3"], "grpc-status-details-bin": [encoded]},
    )


def test_unknown_status_detail_types_pass(operation, response_factory):
    assert operation.validate_response(rejection(response_factory, "user_id"), case=operation.Case(body={})) is None


@pytest.fixture(scope="module")
def types(tmp_path_factory):
    path = write_descriptor_set(tmp_path_factory.mktemp("grpc"), all_types_descriptor_set())
    return schemathesis.grpc.from_path(path)["types.Types"]


@pytest.mark.parametrize(
    ("method", "body", "field", "path"),
    [
        ("Scalars", {}, "string", ("string",)),
        ("Scalars", {}, "tags", ("tags",)),
        ("Choice", {}, "note", ("note",)),
        ("Envelope", {}, "first.extra", ("first", "extra")),
        ("Envelope", {}, "first.title", ("first", "title")),
        ("Envelope", {"payload": {"@type": "type.googleapis.com/types.Node", "name": "a"}}, "id", ("id",)),
        ("Node", {}, "name", ("name",)),
    ],
    ids=["implicit-presence", "repeated", "explicit-presence", "nested", "custom-json-name", "packed-any", "recursive"],
)
def test_violations_on_missing_fields_are_observed(types, response_factory, method, body, field, path):
    operation = types[method]
    case = operation.Case(body=body)
    assert parse_observations(operation, case, rejection(response_factory, field)) == (
        Observation(
            operation_label=operation.label,
            location=ParameterLocation.BODY,
            parameter_path=path,
            kind=ObservationKind.MUST_NOT_BE_BLANK,
            raw_message="invalid",
        ),
    )


@pytest.mark.parametrize("field", ["user_id", "userId"], ids=["proto-name", "json-name"])
def test_violation_field_names_map_to_json_names(operation, response_factory, field):
    case = operation.Case()
    assert [
        observation.parameter_path
        for observation in parse_observations(operation, case, rejection(response_factory, field))
    ] == [("userId",)]


@pytest.mark.parametrize(
    ("method", "body", "field"),
    [
        ("Scalars", {"string": "a"}, "string"),
        ("Scalars", {"tags": [1]}, "tags"),
        ("Choice", {"note": ""}, "note"),
        ("Scalars", b"\x08\x05", "int32"),
        ("Scalars", {}, "unknown"),
        ("Scalars", {}, "tags[0]"),
        ("Scalars", {}, "string.value"),
        ("Node", {}, "children.name"),
    ],
    ids=[
        "implicit-presence",
        "repeated",
        "explicit-presence",
        "binary-body",
        "unknown-field",
        "indexed",
        "scalar-parent",
        "repeated-parent",
    ],
)
def test_violations_without_a_missing_field_are_ignored(types, response_factory, method, body, field):
    operation = types[method]
    case = operation.Case(body=body)
    assert parse_observations(operation, case, rejection(response_factory, field)) == ()


def corrupt_bad_request():
    details = status_pb2.Status(code=3)
    details.details.append(any_pb2.Any(type_url="type.googleapis.com/google.rpc.BadRequest", value=b"\xff"))
    return base64.b64encode(details.SerializeToString()).decode()


@pytest.mark.parametrize(
    ("body", "details"),
    [({}, ["/w=="]), ({}, [corrupt_bad_request()]), ({"unknown": 1}, [])],
    ids=["malformed-status", "malformed-bad-request", "no-details"],
)
def test_rejections_without_field_violations_teach_nothing(operation, response_factory, body, details):
    response = response_factory.core(
        content=b"",
        content_type=None,
        status_code=400,
        headers={"grpc-status": ["3"], "grpc-status-details-bin": details},
    )
    assert parse_observations(operation, operation.Case(body=body), response) == ()
