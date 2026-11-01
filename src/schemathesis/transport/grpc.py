from __future__ import annotations

import base64
import json
import socket
import ssl
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast
from urllib.parse import urlsplit

import grpc
import requests
from google.protobuf import message, message_factory
from typing_extensions import override

from schemathesis.core import Body
from schemathesis.core.rate_limit import ratelimit
from schemathesis.core.timing import Instant
from schemathesis.core.transport import Response
from schemathesis.specs.grpc.converter import message_to_json
from schemathesis.specs.grpc.schemas import MEDIA_TYPE, metadata_pairs, parse_target, request_metadata, request_payload
from schemathesis.specs.grpc.status import GRPC_STATUSES
from schemathesis.transport import BaseTransport, SerializationContext

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from schemathesis.generation.case import Case

# How long a failed call waits for the channel to connect before blaming the network.
READY_TIMEOUT = 0.5
CHANNEL_OPTIONS = (
    # Servers attach error details as metadata; the default 8 KB limit turns large ones into a client-side failure.
    ("grpc.max_metadata_size", 1024 * 1024),
    # Responses of any size get validated, instead of failing on the client past the default 4 MB limit.
    ("grpc.max_receive_message_length", -1),
    # The server keeps no headers for reuse, so one unparsable response header cannot fail later responses.
    ("grpc.http2.hpack_table_size.decoder", 0),
)
# Channels with equal options share connections; the reachability probe needs its own.
PROBE_CHANNEL_OPTIONS = (*CHANNEL_OPTIONS, ("grpc.use_local_subchannel_pool", 1))

ChannelKey = tuple[str, bool, "bool | str", "str | tuple[str, str] | None"]
# How the gRPC runtime reports a failed TLS handshake or certificate check.
TLS_FAILURE_MARKERS = ("Tls handshake failed", "verification check failed")
TLS_FAILED = "TLS handshake with {address} failed: {details}"
TLS_EXPECTED = "The server expects TLS; use `grpcs://{address}`"
NO_RESPONSE_MESSAGE = "cardinality violation: received no response message from non-server-streaming RPC"
# How gRPC fails a call it did not send because the channel has no connection.
NOT_CONNECTED = "failed to connect to all addresses"


