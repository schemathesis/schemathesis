from __future__ import annotations

import time
from concurrent import futures
from dataclasses import dataclass, field
from pathlib import Path

import grpc
import trustme
from google.protobuf import descriptor_database, descriptor_pb2, descriptor_pool, message_factory
from grpc_reflection.v1alpha import reflection, reflection_pb2

from test.apps.catalog.grpc.descriptors import side_effects_descriptor_set, users_descriptor_set, write_descriptor_set


@dataclass(slots=True)
class GrpcCall:
    method: str
    metadata: list[tuple[str, str | bytes]]
    # Common names of the client certificate the server saw.
    peer_names: list[bytes]
    # Client address; calls over one connection share it.
    peer: str


@dataclass(slots=True)
class TlsFiles:
    ca: trustme.CA
    server: trustme.LeafCert
    client: trustme.LeafCert


@dataclass(slots=True)
class GrpcApp:
    server: grpc.Server
    port: int
    calls: list[GrpcCall]
    reflection_calls: list[list[tuple[str, str | bytes]]]
    tls: TlsFiles | None = None
    descriptor_set: bytes = field(default_factory=users_descriptor_set)

    def make_server(self, directory: Path) -> GrpcServer:
        server = GrpcServer(
            target=f"grpc://127.0.0.1:{self.port}",
            descriptor_set_path=write_descriptor_set(directory, self.descriptor_set),
            calls=self.calls,
            reflection_calls=self.reflection_calls,
            server=self.server,
        )
        if self.tls is not None:
            server.target = f"grpcs://127.0.0.1:{self.port}"
            server.ca_path = directory / "ca.pem"
            self.tls.ca.cert_pem.write_to_path(str(server.ca_path))
            server.client_cert_path = directory / "client.pem"
            self.tls.client.cert_chain_pems[0].write_to_path(str(server.client_cert_path))
            server.client_key_path = directory / "client.key"
            self.tls.client.private_key_pem.write_to_path(str(server.client_key_path))
        return server


@dataclass(slots=True)
class GrpcServer:
    target: str
    descriptor_set_path: Path
    calls: list[GrpcCall]
    # Metadata of each reflection call.
    reflection_calls: list[list[tuple[str, str | bytes]]]
    server: grpc.Server
    ca_path: Path | None = None
    client_cert_path: Path | None = None
    client_key_path: Path | None = None

    def stop(self) -> None:
        self.server.stop(None).wait()


class RecordReflection(grpc.ServerInterceptor):
    def __init__(self) -> None:
        self.calls: list[list[tuple[str, str | bytes]]] = []

    def intercept_service(self, continuation, handler_call_details):
        if handler_call_details.method.startswith("/grpc.reflection."):
            metadata = [(item.key, item.value) for item in handler_call_details.invocation_metadata]
            self.calls.append(metadata)
            if ("x-deny", "1") in metadata:
                return grpc.stream_stream_rpc_method_handler(deny)
        return continuation(handler_call_details)


def deny(requests, context):
    context.abort(grpc.StatusCode.PERMISSION_DENIED, "denied")


class ReflectionServicer(reflection.ReflectionServicer):
    def __init__(self, names: tuple[str, ...], pool: descriptor_pool.DescriptorPool, *, imports: bool) -> None:
        super().__init__(names, pool=pool)
        self.imports = imports

    def _file_containing_symbol(self, request, fully_qualified_name):
        response = super()._file_containing_symbol(request, fully_qualified_name)
        # The containing file comes first, its imports after it.
        if not self.imports:
            del response.file_descriptor_response.file_descriptor_proto[1:]
        return response


def serve_reflection(
    server: grpc.Server,
    version: str,
    pool: descriptor_pool.DescriptorPool,
    *,
    undescribed_services: tuple[str, ...] = (),
    imports: bool = True,
) -> None:
    # Reflection lists the health service by name only; loaders must skip it with reflection itself.
    names = (
        "pkg.Users",
        "pkg.Admin",
        "grpc.health.v1.Health",
        f"grpc.reflection.{version}.ServerReflection",
        *undescribed_services,
    )
    # Both versions exchange the same messages.
    servicer = ReflectionServicer(names, pool=pool, imports=imports)
    handler = grpc.stream_stream_rpc_method_handler(
        servicer.ServerReflectionInfo,
        request_deserializer=reflection_pb2.ServerReflectionRequest.FromString,
        response_serializer=reflection_pb2.ServerReflectionResponse.SerializeToString,
    )
    server.add_generic_rpc_handlers(
        (
            grpc.method_handlers_generic_handler(
                f"grpc.reflection.{version}.ServerReflection", {"ServerReflectionInfo": handler}
            ),
        )
    )


