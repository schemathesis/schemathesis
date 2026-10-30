import base64

import pytest
from google.rpc import status_pb2

import schemathesis
from schemathesis.core.transport import CallOutcome
from schemathesis.grpc.checks import MalformedGrpcMessage
from test.apps.catalog.grpc.descriptors import users_descriptor_set, write_descriptor_set


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