class GrpcTransport(BaseTransport["requests.Session"]):
    """Sends a case as a unary call; one instance per schema, so channels live as long as it."""

    def __init__(self) -> None:
        super().__init__()
        self._channels: dict[ChannelKey, grpc.Channel] = {}
        # Channels the server answered on; a call dropped on any other one cannot be shown to have reached it.
        self._answered: set[grpc.Channel] = set()
        self._lock = threading.Lock()
        # Coverage cases go out only in media types the transport can encode.
        self.serializer(MEDIA_TYPE)(serialize_message)

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
        config = case.operation.schema.config
        # Direct calls pass nothing, so the config fills in what the caller left out.
        settings = config.request_settings_for(operation=case.operation)
        if verify is None:
            verify = True if settings.verify is None else settings.verify
        cert = cert or settings.cert
        if timeout is None:
            timeout = settings.network_timeout
        base_url = base_url or case.operation.base_url
        request_headers = request_metadata(case, headers)
        auth = (
            session.auth if session is not None and isinstance(session.auth, tuple) and len(session.auth) == 2 else None
        )
        address, tls = prepare_target(
            base_url, verify, request_headers, uses_proxy=bool(proxies or settings.proxy), auth=auth
        )
        method = case.operation.definition.raw
        payload = request_payload(case)
        url = case.operation.schema.build_request_url(case, base_url)
        request = requests.Request(method="POST", url=url, headers=request_headers, data=payload).prepare()
        metadata = metadata_pairs(request_headers)
        channel = self._channel(address, tls, verify, cert)
        # No serializers: the call sends and receives bytes, or no message at all.
        call: grpc.UnaryUnaryMultiCallable[bytes, bytes | None] = channel.unary_unary(case.path)
        # Whether the server refused a new connection, once a call fails before being sent.
        unreachable = False
        try:
            try:
                with ratelimit(config.rate_limit_for(operation=case.operation), config.base_url):
                    started_at = Instant()
                    content, outcome = call.with_call(payload, timeout=timeout, metadata=metadata)
            except grpc.RpcError as error:
                if not (cast(grpc.Call, error).details() or "").startswith(NOT_CONNECTED):
                    raise
                unreachable = not _is_reachable(address, tls, verify, cert)
                if unreachable:
                    raise
                # The server is up, but a channel that failed to connect waits before trying again and fails calls in
                # the meantime without sending them.
                started_at = Instant()
                content, outcome = call.with_call(payload, timeout=timeout, metadata=metadata, wait_for_ready=True)
        except grpc.RpcError as error:
            # Every error a unary call raises is also the call itself.
            outcome = cast(grpc.Call, error)
            code = outcome.code()
            # Errors are wrapped the way `requests` wraps its own, so error reports read the inner one.
            # Servers send DEADLINE_EXCEEDED with any text too; only one arriving after the timeout is a read timeout.
            if code == grpc.StatusCode.DEADLINE_EXCEEDED and started_at.elapsed >= timeout:
                timed_out = TimeoutError(f"{base_url}: Read timed out. (read timeout={timeout})")
                raise requests.exceptions.ReadTimeout(timed_out, request=request) from error
            if code == grpc.StatusCode.UNAVAILABLE and (unreachable or not _is_reachable(address, tls, verify, cert)):
                details = outcome.details() or ""
                failed_to_connect = "failed to connect" in details.lower()
                if any(marker in details for marker in TLS_FAILURE_MARKERS):
                    failure = ConnectionError(TLS_FAILED.format(address=address, details=details))
                    raise requests.exceptions.ConnectionError(failure, request=request) from failure
                # Once the server has answered, a call that did not fail on reconnecting reached it and may be
                # what brought it down; gRPC's own message is the only sign of a call retried on a new connection.
                if channel in self._answered and not failed_to_connect:
                    aborted = ConnectionAbortedError(f"Connection aborted. {details}")
                    raise requests.exceptions.ConnectionError(aborted, request=request) from aborted
                # A port proxy accepts the connection even when the server behind it is gone, then resets it.
                if failed_to_connect and "connection reset by peer" in details.lower():
                    reset = ConnectionResetError(f"Connection reset. {details}")
                    raise requests.exceptions.ConnectionError(reset, request=request) from reset
                # A plaintext connection to a server that speaks TLS closes without a handshake error.
                if not tls and accepts_tls(address):
                    mismatch = ConnectionError(f"{details}. {TLS_EXPECTED.format(address=address)}")
                    raise requests.exceptions.ConnectionError(mismatch, request=request) from mismatch
                refused = ConnectionRefusedError(f"Failed to connect to {base_url}")
                raise requests.exceptions.ConnectionError(refused, request=request) from refused
            content = b""
        self._answered.add(channel)
        elapsed = started_at.elapsed
        code = outcome.code()
        details = outcome.details() or ""
        if content is None:
            # A unary call answers with exactly one message, so clients turn an OK without one into an error.
            code = grpc.StatusCode.INTERNAL
            details = NO_RESPONSE_MESSAGE
            content = b""
        response_headers: dict[str, list[str]] = {}
        for key, value in (*(outcome.initial_metadata() or ()), *(outcome.trailing_metadata() or ())):
            text = base64.b64encode(value).decode() if isinstance(value, bytes) else value
            response_headers.setdefault(key, []).append(text)
        response_headers["grpc-status"] = [str(code.value[0])]
        response_headers["grpc-message"] = [details]
        render_content: Callable[[bytes], str | None] | None = None
        if code == grpc.StatusCode.OK:
            output_class = message_factory.GetMessageClass(method.output_type)

            def render_content(content: bytes) -> str | None:
                try:
                    rendered = message_to_json(output_class.FromString(content), method.output_type.file.pool)
                    return json.dumps(rendered, indent=2, ensure_ascii=False)
                except message.DecodeError:
                    # Shown as binary; response conformance reports the message or packed value that does not decode.
                    return None

        return Response(
            status_code=GRPC_STATUSES[code.value[0]].http_status,
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


def serialize_message(context: SerializationContext, body: Body) -> dict[str, bytes]:
    # Registered only to mark the media type as sendable; a call encodes its request message without it.
    raise NotImplementedError


def prepare_target(
    url: str, verify: bool | str, headers: dict[str, str], *, uses_proxy: bool, auth: tuple[str, str] | None
) -> tuple[str, bool]:
    """Address and TLS mode of `url`, with basic auth added to `headers`."""
    if auth is not None:
        headers.setdefault("authorization", requests.auth._basic_auth_str(*auth))
    return parse_target(url, verify, uses_proxy=uses_proxy)


def create_channel(
    address: str,
    tls: bool,
    verify: bool | str,
    cert: str | tuple[str, str] | None,
    options: tuple[tuple[str, int], ...] = CHANNEL_OPTIONS,
) -> grpc.Channel:
    if tls:
        return grpc.secure_channel(address, _credentials(verify, cert), options=options)
    return grpc.insecure_channel(address, options=options)


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


def _is_reachable(address: str, tls: bool, verify: bool | str, cert: str | tuple[str, str] | None) -> bool:
    # A new connection, since the one a dropped call used may still look connected for a moment afterwards.
    with create_channel(address, tls, verify, cert, options=PROBE_CHANNEL_OPTIONS) as channel:
        return _is_ready(channel)


def accepts_tls(address: str) -> bool:
    """Whether the server at `address` completes a TLS handshake, whatever certificate it presents."""
    parts = urlsplit(f"//{address}")
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    try:
        # gRPC targets without a port use 443.
        with socket.create_connection((parts.hostname, parts.port or 443), timeout=READY_TIMEOUT) as sock:
            context.wrap_socket(sock, server_hostname=parts.hostname).close()
    except (OSError, ValueError):
        return False
    return True


def _is_ready(channel: grpc.Channel) -> bool:
    future = grpc.channel_ready_future(channel)
    try:
        future.result(timeout=READY_TIMEOUT)
    except grpc.FutureTimeoutError:
        # A pending check keeps watching the channel and asking it to reconnect.
        future.cancel()
        return False
    return True
