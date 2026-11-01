from __future__ import annotations

import importlib
import time
from collections.abc import Sequence
from os import PathLike
from pathlib import Path
from typing import TYPE_CHECKING, cast

from schemathesis.config import SchemathesisConfig
from schemathesis.core.errors import LoaderError, LoaderErrorKind
from schemathesis.core.loaders import WAIT_FOR_SCHEMA_INTERVAL
from schemathesis.core.timing import Instant
from schemathesis.core.transport import DEFAULT_RESPONSE_TIMEOUT
from schemathesis.hooks import load_with_hooks

if TYPE_CHECKING:
    import grpc
    from google.protobuf import descriptor_pb2

    from schemathesis.specs.grpc.schemas import GrpcSchema

BUILD_HINT = "Build one with `buf build -o api.binpb` or `protoc --include_imports --descriptor_set_out=api.binpb`"
EXTRA_HINT = "Loading gRPC schemas requires the `grpc` extra: `pip install 'schemathesis[grpc]'`"
# Reflection lists every service the server registers, these included.
INFRASTRUCTURE_SERVICES = frozenset(
    {
        "grpc.reflection.v1.ServerReflection",
        "grpc.reflection.v1alpha.ServerReflection",
        "grpc.health.v1.Health",
        "grpc.channelz.v1.Channelz",
        "envoy.service.status.v3.ClientStatusDiscoveryService",
    }
)


def require_extra() -> None:
    """Raise `ImportError` naming the `grpc` extra if any of its packages is missing."""
    try:
        for package in ("google.protobuf", "google.rpc", "grpc", "grpc_reflection"):
            importlib.import_module(package)
    except ImportError as exc:
        raise ImportError(EXTRA_HINT) from exc


