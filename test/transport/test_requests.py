import platform
import socket
import threading
from collections.abc import Callable
from http.client import RemoteDisconnected

import pytest
import requests
from urllib3.exceptions import ProtocolError

import schemathesis

pytestmark = pytest.mark.skipif(
    platform.system() == "Windows", reason="conn.close() on Windows does not raise ConnectionError on the client side"
)

KEEP_ALIVE_OK = b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: 2\r\n\r\n{}"


def serve_tcp(handle: Callable[[socket.socket, socket.socket], None]) -> tuple[int, list[socket.socket]]:
    listener = socket.create_server(("127.0.0.1", 0))
    accepted: list[socket.socket] = []

    def serve() -> None:
        while True:
            try:
                connection, _ = listener.accept()
            except OSError:
                return
            accepted.append(connection)
            with connection:
                handle(connection, listener)

    threading.Thread(target=serve, daemon=True).start()
    return listener.getsockname()[1], accepted


@pytest.fixture
def post_case(ctx):
    schema = ctx.openapi.load_schema({"/items": {"post": {"responses": {"200": {"description": "OK"}}}}})
    return schema["/items"]["POST"].Case()


def test_request_after_crash_on_same_connection(ctx):
    api = ctx.openapi.apps.crash_closes_connection()
    schema = schemathesis.openapi.from_url(api.schema_url)
    case = schema["/api/crash"]["POST"].Case(body={})
    with requests.Session() as session:
        assert [case.call(session=session).status_code for _ in range(3)] == [500, 500, 500]


def test_request_dropped_on_fresh_connection_is_not_resent(post_case):
    port, accepted = serve_tcp(lambda connection, listener: connection.recv(65536))
    with requests.Session() as session:
        for _ in range(3):
            with pytest.raises(requests.ConnectionError):
                post_case.call(base_url=f"http://127.0.0.1:{port}", session=session)
    assert len(accepted) == 3


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
