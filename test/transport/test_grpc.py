import base64
import json
import re

import pytest
import requests

import schemathesis
from schemathesis.config import SchemathesisConfig
from schemathesis.core.errors import IncorrectUsage

pytest.importorskip("grpc", reason="grpcio has no free-threaded build")


def load(server, **config):
    config = SchemathesisConfig.from_dict({"base-url": server.target, **config})
    return schemathesis.grpc.from_path(server.descriptor_set_path, config=config)


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


def test_server_unavailable_is_a_response(ctx):
    _, response = get_user(ctx.grpc.apps.users(), 503)
    assert (response.status_code, response.status_line) == (503, "UNAVAILABLE")


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


def test_stopped_server_raises_connection_error(ctx):
    server = ctx.grpc.apps.users()
    server.stop()
    operation = load(server)["pkg.Users"]["GetUser"]
    with pytest.raises(requests.exceptions.ConnectionError) as exc_info:
        operation.Case(body={}).call()
    assert isinstance(exc_info.value.__cause__, ConnectionRefusedError)
    # The connectivity check stops watching the channel, so an outage does not pile up reconnecting watchers.
    (channel,) = operation.schema.get_transport(None)._channels.values()
    assert channel._connectivity_state.callbacks_and_connectivities == []


def test_tls_call(ctx):
    server = ctx.grpc.apps.users(tls=True)
    _, response = get_user(server, 7, verify=str(server.ca_path))
    assert response.status_code == 200


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
    with pytest.raises(requests.exceptions.ReadTimeout):
        get_user(server, 13, config=config)
    assert server.calls[-1].peer_names == [b"client"]


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


def test_calls_share_a_connection(ctx):
    server = ctx.grpc.apps.users()
    operation = load(server)["pkg.Users"]["GetUser"]
    operation.Case(body={"userId": 1}).call()
    operation.Case(body={"userId": 2}).call()
    assert server.calls[0].peer == server.calls[1].peer