def from_url(
    url: str,
    *,
    config: SchemathesisConfig | None = None,
    wait_for_schema: float | None = None,
    headers: dict[str, str] | None = None,
    verify: bool | str = True,
    cert: str | tuple[str, str] | None = None,
    auth: tuple[str, str] | None = None,
    timeout: float | None = None,
    proxies: dict[str, str] | None = None,
) -> GrpcSchema:
    """Load a gRPC schema from a server through reflection.

    Args:
        url: Server address, `grpc://host:port` or `grpcs://host:port`
        config: Custom configuration. If `None`, uses auto-discovered config
        wait_for_schema: Seconds to wait for the server to become available
        headers: Metadata to send with reflection calls
        verify: Whether to verify the server certificate, or a CA bundle path
        cert: Client certificate, or a `(certificate, key)` pair
        auth: Basic authentication credentials
        timeout: Seconds to wait for each reflection call
        proxies: Not supported; any value raises `IncorrectUsage`

    Example:
        ```python
        import schemathesis

        schema = schemathesis.grpc.from_url("grpc://localhost:50051")
        ```

    """
    require_extra()
    import grpc
    from google.protobuf import descriptor_pb2

    # grpcio-reflection ships no usable type information.
    from grpc_reflection.v1alpha import reflection_pb2  # type: ignore[import-untyped]

    from schemathesis.specs.grpc.schemas import filter_metadata, metadata_pairs
    from schemathesis.transport.grpc import CHANNEL_OPTIONS, create_channel, prepare_target

    request_headers = filter_metadata(headers or {})
    address, tls = prepare_target(url, verify, request_headers, uses_proxy=bool(proxies), auth=auth)
    metadata = metadata_pairs(request_headers)
    options: tuple[tuple[str, int], ...] = CHANNEL_OPTIONS
    if wait_for_schema is not None:
        # Reconnect as often as attempts repeat, so a server that starts late in the wait is seen before it ends.
        backoff_ms = int(WAIT_FOR_SCHEMA_INTERVAL * 1000)
        options = (
            *CHANNEL_OPTIONS,
            ("grpc.initial_reconnect_backoff_ms", backoff_ms),
            ("grpc.max_reconnect_backoff_ms", backoff_ms),
        )
    with create_channel(address, tls, verify, cert, options=options) as channel:
        # Both versions exchange the same messages; some servers serve only one of them.
        v1, v1alpha = (
            channel.stream_stream(
                f"/grpc.reflection.{version}.ServerReflection/ServerReflectionInfo",
                request_serializer=reflection_pb2.ServerReflectionRequest.SerializeToString,
                response_deserializer=reflection_pb2.ServerReflectionResponse.FromString,
            )
            for version in ("v1", "v1alpha")
        )

        def ask(call: grpc.StreamStreamMultiCallable, **kwargs: str) -> reflection_pb2.ServerReflectionResponse:
            started_at = Instant()
            while True:
                # A server may accept connections before it serves, so a call may last until the wait ends.
                remaining = max(0, (wait_for_schema or 0) - started_at.elapsed)
                deadline = (timeout or DEFAULT_RESPONSE_TIMEOUT) + remaining
                try:
                    return next(
                        call(
                            iter([reflection_pb2.ServerReflectionRequest(**kwargs)]),
                            metadata=metadata,
                            timeout=deadline,
                        )
                    )
                except grpc.RpcError as exc:
                    error = _reflection_error(cast(grpc.Call, exc), url, address, tls, deadline)
                    # Waiting covers a server that is not up yet; a TLS failure would repeat on every attempt.
                    if (
                        wait_for_schema is None
                        or error.kind != LoaderErrorKind.CONNECTION_OTHER
                        or started_at.elapsed >= wait_for_schema
                    ):
                        raise error from None
                    time.sleep(WAIT_FOR_SCHEMA_INTERVAL)

        try:
            listing = ask(v1, list_services="")
            call = v1
        except LoaderError as exc:
            if exc.kind != LoaderErrorKind.GRPC_REFLECTION_UNAVAILABLE:
                raise
            listing = ask(v1alpha, list_services="")
            call = v1alpha
        names = [
            service.name
            for service in listing.list_services_response.service
            if service.name not in INFRASTRUCTURE_SERVICES
        ]
        if not names:
            raise LoaderError(
                LoaderErrorKind.GRPC_REFLECTION_UNAVAILABLE,
                "The server lists no services besides reflection and health. Build a descriptor set with "
                f"`buf build -o api.binpb` and run `schemathesis run api.binpb --url {url}`",
            )
        files: dict[str, descriptor_pb2.FileDescriptorProto] = {}
        undescribed: list[LoaderError] = []

        def describe(subject: str, **kwargs: str) -> LoaderError | None:
            response = ask(call, **kwargs)
            if response.HasField("error_response"):
                return LoaderError(
                    LoaderErrorKind.GRPC_REFLECTION_UNAVAILABLE,
                    f"The server cannot describe `{subject}` through reflection: "
                    f"{response.error_response.error_message}. Build a descriptor set with `buf build -o api.binpb` "
                    f"and run `schemathesis run api.binpb --url {url}`",
                )
            for data in response.file_descriptor_response.file_descriptor_proto:
                file = descriptor_pb2.FileDescriptorProto.FromString(data)
                files.setdefault(file.name, file)
            return None

        for name in names:
            if (error := describe(name, file_containing_symbol=name)) is not None:
                raise error
        # Some servers return only the requested file, without its imports.
        requested: set[str] = set()
        while missing := next(
            (
                dependency
                for file in files.values()
                for dependency in file.dependency
                if dependency not in files and dependency not in requested
            ),
            None,
        ):
            requested.add(missing)
            if (error := describe(missing, file_by_filename=missing)) is not None:
                # An import used only for custom options loads as an empty file; a type needed from it fails below.
                files[missing] = descriptor_pb2.FileDescriptorProto(name=missing)
                undescribed.append(error)
    data = descriptor_pb2.FileDescriptorSet(file=files.values()).SerializeToString()
    try:
        loaded = _from_descriptor_set(data, config=config, service_names=names)
    except LoaderError:
        if undescribed:
            raise undescribed[0] from None
        raise
    loaded.location = url
    return loaded


def _reflection_error(error: grpc.Call, url: str, address: str, tls: bool, timeout: float) -> LoaderError:
    import grpc

    from schemathesis.transport.grpc import TLS_EXPECTED, TLS_FAILED, TLS_FAILURE_MARKERS, accepts_tls

    code = error.code()
    if code == grpc.StatusCode.UNIMPLEMENTED:
        return LoaderError(
            LoaderErrorKind.GRPC_REFLECTION_UNAVAILABLE,
            "The server does not support reflection. Build a descriptor set with `buf build -o api.binpb` "
            f"and run `schemathesis run api.binpb --url {url}`",
        )
    if code == grpc.StatusCode.UNAVAILABLE:
        details = error.details()
        # Waiting does not fix a rejected certificate or a TLS mismatch, so these get no retry advice.
        if any(marker in details for marker in TLS_FAILURE_MARKERS):
            return LoaderError(LoaderErrorKind.INVALID_CERTIFICATE, TLS_FAILED.format(address=address, details=details))
        message = f"Failed to connect to {address}: {details}"
        if not tls and accepts_tls(address):
            return LoaderError(LoaderErrorKind.NETWORK_OTHER, f"{message}. {TLS_EXPECTED.format(address=address)}")
        return LoaderError(LoaderErrorKind.CONNECTION_OTHER, message)
    if code in (grpc.StatusCode.UNAUTHENTICATED, grpc.StatusCode.PERMISSION_DENIED):
        return LoaderError(LoaderErrorKind.HTTP_FORBIDDEN, f"Reflection failed with {code.name}: {error.details()}")
    if code == grpc.StatusCode.DEADLINE_EXCEEDED:
        return LoaderError(LoaderErrorKind.NETWORK_OTHER, f"Read timed out after {timeout:g} seconds")
    return LoaderError(LoaderErrorKind.UNCLASSIFIED, f"Reflection failed with {code.name}: {error.details()}")