def users(
    *,
    tls: bool = False,
    client_auth: bool = False,
    reflection_version: str | None = "v1alpha",
    undescribed_services: tuple[str, ...] = (),
    reflection_imports: bool = True,
    start: bool = True,
) -> GrpcApp:
    """`pkg.Users/GetUser` answers `u<id>`; a negative id fails, 13 is slow, 503 is a server-sent UNAVAILABLE.

    404 is NOT_FOUND, and 17 answers with bytes that do not decode. `pkg.Admin/Reset` answers with `u<id>` packed
    into `response`. Reflection refuses calls that carry `x-deny: 1`.
    Reflection also lists `undescribed_services`, which it answers with a NOT_FOUND error response.
    Without `reflection_imports`, a symbol lookup returns only its file, and imports come by file name.
    """
    database = descriptor_database.DescriptorDatabase()
    for file in descriptor_pb2.FileDescriptorSet.FromString(users_descriptor_set()).file:
        database.Add(file)
    pool = descriptor_pool.DescriptorPool(database)
    request_class = message_factory.GetMessageClass(pool.FindMessageTypeByName("pkg.GetUserRequest"))
    user_class = message_factory.GetMessageClass(pool.FindMessageTypeByName("pkg.User"))
    operation_class = message_factory.GetMessageClass(pool.FindMessageTypeByName("google.longrunning.Operation"))
    calls: list[GrpcCall] = []

    def record(method: str, context: grpc.ServicerContext) -> None:
        calls.append(
            GrpcCall(
                method=method,
                metadata=[(item.key, item.value) for item in context.invocation_metadata()],
                peer_names=list(context.auth_context().get("x509_common_name", [])),
                peer=context.peer(),
            )
        )

    def get_user(payload: bytes, context: grpc.ServicerContext) -> bytes:
        record("GetUser", context)
        user_id = request_class.FromString(payload).user_id
        if user_id < 0:
            context.abort(grpc.StatusCode.INTERNAL, "negative id")
        if user_id == 400:
            context.set_trailing_metadata((("x-details", "x" * 65536),))
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, "invalid")
        if user_id == 503:
            context.abort(grpc.StatusCode.UNAVAILABLE, "")
        if user_id == 404:
            context.abort(grpc.StatusCode.NOT_FOUND, "no such user")
        if user_id == 501:
            context.abort(grpc.StatusCode.UNIMPLEMENTED, "")
        if user_id == 9:
            context.abort(grpc.StatusCode.FAILED_PRECONDITION, "locked")
        if user_id == 401:
            context.abort(grpc.StatusCode.UNAUTHENTICATED, "no token")
        if user_id == 13:
            time.sleep(1)
        if user_id == 17:
            return b"\xff\xff\xff"
        context.send_initial_metadata((("x-served-by", "users"),))
        context.set_trailing_metadata((("x-trace-bin", b"\x00\x01"),))
        return user_class(name=f"u{user_id}").SerializeToString()

    def reset(payload: bytes, context: grpc.ServicerContext) -> bytes:
        record("Reset", context)
        operation = operation_class()
        operation.response.Pack(user_class(name=f"u{request_class.FromString(payload).user_id}"))
        return operation.SerializeToString()

    recorder = RecordReflection()
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=4), interceptors=[recorder])
    server.add_generic_rpc_handlers(
        (
            grpc.method_handlers_generic_handler(
                "pkg.Users", {"GetUser": grpc.unary_unary_rpc_method_handler(get_user)}
            ),
            grpc.method_handlers_generic_handler("pkg.Admin", {"Reset": grpc.unary_unary_rpc_method_handler(reset)}),
        )
    )
    if reflection_version is not None:
        serve_reflection(
            server,
            reflection_version,
            pool,
            undescribed_services=undescribed_services,
            imports=reflection_imports,
        )
    files = None
    if tls:
        ca = trustme.CA()
        files = TlsFiles(
            ca=ca, server=ca.issue_cert("127.0.0.1"), client=ca.issue_cert("client.example", common_name="client")
        )
        credentials = grpc.ssl_server_credentials(
            [(files.server.private_key_pem.bytes(), files.server.cert_chain_pems[0].bytes())],
            root_certificates=ca.cert_pem.bytes(),
            require_client_auth=client_auth,
        )
        port = server.add_secure_port("127.0.0.1:0", credentials)
    else:
        port = server.add_insecure_port("127.0.0.1:0")
    if start:
        server.start()
    return GrpcApp(server=server, port=port, calls=calls, reflection_calls=recorder.calls, tls=files)


class AnswerEmpty(grpc.GenericRpcHandler):
    def __init__(self, calls: list[GrpcCall]) -> None:
        self.calls = calls

    def service(self, handler_call_details):
        def answer(payload: bytes, context: grpc.ServicerContext) -> bytes:
            self.calls.append(
                GrpcCall(
                    method=handler_call_details.method.rsplit("/", 1)[1],
                    metadata=[(item.key, item.value) for item in context.invocation_metadata()],
                    peer_names=[],
                    peer=context.peer(),
                )
            )
            return b""

        return grpc.unary_unary_rpc_method_handler(answer)


def side_effects() -> GrpcApp:
    """Every `store.Store` method answers an empty message; calls are recorded in arrival order."""
    calls: list[GrpcCall] = []
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=4))
    server.add_generic_rpc_handlers((AnswerEmpty(calls),))
    port = server.add_insecure_port("127.0.0.1:0")
    server.start()
    return GrpcApp(
        server=server, port=port, calls=calls, reflection_calls=[], descriptor_set=side_effects_descriptor_set()
    )
