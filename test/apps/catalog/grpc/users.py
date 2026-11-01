from __future__ import annotations

import os
import secrets
import socket
import subprocess
import sys
import time
from collections.abc import Callable
from concurrent import futures
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

import grpc
import trustme
from google.protobuf import any_pb2, descriptor_database, descriptor_pb2, descriptor_pool, message_factory
from google.rpc import error_details_pb2, status_pb2
from grpc_reflection.v1alpha import reflection, reflection_pb2

from test.apps.catalog.grpc.descriptors import (
    accounts_descriptor_set,
    all_types_descriptor_set,
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
    # Bound without listening once the server stops, so connections are refused and no other server takes the port.
    held_port: socket.socket | None = None

    def stop(self) -> None:
        self.server.stop(None).wait()
        self.held_port = socket.socket()
        # Connections the server just closed may still occupy the port.
        self.held_port.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.held_port.bind(("127.0.0.1", urlsplit(self.target).port))

    def close(self) -> None:
        self.server.stop(None)
        if self.held_port is not None:
            self.held_port.close()


@dataclass(slots=True)
class CrashingGrpcServer:
    target: str
    descriptor_set_path: Path
    process: subprocess.Popen[str]

    def wait_for_call(self, user_id: int) -> None:
        assert self.process.stdout is not None
        for line in self.process.stdout:
            if line.strip() == str(user_id):
                return
        raise AssertionError(f"The server exited before receiving {user_id}")

    def kill(self) -> None:
        self.process.kill()
        # Reads what is left of the output and closes the pipe.
        self.process.communicate()


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
    def __init__(
        self,
        names: tuple[str, ...],
        pool: descriptor_pool.DescriptorPool,
        *,
        imports: bool,
        unserved_files: tuple[str, ...],
        emptied_files: tuple[str, ...],
    ) -> None:
        super().__init__(names, pool=pool)
        self.imports = imports
        self.unserved_files = unserved_files
        self.emptied_files = emptied_files

    def _file_by_filename(self, request, filename):
        if filename in self.unserved_files:
            return reflection_pb2.ServerReflectionResponse(
                error_response=reflection_pb2.ErrorResponse(
                    error_code=grpc.StatusCode.NOT_FOUND.value[0], error_message=grpc.StatusCode.NOT_FOUND.value[1]
                ),
                original_request=request,
            )
        return super()._file_by_filename(request, filename)

    def _file_containing_symbol(self, request, fully_qualified_name):
        response = super()._file_containing_symbol(request, fully_qualified_name)
        files = response.file_descriptor_response.file_descriptor_proto
        # The containing file comes first, its imports after it.
        if not self.imports:
            del files[1:]
        for index in reversed(range(len(files))):
            name = descriptor_pb2.FileDescriptorProto.FromString(files[index]).name
            if name in self.unserved_files:
                del files[index]
            elif name in self.emptied_files:
                files[index] = descriptor_pb2.FileDescriptorProto(name=name).SerializeToString()
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
    unserved_files: tuple[str, ...] = (),
    emptied_files: tuple[str, ...] = (),
) -> None:
    names = (*services, f"grpc.reflection.{version}.ServerReflection", *undescribed_services)
    # Both versions exchange the same messages.
    servicer = ReflectionServicer(
        names, pool=pool, imports=imports, unserved_files=unserved_files, emptied_files=emptied_files
    )
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
    unserved_files: tuple[str, ...] = (),
    emptied_files: tuple[str, ...] = (),
    annotated: bool = False,
    start: bool = True,
    port: int = 0,
    on_get_user: Callable[[int, grpc.Server], object] | None = None,
) -> GrpcApp:
    """`pkg.Users/GetUser` answers `u<id>`; a negative id fails, 13 is slow, 503 is a server-sent UNAVAILABLE.

    `on_get_user` sees each id first. 404 is NOT_FOUND, 499 is CANCELLED, 504 is DEADLINE_EXCEEDED sent at once,
    17 answers with bytes that do not decode, 413 answers with a name of 5 MiB, and 422 fails in the request
    deserializer. `pkg.Admin/Reset` answers with `u<id>` packed into `response`, or with
    a `google.rpc.ErrorInfo` detail, a type clients do not know, in `error` for a negative id. Reflection refuses calls
    that carry `x-deny: 1`.
    Reflection also lists `undescribed_services`, which it answers with a NOT_FOUND error response.
    Without `reflection_imports`, a symbol lookup returns only its file, and imports come by file name.
    Reflection cannot describe `unserved_files` and leaves them out of symbol lookups; it describes `emptied_files`
    in symbol lookups as files that define nothing.
    With `annotated`, `GetUser` sets a custom option from `annotations.proto`, imported for that option alone.
    `pkg.Accounts/CreateAccount` (in `accounts_descriptor_set()`, not reflected) rejects an empty `display_name`,
    an empty `profile.contact_email` and the reserved name `admin` with INVALID_ARGUMENT, listing each field in
    `google.rpc.BadRequest` details next to a detail of a type clients do not know.
    """
    descriptor_set = users_descriptor_set(annotated=annotated)
    database = descriptor_database.DescriptorDatabase()
    for file in descriptor_pb2.FileDescriptorSet.FromString(descriptor_set).file:
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
        if on_get_user is not None:
            on_get_user(user_id, server)
        if user_id < 0:
            context.abort(grpc.StatusCode.INTERNAL, "negative id")
        if user_id == 400:
            context.set_trailing_metadata((("x-details", "x" * 65536),))
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, "invalid")
        if user_id == 503:
            context.abort(grpc.StatusCode.UNAVAILABLE, "")
        if user_id == 504:
            context.abort(grpc.StatusCode.DEADLINE_EXCEEDED, "upstream timed out")
        if user_id == 404:
            context.abort(grpc.StatusCode.NOT_FOUND, "no such user")
        if user_id == 499:
            context.abort(grpc.StatusCode.CANCELLED, "")
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
        if user_id == 413:
            return user_class(name="x" * 5 * 1024 * 1024).SerializeToString()
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
        user_id = request_class.FromString(payload).user_id
        if user_id < 0:
            operation.error.code = grpc.StatusCode.NOT_FOUND.value[0]
            operation.error.details.add().Pack(error_details_pb2.ErrorInfo(reason="NO_USER"))
        else:
            operation.response.Pack(user_class(name=f"u{user_id}"))
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
            unserved_files=unserved_files,
            emptied_files=emptied_files,
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
        port = server.add_secure_port(f"127.0.0.1:{port}", credentials)
    else:
        port = server.add_insecure_port(f"127.0.0.1:{port}")
    if start:
        server.start()
    return GrpcApp(
        server=server,
        port=port,
        calls=calls,
        reflection_calls=recorder.calls,
        tls=files,
        descriptor_set=descriptor_set,
    )


