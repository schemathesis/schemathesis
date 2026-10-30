from __future__ import annotations

import secrets
import time
from concurrent import futures
from dataclasses import dataclass, field
from pathlib import Path

import grpc
import trustme
from google.protobuf import any_pb2, descriptor_database, descriptor_pb2, descriptor_pool, message_factory
from google.rpc import error_details_pb2, status_pb2
from grpc_reflection.v1alpha import reflection, reflection_pb2

from test.apps.catalog.grpc.descriptors import (
    accounts_descriptor_set,
    library_descriptor_set,
    side_effects_descriptor_set,
    users_descriptor_set,
    write_descriptor_set,
)


@dataclass(slots=True)
class GrpcCall:
    method: str
    metadata: list[tuple[str, str | bytes]]
    # Common names of the client certificate the server saw.
    peer_names: list[bytes]
    # Client address; calls over one connection share it.
    peer: str
    # Serialized request message.
    payload: bytes


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
    # Reflection lists the health service by name only; loaders must skip it with reflection itself.
    services: tuple[str, ...] = ("pkg.Users", "pkg.Admin", "grpc.health.v1.Health"),
    undescribed_services: tuple[str, ...] = (),
    imports: bool = True,
) -> None:
    names = (*services, f"grpc.reflection.{version}.ServerReflection", *undescribed_services)
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

    404 is NOT_FOUND, 17 answers with bytes that do not decode, and 422 fails in the request deserializer. `pkg.Admin/Reset` answers with `u<id>` packed
    into `response`. Reflection refuses calls that carry `x-deny: 1`.
    Reflection also lists `undescribed_services`, which it answers with a NOT_FOUND error response.
    Without `reflection_imports`, a symbol lookup returns only its file, and imports come by file name.
    `pkg.Accounts/CreateAccount` (in `accounts_descriptor_set()`, not reflected) rejects an empty `display_name`,
    an empty `profile.contact_email` and the reserved name `admin` with INVALID_ARGUMENT, listing each field in
    `google.rpc.BadRequest` details next to a detail of a type clients do not know.
    """
    database = descriptor_database.DescriptorDatabase()
    for file in descriptor_pb2.FileDescriptorSet.FromString(users_descriptor_set()).file:
        database.Add(file)
    pool = descriptor_pool.DescriptorPool(database)
    request_class = message_factory.GetMessageClass(pool.FindMessageTypeByName("pkg.GetUserRequest"))
    user_class = message_factory.GetMessageClass(pool.FindMessageTypeByName("pkg.User"))
    operation_class = message_factory.GetMessageClass(pool.FindMessageTypeByName("google.longrunning.Operation"))
    accounts_pool = descriptor_pool.DescriptorPool()
    for file in descriptor_pb2.FileDescriptorSet.FromString(accounts_descriptor_set()).file:
        accounts_pool.Add(file)
    account_request_class = message_factory.GetMessageClass(
        accounts_pool.FindMessageTypeByName("pkg.CreateAccountRequest")
    )
    account_class = message_factory.GetMessageClass(accounts_pool.FindMessageTypeByName("pkg.Account"))
    calls: list[GrpcCall] = []

    def record(method: str, context: grpc.ServicerContext, payload: bytes = b"") -> None:
        calls.append(
            GrpcCall(
                method=method,
                metadata=[(item.key, item.value) for item in context.invocation_metadata()],
                peer_names=list(context.auth_context().get("x509_common_name", [])),
                peer=context.peer(),
                payload=payload,
            )
        )

    def parse_request(payload: bytes) -> int:
        user_id = request_class.FromString(payload).user_id
        # The gRPC library answers INTERNAL itself when the request deserializer fails.
        if user_id == 422:
            raise ValueError("invalid user id")
        return user_id

    def get_user(user_id: int, context: grpc.ServicerContext) -> bytes:
        record("GetUser", context)
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
        if user_id == 403 and not any(item.key == "authorization" for item in context.invocation_metadata()):
            context.abort(grpc.StatusCode.UNAUTHENTICATED, "missing credentials")
        context.send_initial_metadata((("x-served-by", "users"),))
        context.set_trailing_metadata((("x-trace-bin", b"\x00\x01"),))
        return user_class(name=f"u{user_id}").SerializeToString()

    def create_account(payload: bytes, context: grpc.ServicerContext) -> bytes:
        record("CreateAccount", context)
        request = account_request_class.FromString(payload)
        violations = []
        if not request.display_name:
            violations.append(("display_name", "must not be empty"))
        elif request.display_name == "admin":
            violations.append(("display_name", "is reserved"))
        if not request.profile.contact_email:
            violations.append(("profile.contact_email", "must not be empty"))
        if violations:
            bad_request = error_details_pb2.BadRequest(
                field_violations=[
                    error_details_pb2.BadRequest.FieldViolation(field=name, description=description)
                    for name, description in violations
                ]
            )
            details = status_pb2.Status(code=grpc.StatusCode.INVALID_ARGUMENT.value[0], message="invalid account")
            details.details.add().Pack(bad_request)
            details.details.append(any_pb2.Any(type_url="type.googleapis.com/example.Unknown", value=b"\x08\x01"))
            context.set_trailing_metadata((("grpc-status-details-bin", details.SerializeToString()),))
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, "invalid account")
        return account_class(display_name=request.display_name).SerializeToString()

    def reset(payload: bytes, context: grpc.ServicerContext) -> bytes:
        record("Reset", context, payload)
        operation = operation_class()
        operation.response.Pack(user_class(name=f"u{request_class.FromString(payload).user_id}"))
        return operation.SerializeToString()

    recorder = RecordReflection()
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=4), interceptors=[recorder])
    server.add_generic_rpc_handlers(
        (
            grpc.method_handlers_generic_handler(
                "pkg.Users",
                {"GetUser": grpc.unary_unary_rpc_method_handler(get_user, request_deserializer=parse_request)},
            ),
            grpc.method_handlers_generic_handler("pkg.Admin", {"Reset": grpc.unary_unary_rpc_method_handler(reset)}),
            grpc.method_handlers_generic_handler(
                "pkg.Accounts", {"CreateAccount": grpc.unary_unary_rpc_method_handler(create_account)}
            ),
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
                    payload=payload,
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


def library() -> GrpcApp:
    """`library.Library` with two bugs that only real values from earlier responses reach.

    `CreateBook` answers `books/<random hex>`, or INVALID_ARGUMENT without a title; `GetBook` fails on a created name and answers NOT_FOUND otherwise.
    `ListBooks` pages through two books; a page token it issued fails, any other non-empty token is INVALID_ARGUMENT.
    """
    database = descriptor_database.DescriptorDatabase()
    for file in descriptor_pb2.FileDescriptorSet.FromString(library_descriptor_set()).file:
        database.Add(file)
    pool = descriptor_pool.DescriptorPool(database)

    def message_class(name: str) -> type:
        return message_factory.GetMessageClass(pool.FindMessageTypeByName(f"library.{name}"))

    book_class = message_class("Book")
    created: set[str] = set()
    issued: set[str] = set()

    def create_book(payload: bytes, context: grpc.ServicerContext) -> bytes:
        title = message_class("CreateBookRequest").FromString(payload).title
        if not title:
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, "title is required")
        name = f"books/{secrets.token_hex(8)}"
        created.add(name)
        return book_class(name=name, title=title).SerializeToString()

    def get_book(payload: bytes, context: grpc.ServicerContext) -> bytes:
        name = message_class("GetBookRequest").FromString(payload).name
        if name in created:
            context.abort(grpc.StatusCode.INTERNAL, "cannot load a created book")
        context.abort(grpc.StatusCode.NOT_FOUND, "no such book")

    def list_books(payload: bytes, context: grpc.ServicerContext) -> bytes:
        token = message_class("ListBooksRequest").FromString(payload).page_token
        if token in issued:
            context.abort(grpc.StatusCode.INTERNAL, "cannot read the second page")
        if token:
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, "malformed page token")
        next_token = secrets.token_hex(8)
        issued.add(next_token)
        return message_class("ListBooksResponse")(
            books=[book_class(name="books/first")], next_page_token=next_token
        ).SerializeToString()

    server = grpc.server(futures.ThreadPoolExecutor(max_workers=4))
    handlers = {"CreateBook": create_book, "GetBook": get_book, "ListBooks": list_books}
    server.add_generic_rpc_handlers(
        (
            grpc.method_handlers_generic_handler(
                "library.Library",
                {name: grpc.unary_unary_rpc_method_handler(handler) for name, handler in handlers.items()},
            ),
        )
    )
    serve_reflection(server, "v1alpha", pool, services=("library.Library",))
    port = server.add_insecure_port("127.0.0.1:0")
    server.start()
    return GrpcApp(server=server, port=port, calls=[], reflection_calls=[], descriptor_set=library_descriptor_set())
