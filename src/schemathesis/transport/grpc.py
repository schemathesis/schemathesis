from __future__ import annotations

import base64
import json
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import grpc
import requests
from google.protobuf import message, message_factory
from typing_extensions import override

from schemathesis.core import Body
from schemathesis.core.errors import IncorrectUsage
from schemathesis.core.rate_limit import ratelimit
from schemathesis.core.timing import Instant
from schemathesis.core.transport import Response
from schemathesis.specs.grpc.converter import message_to_json
from schemathesis.specs.grpc.schemas import MEDIA_TYPE, request_metadata, request_payload
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
    # The server keeps no headers for reuse, so one unparsable response header cannot fail later responses.
    ("grpc.http2.hpack_table_size.decoder", 0),
)
# Channels with equal options share connections; the reachability probe needs its own.
PROBE_CHANNEL_OPTIONS = (*CHANNEL_OPTIONS, ("grpc.use_local_subchannel_pool", 1))

ChannelKey = tuple[str, bool, "bool | str", "str | tuple[str, str] | None"]


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
        # No serializers: the call sends and receives bytes.
        call: grpc.UnaryUnaryMultiCallable[bytes, bytes] = channel.unary_unary(case.path)
        try:
            with ratelimit(config.rate_limit_for(operation=case.operation), config.base_url):
                started_at = Instant()
                content, outcome = call.with_call(payload, timeout=timeout, metadata=metadata)
        except grpc.RpcError as error:
            # Every error a unary call raises is also the call itself.
            outcome = cast(grpc.Call, error)
            code = outcome.code()
            # Both errors are wrapped the way `requests` wraps its own, so error reports read the inner one.
            if code == grpc.StatusCode.DEADLINE_EXCEEDED:
                timed_out = TimeoutError(f"{base_url}: Read timed out. (read timeout={timeout})")
                raise requests.exceptions.ReadTimeout(timed_out, request=request) from error
            if code == grpc.StatusCode.UNAVAILABLE and not _is_reachable(address, tls, verify, cert):
                details = outcome.details() or ""
                # Once the server has answered, a call that did not fail on reconnecting reached it and may be
                # what brought it down; gRPC's own message is the only sign of a call retried on a new connection.
                if channel in self._answered and "failed to connect" not in details.lower():
                    aborted = ConnectionAbortedError(f"Connection aborted. {details}")
                    raise requests.exceptions.ConnectionError(aborted, request=request) from aborted
                refused = ConnectionRefusedError(f"Failed to connect to {base_url}")
                raise requests.exceptions.ConnectionError(refused, request=request) from refused
            content = b""
        self._answered.add(channel)
        elapsed = started_at.elapsed
        code = outcome.code()
        details = outcome.details() or ""
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
                    # Shown as binary; the response checks report the malformed message.
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
    if uses_proxy:
        raise IncorrectUsage("Proxies are not supported for gRPC targets")
    if auth is not None:
        headers.setdefault("authorization", requests.auth._basic_auth_str(*auth))
    return parse_target(url, verify)


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


def _is_ready(channel: grpc.Channel) -> bool:
    future = grpc.channel_ready_future(channel)
    try:
        future.result(timeout=READY_TIMEOUT)
    except grpc.FutureTimeoutError:
        # A pending check keeps watching the channel and asking it to reconnect.
        future.cancel()
        return False
    return True
