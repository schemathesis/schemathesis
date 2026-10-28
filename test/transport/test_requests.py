import platform
import socket
from collections.abc import Callable
from http.client import RemoteDisconnected

import pytest
import requests
from urllib3.exceptions import ProtocolError

import schemathesis
from schemathesis.transport.requests import _is_dropped_before_response
from test.fixtures.app_runner import accept_connections

pytestmark = pytest.mark.skipif(
    platform.system() == "Windows", reason="conn.close() on Windows does not raise ConnectionError on the client side"
)

KEEP_ALIVE_OK = b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: 2\r\n\r\n{}"


def serve_tcp(handle: Callable[[socket.socket, socket.socket], None]) -> tuple[int, list[socket.socket]]:
    listener = socket.create_server(("127.0.0.1", 0))
    accepted: list[socket.socket] = []

    def serve(connection: socket.socket) -> None:
        accepted.append(connection)
        with connection:
            handle(connection, listener)

    accept_connections(listener, serve)
    return listener.getsockname()[1], accepted


@pytest.fixture
def post_case(ctx):
    operation = ctx.openapi.load_operation("POST /items", {"responses": {"200": {"description": "OK"}}})
    return operation.Case()


def test_request_after_crash_on_same_connection(ctx):
    api = ctx.openapi.apps.crash_closes_connection()
    schema = schemathesis.openapi.from_url(api.schema_url)
    case = schema["/api/crash"]["POST"].Case(body={})
    with requests.Session() as session:
        assert [case.call(session=session).status_code for _ in range(3)] == [500, 500, 500]


@pytest.mark.parametrize("custom_adapter", [False, True], ids=["default-adapters", "custom-adapter"])
def test_request_dropped_on_fresh_connection_is_not_resent(post_case, custom_adapter):
    port, accepted = serve_tcp(lambda connection, listener: connection.recv(65536))
    with requests.Session() as session:
        if custom_adapter:
            session.mount("custom://", requests.adapters.BaseAdapter())
        for _ in range(3):
            with pytest.raises(requests.ConnectionError):
                post_case.call(base_url=f"http://127.0.0.1:{port}", session=session)
        session.adapters.pop("custom://", None)
    assert len(accepted) == 3


def test_request_retries_reconnect_until_exhausted(post_case):
    port, accepted = serve_tcp(lambda connection, listener: connection.recv(65536))
    post_case.operation.schema.config.request_retries = 1
    with pytest.raises(requests.ConnectionError):
        post_case.call(base_url=f"http://127.0.0.1:{port}")
    assert len(accepted) == 2


def test_failed_resend_raises_the_original_error(post_case):
    def answer_once_then_die(connection, listener):
        connection.recv(65536)
        connection.sendall(KEEP_ALIVE_OK)
        connection.recv(65536)
        listener.close()

    port, accepted = serve_tcp(answer_once_then_die)
    with requests.Session() as session:
        assert post_case.call(base_url=f"http://127.0.0.1:{port}", session=session).status_code == 200
        with pytest.raises(requests.ConnectionError) as exc_info:
            post_case.call(base_url=f"http://127.0.0.1:{port}", session=session)
    assert len(accepted) == 1
    reason = exc_info.value.args[0]
    assert isinstance(reason, ProtocolError)
    assert isinstance(reason.args[1], RemoteDisconnected)


def test_session_with_non_http_adapter_mount_still_sends_request(ctx):
    api = ctx.openapi.apps.success()
    schema = schemathesis.openapi.from_url(api.schema_url)
    case = schema["/api/success"]["GET"].Case()
    with requests.Session() as session:
        session.mount("custom://", requests.adapters.BaseAdapter())
        assert case.call(session=session).status_code == 200
        session.adapters.pop("custom://")


@pytest.mark.parametrize(
    "reason_type",
    [RemoteDisconnected, ConnectionResetError, BrokenPipeError, ConnectionAbortedError],
    ids=["remote_disconnected", "connection_reset", "broken_pipe", "connection_aborted"],
)
def test_dropped_before_response(reason_type):
    error = requests.ConnectionError(ProtocolError("Connection aborted.", reason_type("Connection dropped")))
    assert _is_dropped_before_response(error)


@pytest.mark.parametrize(
    "error",
    [requests.ConnectionError(), requests.ConnectionError(ProtocolError("Response ended prematurely"))],
    ids=["no-reason", "protocol-error-without-cause"],
)
def test_not_dropped_before_response(error):
    assert not _is_dropped_before_response(error)
