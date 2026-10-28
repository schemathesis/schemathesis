from __future__ import annotations

from os import PathLike
from pathlib import Path
from typing import TYPE_CHECKING

from schemathesis.config import SchemathesisConfig
from schemathesis.core.errors import LoaderError, LoaderErrorKind
from schemathesis.hooks import (
    GLOBAL_HOOK_DISPATCHER,
    HookContext,
    dispatch_after_load_schema,
    dispatch_before_load_schema,
)

if TYPE_CHECKING:
    from google.protobuf import descriptor_pb2

    from schemathesis.specs.grpc.schemas import GrpcSchema

BUILD_HINT = "Build one with `buf build -o api.binpb` or `protoc --include_imports --descriptor_set_out=api.binpb`"


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
    loaded = _from_descriptor_set(Path(path).read_bytes(), config=config)
    loaded.location = Path(path).absolute().as_uri()
    return loaded


def _from_descriptor_set(data: bytes, *, config: SchemathesisConfig | None) -> GrpcSchema:
    try:
        from google.protobuf import descriptor_pb2, descriptor_pool, json_format
        from google.protobuf.message import DecodeError
    except ImportError as exc:
        raise ImportError("Loading gRPC schemas requires the `grpc` extra: `pip install schemathesis[grpc]`") from exc

    from schemathesis.specs.grpc.schemas import GrpcSchema

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
    try:
        for file in descriptor_set.file:
            add(file)
        # The pure-Python runtime resolves names only on lookup.
        services = {
            service.full_name: service
            for file in descriptor_set.file
            for service in pool.FindFileByName(file.name).services_by_name.values()
            if file.name not in imported
        }
    except TypeError as exc:
        raise _does_not_load(str(exc).removeprefix("Couldn't build proto file into descriptor pool: ")) from exc
    except KeyError as exc:
        raise _does_not_load(f"couldn't resolve name {exc}") from exc
    raw_schema = json_format.MessageToDict(descriptor_set)
    hook_context = HookContext()
    dispatch_before_load_schema(GLOBAL_HOOK_DISPATCHER, context=hook_context, raw_schema=raw_schema)
    if config is None:
        config = SchemathesisConfig.discover()
    project_config = config.projects.get(raw_schema)
    instance = GrpcSchema(raw_schema, config=project_config, pool=pool, services=services)
    instance.filter_set = project_config.operations.filter_set_with(include=instance.filter_set)
    dispatch_after_load_schema(GLOBAL_HOOK_DISPATCHER, context=hook_context, schema=instance)
    return instance


def _does_not_load(detail: str) -> LoaderError:
    return LoaderError(LoaderErrorKind.GRPC_INVALID_DESCRIPTOR_SET, f"The descriptor set does not load: {detail}")