def from_path(path: PathLike | str, *, config: SchemathesisConfig | None = None) -> GrpcSchema:
    """Load a gRPC schema from a serialized `FileDescriptorSet`.

    Args:
        path: File built with `buf build -o api.binpb` or `protoc --include_imports --descriptor_set_out=api.binpb`
        config: Custom configuration. If `None`, uses auto-discovered config

    Example:
        ```python
        import schemathesis

        schema = schemathesis.grpc.from_path("api.binpb")
        ```

    """
    try:
        loaded = _from_descriptor_set(Path(path).read_bytes(), config=config)
    except ImportError as exc:
        raise ImportError(EXTRA_HINT) from exc
    loaded.location = Path(path).absolute().as_uri()
    return loaded


def _from_descriptor_set(
    data: bytes, *, config: SchemathesisConfig | None, service_names: Sequence[str] | None = None
) -> GrpcSchema:
    try:
        from google.protobuf import descriptor_pb2, descriptor_pool, json_format
        from google.protobuf.message import DecodeError
    except ImportError as exc:
        raise ImportError(EXTRA_HINT) from exc

    from schemathesis.specs.grpc.schemas import GrpcSchema, filter_metadata, metadata_pairs, parse_target

    try:
        descriptor_set = descriptor_pb2.FileDescriptorSet.FromString(data)
    except DecodeError as exc:
        raise LoaderError(
            LoaderErrorKind.GRPC_INVALID_DESCRIPTOR_SET,
            f"The file is not a serialized `FileDescriptorSet`. {BUILD_HINT}",
        ) from exc
    if not descriptor_set.file:
        raise LoaderError(
            LoaderErrorKind.GRPC_INVALID_DESCRIPTOR_SET, f"The descriptor set contains no files. {BUILD_HINT}"
        )
    files = {file.name: file for file in descriptor_set.file}
    for file in descriptor_set.file:
        for dependency in file.dependency:
            if dependency not in files:
                raise LoaderError(
                    LoaderErrorKind.GRPC_INVALID_DESCRIPTOR_SET,
                    f"The descriptor set is incomplete: `{file.name}` imports `{dependency}`, which it does not "
                    f"contain. {BUILD_HINT}",
                )
    pool = descriptor_pool.DescriptorPool()
    added: set[str] = set()
    pending: set[str] = set()

    # The pool takes a file only after everything it imports.
    def add(file: descriptor_pb2.FileDescriptorProto) -> None:
        if file.name in pending:
            raise _does_not_load(f"`{file.name}` imports itself through its imports")
        if file.name not in added:
            pending.add(file.name)
            for dependency in file.dependency:
                add(files[dependency])
            pending.remove(file.name)
            added.add(file.name)
            pool.Add(file)

    # Files pulled in for their message types carry services of other APIs.
    imported = {dependency for file in descriptor_set.file for dependency in file.dependency}
    # Imported files of a top-level package belong to the same API. Files without a package all share the empty one.
    packages = {file.package for file in descriptor_set.file if file.name not in imported} - {""}
    try:
        for file in descriptor_set.file:
            add(file)
        # The pure-Python runtime resolves names only on lookup.
        if service_names is not None:
            services = {name: pool.FindServiceByName(name) for name in service_names}
        else:
            services = {
                service.full_name: service
                for file in descriptor_set.file
                for service in pool.FindFileByName(file.name).services_by_name.values()
                if (file.name not in imported or file.package in packages)
                and service.full_name not in INFRASTRUCTURE_SERVICES
            }
    except TypeError as exc:
        raise _does_not_load(str(exc).removeprefix("Couldn't build proto file into descriptor pool: ")) from exc
    except KeyError as exc:
        raise _does_not_load(f"couldn't resolve name {exc}") from exc
    raw_schema = json_format.MessageToDict(descriptor_set)
    schema = load_with_hooks(
        raw_schema,
        config,
        lambda project_config: GrpcSchema(raw_schema, config=project_config, pool=pool, services=services),
    )
    # Settings every call would fail on are refused once, before any test runs.
    metadata_pairs(filter_metadata(schema.config.headers_for()))
    if schema.config.base_url is not None:
        settings = schema.config.request_settings_for()
        parse_target(schema.config.base_url, settings.verify, uses_proxy=bool(settings.proxy))
    return schema


def _does_not_load(detail: str) -> LoaderError:
    return LoaderError(LoaderErrorKind.GRPC_INVALID_DESCRIPTOR_SET, f"The descriptor set does not load: {detail}")