def serve_crashing_users(how: str) -> None:
    """Serve `users` until a call with id 500 exits the process or stops the server; prints the port, then each id."""

    def on_get_user(user_id: int, server: grpc.Server) -> None:
        print(user_id, flush=True)  # noqa: T201
        if user_id == 500:
            if how == "exit":
                os._exit(1)
            server.stop(None)

    app = users(on_get_user=on_get_user)
    print(app.port, flush=True)  # noqa: T201
    app.server.wait_for_termination()


def crashing_users(how: str, directory: Path) -> CrashingGrpcServer:
    """`users` in a separate process, so a call can bring the whole server down."""
    process = subprocess.Popen(
        [
            sys.executable,
            "-c",
            f"from test.apps.catalog.grpc.users import serve_crashing_users; serve_crashing_users({how!r})",
        ],
        cwd=Path(__file__).parents[4],
        stdout=subprocess.PIPE,
        text=True,
    )
    assert process.stdout is not None
    port = int(process.stdout.readline())
    return CrashingGrpcServer(
        target=f"grpc://127.0.0.1:{port}",
        descriptor_set_path=write_descriptor_set(directory, users_descriptor_set()),
        process=process,
    )


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


def envelope() -> GrpcApp:
    """`types.Types/Envelope` reads `first.extra` without a presence check, as a careless handler would."""
    pool = descriptor_pool.DescriptorPool()
    for file in descriptor_pb2.FileDescriptorSet.FromString(all_types_descriptor_set()).file:
        pool.Add(file)
    envelope_class = message_factory.GetMessageClass(pool.FindMessageTypeByName("types.Envelope"))
    empty_class = message_factory.GetMessageClass(pool.FindMessageTypeByName("google.protobuf.Empty"))

    def handle(payload: bytes, context: grpc.ServicerContext) -> bytes:
        request = envelope_class.FromString(payload)
        if request.HasField("first") and not request.first.HasField("extra"):
            context.abort(grpc.StatusCode.INTERNAL, "nil extra")
        return empty_class().SerializeToString()

    server = grpc.server(futures.ThreadPoolExecutor(max_workers=4))
    server.add_generic_rpc_handlers(
        (
            grpc.method_handlers_generic_handler(
                "types.Types", {"Envelope": grpc.unary_unary_rpc_method_handler(handle)}
            ),
        )
    )
    port = server.add_insecure_port("127.0.0.1:0")
    server.start()
    return GrpcApp(server=server, port=port, calls=[], reflection_calls=[], descriptor_set=all_types_descriptor_set())
