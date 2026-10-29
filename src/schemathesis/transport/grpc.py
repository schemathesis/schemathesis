from __future__ import annotations

import base64
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import grpc
import requests
from google.protobuf import json_format, message_factory
from typing_extensions import override

from schemathesis.core import NotSet
from schemathesis.core.errors import IncorrectUsage
from schemathesis.core.timing import Instant
from schemathesis.core.transport import DEFAULT_RESPONSE_TIMEOUT, Response
from schemathesis.specs.grpc.schemas import request_metadata
from schemathesis.specs.grpc.status import GRPC_STATUSES
from schemathesis.transport import BaseTransport

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from schemathesis.generation.case import Case

# How long a failed call waits for the channel to connect before blaming the network.
READY_TIMEOUT = 0.5

ChannelKey = tuple[str, bool, "bool | str", "str | tuple[str, str] | None"]


class GrpcTransport(BaseTransport["requests.Session"]):
    """Sends a case as a unary call; one instance per schema, so channels live as long as it."""

    def __init__(self) -> None:
        super().__init__()
        self._channels: dict[ChannelKey, grpc.Channel] = {}
        self._lock = threading.Lock()

    @override
    def send(
        self,
        case: Case,
        *,
        session: requests.Session | None = None,
        base_url: str | None = None,
        headers: Mapping[str, Any] | None = None,
        timeout: float | None = None,
        verify: bool | str | None = None,
        cert: str | tuple[str, str] | None = None,
        proxies: Mapping[str, str] | None = None,
        **kwargs: Any,
    ) -> Response:
        # Direct calls pass nothing, so the config fills in what the caller left out.
        config = case.operation.schema.config
        if proxies or config.proxy_for(operation=case.operation):
            raise IncorrectUsage("Proxies are not supported for gRPC targets")
        if verify is None:
            verify = config.tls_verify_for(operation=case.operation)
            verify = True if verify is None else verify
        cert = cert or config.request_cert_for(operation=case.operation)
        if timeout is None:
            timeout = config.request_timeout_for(operation=case.operation)
        base_url = base_url or case.operation.base_url
        address, tls = parse_target(base_url, verify)
        method = case.operation.definition.raw
        body = {} if isinstance(case.body, NotSet) else case.body
        if isinstance(body, bytes):
            payload = body
        else:
            message = message_factory.GetMessageClass(method.input_type)()
            # Well-known messages such as wrappers map to any JSON value, not only to objects.
            payload = json_format.ParseDict(cast("dict[str, Any]", body), message).SerializeToString()
        request_headers = request_metadata(case, headers)
        if session is not None and isinstance(session.auth, tuple) and len(session.auth) == 2:
            request_headers.setdefault("authorization", requests.auth._basic_auth_str(*session.auth))
        url = case.operation.schema.build_request_url(case, base_url)
        request = requests.Request(method="POST", url=url, headers=request_headers, data=payload).prepare()
        metadata = metadata_pairs(request_headers)
        channel = self._channel(address, tls, verify, cert)
        # No serializers: the call sends and receives bytes.
        call: grpc.UnaryUnaryMultiCallable[bytes, bytes] = channel.unary_unary(case.path)
        started_at = Instant()
        try:
            content, outcome = call.with_call(
                payload, timeout=timeout if timeout is not None else DEFAULT_RESPONSE_TIMEOUT, metadata=metadata
            )
        except grpc.RpcError as error:
            # Every error a unary call raises is also the call itself.
            outcome = cast(grpc.Call, error)
            code = outcome.code()
            if code == grpc.StatusCode.DEADLINE_EXCEEDED:
                raise requests.exceptions.ReadTimeout(outcome.details(), request=request) from error
            if code == grpc.StatusCode.UNAVAILABLE and not _is_ready(channel):
                raise requests.exceptions.ConnectionError(
                    outcome.details(), request=request
                ) from ConnectionRefusedError(f"Failed to connect to {address}")
            content = b""
        elapsed = started_at.elapsed
        code = outcome.code()
        details = outcome.details() or ""
        response_headers: dict[str, list[str]] = {}
        for key, value in (*(outcome.initial_metadata() or ()), *(outcome.trailing_metadata() or ())):
            text = base64.b64encode(value).decode() if isinstance(value, bytes) else value
            response_headers.setdefault(key, []).append(text)
        response_headers["grpc-status"] = [str(code.value[0])]
        response_headers["grpc-message"] = [details]
        render_content: Callable[[bytes], str] | None = None
        if code == grpc.StatusCode.OK:
            output_class = message_factory.GetMessageClass(method.output_type)

            def render_content(content: bytes) -> str:
                return json_format.MessageToJson(output_class.FromString(content))

        return Response(
            status_code=GRPC_STATUSES[code.value[0]][1],
            headers=response_headers,
            content=content,
            request=request,
            elapsed=elapsed,
            verify=verify is not False,
            message=code.name,
            http_version="2",
            render_content=render_content,
            status_line=f"{code.name}: {details}" if details else code.name,
        )

    def _channel(self, address: str, tls: bool, verify: bool | str, cert: str | tuple[str, str] | None) -> grpc.Channel:
        key: ChannelKey = (address, tls, verify, cert)
        with self._lock:
            channel = self._channels.get(key)
            if channel is None:
                channel = create_channel(address, tls, verify, cert)
                self._channels[key] = channel
            return channel


def parse_target(url: str, verify: bool | str) -> tuple[str, bool]:
    """Address and whether it uses TLS, for a `grpc://` or `grpcs://` URL."""
    if isinstance(verify, str) and Path(verify).is_dir():
        raise IncorrectUsage(f"gRPC targets need a CA bundle file, got a directory: `{verify}`")
    scheme, _, address = url.partition("://")
    if scheme not in ("grpc", "grpcs"):
        raise IncorrectUsage(f"gRPC targets need a `grpc://` or `grpcs://` base URL, got `{url}`")
    tls = scheme == "grpcs"
    if tls and verify is False:
        raise IncorrectUsage("`tls-verify=false` is not supported for gRPC targets; pass a CA bundle path instead")
    return address, tls


def metadata_pairs(headers: Mapping[str, str]) -> tuple[tuple[str, str | bytes], ...]:
    """Metadata as gRPC sends it: values of `-bin` keys travel as bytes."""
    return tuple((key, base64.b64decode(value) if key.endswith("-bin") else value) for key, value in headers.items())


def create_channel(address: str, tls: bool, verify: bool | str, cert: str | tuple[str, str] | None) -> grpc.Channel:
    if tls:
        return grpc.secure_channel(address, _credentials(verify, cert))
    return grpc.insecure_channel(address)


def _credentials(verify: bool | str, cert: str | tuple[str, str] | None) -> grpc.ChannelCredentials:
    # `True` trusts the system roots.
    root_certificates = Path(verify).read_bytes() if isinstance(verify, str) else None
    if cert is None:
        private_key = certificate_chain = None
    elif isinstance(cert, str):
        # One file holding both, as `requests` accepts.
        private_key = certificate_chain = Path(cert).read_bytes()
    else:
        certificate_chain, private_key = Path(cert[0]).read_bytes(), Path(cert[1]).read_bytes()
    return grpc.ssl_channel_credentials(root_certificates, private_key, certificate_chain)


def _is_ready(channel: grpc.Channel) -> bool:
    future = grpc.channel_ready_future(channel)
    try:
        future.result(timeout=READY_TIMEOUT)
    except grpc.FutureTimeoutError:
        # A pending check keeps watching the channel and asking it to reconnect.
        future.cancel()
        return False
    return True
