from pathlib import Path

from google.protobuf import (
    any_pb2,
    descriptor_pb2,
    descriptor_pool,
    duration_pb2,
    empty_pb2,
    field_mask_pb2,
    json_format,
    message,
    message_factory,
    struct_pb2,
    timestamp_pb2,
    wrappers_pb2,
)

import schemathesis
from schemathesis.config import SchemathesisConfig

FieldDescriptorProto = descriptor_pb2.FieldDescriptorProto


def write_descriptor_set(directory: Path, data: bytes) -> Path:
    path = directory / "api.binpb"
    path.write_bytes(data)
    return path


def load_descriptor_set(directory: Path, data: bytes, **config):
    """Schema from `data` written as a descriptor set; without `config`, the configuration is auto-discovered."""
    return schemathesis.grpc.from_path(
        write_descriptor_set(directory, data), config=SchemathesisConfig.from_dict(config) if config else None
    )


def load(server, **config):
    """Schema from the descriptor set `server` writes, aimed at that server."""
    config = SchemathesisConfig.from_dict({"base-url": server.target, **config})
    return schemathesis.grpc.from_path(server.descriptor_set_path, config=config)


def users_descriptor_set(*, annotated: bool = False) -> bytes:
    """With `annotated`, `GetUser` sets a custom option from `annotations.proto`, imported for that option alone."""
    timestamp = descriptor_pb2.FileDescriptorProto()
    timestamp_pb2.DESCRIPTOR.CopyToProto(timestamp)
    any_file = descriptor_pb2.FileDescriptorProto()
    any_pb2.DESCRIPTOR.CopyToProto(any_file)
    users = descriptor_pb2.FileDescriptorProto(
        name="users.proto",
        package="pkg",
        syntax="proto3",
        dependency=["google/protobuf/timestamp.proto", "google/longrunning/operations.proto"],
    )
    request = users.message_type.add(name="GetUserRequest")
    request.field.add(
        name="user_id",
        json_name="userId",
        number=1,
        type=FieldDescriptorProto.TYPE_INT32,
        label=FieldDescriptorProto.LABEL_OPTIONAL,
    )
    user = users.message_type.add(name="User")
    user.field.add(
        name="name",
        json_name="name",
        number=1,
        type=FieldDescriptorProto.TYPE_STRING,
        label=FieldDescriptorProto.LABEL_OPTIONAL,
    )
    user.field.add(
        name="created_at",
        json_name="createdAt",
        number=2,
        type=FieldDescriptorProto.TYPE_MESSAGE,
        type_name=".google.protobuf.Timestamp",
        label=FieldDescriptorProto.LABEL_OPTIONAL,
    )
    service = users.service.add(name="Users")
    service.method.add(
        name="GetUser",
        input_type=".pkg.GetUserRequest",
        output_type=".pkg.User",
        options=descriptor_pb2.MethodOptions(deprecated=True),
    )
    service.method.add(
        name="WatchUsers", input_type=".pkg.GetUserRequest", output_type=".pkg.User", server_streaming=True
    )
    users.service.add(name="Admin").method.add(
        name="Reset", input_type=".pkg.GetUserRequest", output_type=".google.longrunning.Operation"
    )
    # Imported for its message type; its service belongs to another API.
    operations = descriptor_pb2.FileDescriptorProto(
        name="google/longrunning/operations.proto",
        package="google.longrunning",
        syntax="proto3",
        dependency=["google/protobuf/any.proto"],
    )
    _field(
        operations.message_type.add(name="Operation"),
        "response",
        5,
        FieldDescriptorProto.TYPE_MESSAGE,
        type_name=".google.protobuf.Any",
    )
    _field(
        operations.message_type[0],
        "error",
        4,
        FieldDescriptorProto.TYPE_MESSAGE,
        type_name=".google.longrunning.Status",
    )
    status = operations.message_type.add(name="Status")
    _field(status, "code", 1, FieldDescriptorProto.TYPE_INT32)
    _field(status, "message", 2, FieldDescriptorProto.TYPE_STRING)
    _field(
        status,
        "details",
        3,
        FieldDescriptorProto.TYPE_MESSAGE,
        type_name=".google.protobuf.Any",
        label=FieldDescriptorProto.LABEL_REPEATED,
    )
    operations.message_type.add(name="GetOperationRequest")
    operations.service.add(name="Operations").method.add(
        name="GetOperation",
        input_type=".google.longrunning.GetOperationRequest",
        output_type=".google.longrunning.Operation",
    )
    files = [timestamp, users, operations, any_file]
    if annotated:
        descriptor = descriptor_pb2.FileDescriptorProto()
        descriptor_pb2.DESCRIPTOR.CopyToProto(descriptor)
        annotations = descriptor_pb2.FileDescriptorProto(
            name="annotations.proto", package="annotations", syntax="proto3", dependency=[descriptor.name]
        )
        _field(annotations.message_type.add(name="Operation"), "summary", 1, FieldDescriptorProto.TYPE_STRING)
        _extension(annotations, "operation", 50000, ".google.protobuf.MethodOptions", ".annotations.Operation")
        pool = descriptor_pool.DescriptorPool()
        for file in (descriptor, annotations):
            pool.Add(file)
        users.dependency.append(annotations.name)
        _set_extension(
            pool,
            service.method[0].options,
            "google.protobuf.MethodOptions",
            "annotations.operation",
            {"summary": "Get a user"},
        )
        files.extend([descriptor, annotations])
    return descriptor_pb2.FileDescriptorSet(file=files).SerializeToString()


def library_descriptor_set() -> bytes:
    library = descriptor_pb2.FileDescriptorProto(name="library.proto", package="library", syntax="proto3")
    book = library.message_type.add(name="Book")
    _field(book, "name", 1, FieldDescriptorProto.TYPE_STRING)
    _field(book, "title", 2, FieldDescriptorProto.TYPE_STRING)
    _field(library.message_type.add(name="CreateBookRequest"), "title", 1, FieldDescriptorProto.TYPE_STRING)
    _field(library.message_type.add(name="GetBookRequest"), "name", 1, FieldDescriptorProto.TYPE_STRING)
    request = library.message_type.add(name="ListBooksRequest")
    _field(request, "page_size", 1, FieldDescriptorProto.TYPE_INT32)
    _field(request, "page_token", 2, FieldDescriptorProto.TYPE_STRING)
    response = library.message_type.add(name="ListBooksResponse")
    _field(
        response,
        "books",
        1,
        FieldDescriptorProto.TYPE_MESSAGE,
        type_name=".library.Book",
        label=FieldDescriptorProto.LABEL_REPEATED,
    )
    _field(response, "next_page_token", 2, FieldDescriptorProto.TYPE_STRING)
    service = library.service.add(name="Library")
    service.method.add(name="CreateBook", input_type=".library.CreateBookRequest", output_type=".library.Book")
    service.method.add(name="GetBook", input_type=".library.GetBookRequest", output_type=".library.Book")
    service.method.add(
        name="ListBooks", input_type=".library.ListBooksRequest", output_type=".library.ListBooksResponse"
    )
    return descriptor_pb2.FileDescriptorSet(file=[library]).SerializeToString()


def side_effects_descriptor_set() -> bytes:
    empty = descriptor_pb2.FileDescriptorProto()
    empty_pb2.DESCRIPTOR.CopyToProto(empty)
    store = descriptor_pb2.FileDescriptorProto(
        name="store.proto", package="store", syntax="proto3", dependency=[empty.name]
    )
    service = store.service.add(name="Store")
    levels = {
        "DeleteItem": descriptor_pb2.MethodOptions.IDEMPOTENCY_UNKNOWN,
        "SetConfig": descriptor_pb2.MethodOptions.IDEMPOTENT,
        "Alarm": descriptor_pb2.MethodOptions.IDEMPOTENCY_UNKNOWN,
        "Listen": descriptor_pb2.MethodOptions.IDEMPOTENCY_UNKNOWN,
        "ListItems": descriptor_pb2.MethodOptions.IDEMPOTENCY_UNKNOWN,
        "Status": descriptor_pb2.MethodOptions.NO_SIDE_EFFECTS,
    }
    for name, level in levels.items():
        service.method.add(
            name=name,
            input_type=".google.protobuf.Empty",
            output_type=".google.protobuf.Empty",
            options=descriptor_pb2.MethodOptions(idempotency_level=level),
        )
    return descriptor_pb2.FileDescriptorSet(file=[empty, store]).SerializeToString()


def accounts_descriptor_set() -> bytes:
    accounts = descriptor_pb2.FileDescriptorProto(name="accounts.proto", package="pkg", syntax="proto3")
    request = accounts.message_type.add(name="CreateAccountRequest")
    _field(request, "display_name", 1, FieldDescriptorProto.TYPE_STRING)
    _field(request, "profile", 2, FieldDescriptorProto.TYPE_MESSAGE, type_name=".pkg.Profile")
    _field(accounts.message_type.add(name="Profile"), "contact_email", 1, FieldDescriptorProto.TYPE_STRING)
    _field(accounts.message_type.add(name="Account"), "display_name", 1, FieldDescriptorProto.TYPE_STRING)
    accounts.service.add(name="Accounts").method.add(
        name="CreateAccount", input_type=".pkg.CreateAccountRequest", output_type=".pkg.Account"
    )
    return descriptor_pb2.FileDescriptorSet(file=[accounts]).SerializeToString()


def all_types_descriptor_set() -> bytes:
    well_known = []
    for module in (any_pb2, duration_pb2, empty_pb2, field_mask_pb2, struct_pb2, timestamp_pb2, wrappers_pb2):
        file = descriptor_pb2.FileDescriptorProto()
        module.DESCRIPTOR.CopyToProto(file)
        well_known.append(file)
    types = descriptor_pb2.FileDescriptorProto(
        name="types.proto", package="types", syntax="proto3", dependency=[file.name for file in well_known]
    )
    types.enum_type.add(name="Color").value.add(name="COLOR_UNSPECIFIED", number=0)
    types.enum_type[0].value.add(name="RED", number=1)

    scalars = types.message_type.add(name="Scalars")
    for number, (name, kind) in enumerate(SCALARS.items(), start=1):
        _field(scalars, name, number, kind)
    _field(scalars, "color", 16, FieldDescriptorProto.TYPE_ENUM, type_name=".types.Color")
    _field(scalars, "tags", 17, FieldDescriptorProto.TYPE_INT32, label=FieldDescriptorProto.LABEL_REPEATED)

    maps = types.message_type.add(name="Maps")
    for number, (name, kind) in enumerate(MAP_KEYS.items(), start=1):
        entry = maps.nested_type.add(
            name=f"{name.capitalize()}Entry", options=descriptor_pb2.MessageOptions(map_entry=True)
        )
        _field(entry, "key", 1, kind)
        _field(entry, "value", 2, FieldDescriptorProto.TYPE_STRING)
        _field(
            maps,
            name,
            number,
            FieldDescriptorProto.TYPE_MESSAGE,
            type_name=f".types.Maps.{entry.name}",
            label=FieldDescriptorProto.LABEL_REPEATED,
        )

    known = types.message_type.add(name="WellKnown")
    for number, (name, type_name) in enumerate(WELL_KNOWN.items(), start=1):
        _field(known, name, number, FieldDescriptorProto.TYPE_MESSAGE, type_name=f".google.protobuf.{type_name}")
    _field(
        known,
        "values",
        len(WELL_KNOWN) + 1,
        FieldDescriptorProto.TYPE_MESSAGE,
        type_name=".google.protobuf.Int32Value",
        label=FieldDescriptorProto.LABEL_REPEATED,
    )
    labels = known.nested_type.add(name="LabelsEntry", options=descriptor_pb2.MessageOptions(map_entry=True))
    _field(labels, "key", 1, FieldDescriptorProto.TYPE_STRING)
    _field(labels, "value", 2, FieldDescriptorProto.TYPE_MESSAGE, type_name=".google.protobuf.Value")
    _field(
        known,
        "labels",
        len(WELL_KNOWN) + 2,
        FieldDescriptorProto.TYPE_MESSAGE,
        type_name=".types.WellKnown.LabelsEntry",
        label=FieldDescriptorProto.LABEL_REPEATED,
    )

    node = types.message_type.add(name="Node")
    _field(node, "name", 1, FieldDescriptorProto.TYPE_STRING)
    _field(
        node,
        "children",
        2,
        FieldDescriptorProto.TYPE_MESSAGE,
        type_name=".types.Node",
        label=FieldDescriptorProto.LABEL_REPEATED,
    )

    envelope = types.message_type.add(name="Envelope")
    _field(envelope, "id", 1, FieldDescriptorProto.TYPE_STRING)
    _field(envelope, "payload", 2, FieldDescriptorProto.TYPE_MESSAGE, type_name=".google.protobuf.Any")
    extras = envelope.nested_type.add(name="ExtrasEntry", options=descriptor_pb2.MessageOptions(map_entry=True))
    _field(extras, "key", 1, FieldDescriptorProto.TYPE_STRING)
    _field(extras, "value", 2, FieldDescriptorProto.TYPE_MESSAGE, type_name=".google.protobuf.Any")
    _field(
        envelope,
        "extras",
        3,
        FieldDescriptorProto.TYPE_MESSAGE,
        type_name=".types.Envelope.ExtrasEntry",
        label=FieldDescriptorProto.LABEL_REPEATED,
    )
    _field(envelope, "first", 4, FieldDescriptorProto.TYPE_MESSAGE, type_name=".types.Meta")
    _field(envelope, "second", 5, FieldDescriptorProto.TYPE_MESSAGE, type_name=".types.Meta")
    meta = types.message_type.add(name="Meta")
    _field(meta, "extra", 1, FieldDescriptorProto.TYPE_MESSAGE, type_name=".google.protobuf.Any")
    _field(meta, "label", 2, FieldDescriptorProto.TYPE_STRING, json_name="title")

    blobs = types.message_type.add(name="Blobs")
    _field(blobs, "chunks", 1, FieldDescriptorProto.TYPE_BYTES, label=FieldDescriptorProto.LABEL_REPEATED)
    by_name = blobs.nested_type.add(name="ByNameEntry", options=descriptor_pb2.MessageOptions(map_entry=True))
    _field(by_name, "key", 1, FieldDescriptorProto.TYPE_STRING)
    _field(by_name, "value", 2, FieldDescriptorProto.TYPE_BYTES)
    _field(
        blobs,
        "by_name",
        2,
        FieldDescriptorProto.TYPE_MESSAGE,
        type_name=".types.Blobs.ByNameEntry",
        label=FieldDescriptorProto.LABEL_REPEATED,
    )
    _field(blobs, "scalars", 3, FieldDescriptorProto.TYPE_MESSAGE, type_name=".types.Scalars")

    choice = types.message_type.add(name="Choice")
    choice.oneof_decl.add(name="target")
    choice.oneof_decl.add(name="_note")
    _field(choice, "email", 1, FieldDescriptorProto.TYPE_STRING, oneof_index=0)
    _field(choice, "node", 2, FieldDescriptorProto.TYPE_MESSAGE, type_name=".types.Node", oneof_index=0)
    _field(choice, "note", 3, FieldDescriptorProto.TYPE_STRING, oneof_index=1, proto3_optional=True)

    legacy = descriptor_pb2.FileDescriptorProto(
        name="legacy.proto",
        package="legacy",
        syntax="proto2",
        dependency=["google/protobuf/any.proto", "google/protobuf/wrappers.proto"],
    )
    legacy.enum_type.add(name="Level").value.add(name="LOW", number=1)
    message = legacy.message_type.add(name="Legacy")
    _field(message, "id", 1, FieldDescriptorProto.TYPE_INT32, label=FieldDescriptorProto.LABEL_REQUIRED)
    _field(message, "level", 2, FieldDescriptorProto.TYPE_ENUM, type_name=".legacy.Level")
    required = FieldDescriptorProto.LABEL_REQUIRED
    _field(message, "blob", 3, FieldDescriptorProto.TYPE_MESSAGE, type_name=".google.protobuf.Any", label=required)
    _field(
        message, "label", 4, FieldDescriptorProto.TYPE_MESSAGE, type_name=".google.protobuf.StringValue", label=required
    )

    extendable = legacy.message_type.add(name="Extendable")
    extendable.extension_range.add(start=100, end=200)
    legacy.extension.add(
        name="blob",
        number=100,
        type=FieldDescriptorProto.TYPE_BYTES,
        label=FieldDescriptorProto.LABEL_OPTIONAL,
        extendee=".legacy.Extendable",
    )

    editions = descriptor_pb2.FileDescriptorProto(
        name="editions.proto", package="editions", syntax="editions", edition=descriptor_pb2.EDITION_2023
    )
    message = editions.message_type.add(name="Edition")
    _field(message, "id", 1, FieldDescriptorProto.TYPE_INT32)
    message.field[0].options.features.field_presence = descriptor_pb2.FeatureSet.LEGACY_REQUIRED
    _field(message, "name", 2, FieldDescriptorProto.TYPE_STRING)

    service = types.service.add(name="Types")
    types.dependency.extend(["legacy.proto", "editions.proto"])
    for input_type in (
        ".types.Scalars",
        ".types.Maps",
        ".types.WellKnown",
        ".types.Node",
        ".types.Envelope",
        ".types.Choice",
        ".legacy.Legacy",
        ".legacy.Extendable",
        ".editions.Edition",
        ".google.protobuf.StringValue",
    ):
        # Each method answers with its request message, so responses take every shape requests do.
        service.method.add(name=input_type.rsplit(".", 1)[1], input_type=input_type, output_type=input_type)
    return descriptor_pb2.FileDescriptorSet(file=[*well_known, legacy, editions, types]).SerializeToString()


def nested_descriptor_set() -> bytes:
    """Messages whose shape stresses structural cases: deep, wide, self-nested, or with an enum at the int32 limit."""
    nested = descriptor_pb2.FileDescriptorProto(name="nested.proto", package="nested", syntax="proto3")
    nested.enum_type.add(name="Kind").value.add(name="KIND_UNSPECIFIED", number=0)
    leaf = nested.message_type.add(name="Leaf")
    _field(
        leaf,
        "kinds",
        1,
        FieldDescriptorProto.TYPE_ENUM,
        type_name=".nested.Kind",
        label=FieldDescriptorProto.LABEL_REPEATED,
    )
    middle = nested.message_type.add(name="Middle")
    _field(middle, "leaf", 1, FieldDescriptorProto.TYPE_MESSAGE, type_name=".nested.Leaf")
    _field(middle, "other", 2, FieldDescriptorProto.TYPE_MESSAGE, type_name=".nested.Leaf")
    chain = nested.message_type.add(name="Chain")
    _field(chain, "middle", 1, FieldDescriptorProto.TYPE_MESSAGE, type_name=".nested.Middle")
    wide = nested.message_type.add(name="Wide")
    for number in range(1, 61):
        _field(wide, f"leaf{number}", number, FieldDescriptorProto.TYPE_MESSAGE, type_name=".nested.Leaf")
    flat = nested.message_type.add(name="Flat")
    for number in range(1, 51):
        _field(flat, f"count{number}", number, FieldDescriptorProto.TYPE_INT32)
    _field(flat, "child", 51, FieldDescriptorProto.TYPE_MESSAGE, type_name=".nested.Leaf")
    tree = nested.message_type.add(name="Tree")
    for number in range(1, 31):
        _field(tree, f"branch{number}", number, FieldDescriptorProto.TYPE_MESSAGE, type_name=".nested.Tree")
    extreme = nested.enum_type.add(name="Extreme")
    extreme.value.add(name="EXTREME_UNSPECIFIED", number=0)
    extreme.value.add(name="EXTREME_MAX", number=2**31 - 1)
    edge = nested.message_type.add(name="Edge")
    _field(edge, "extreme", 1, FieldDescriptorProto.TYPE_ENUM, type_name=".nested.Extreme")
    service = nested.service.add(name="Nested")
    for name in ("Chain", "Wide", "Flat", "Tree", "Edge"):
        service.method.add(name=name, input_type=f".nested.{name}", output_type=f".nested.{name}")
    return descriptor_pb2.FileDescriptorSet(file=[nested]).SerializeToString()


SCALARS = {
    "int32": FieldDescriptorProto.TYPE_INT32,
    "sint32": FieldDescriptorProto.TYPE_SINT32,
    "sfixed32": FieldDescriptorProto.TYPE_SFIXED32,
    "uint32": FieldDescriptorProto.TYPE_UINT32,
    "fixed32": FieldDescriptorProto.TYPE_FIXED32,
    "int64": FieldDescriptorProto.TYPE_INT64,
    "sint64": FieldDescriptorProto.TYPE_SINT64,
    "sfixed64": FieldDescriptorProto.TYPE_SFIXED64,
    "uint64": FieldDescriptorProto.TYPE_UINT64,
    "fixed64": FieldDescriptorProto.TYPE_FIXED64,
    "float": FieldDescriptorProto.TYPE_FLOAT,
    "double": FieldDescriptorProto.TYPE_DOUBLE,
    "bool": FieldDescriptorProto.TYPE_BOOL,
    "string": FieldDescriptorProto.TYPE_STRING,
    "bytes": FieldDescriptorProto.TYPE_BYTES,
}
MAP_KEYS = {
    "by_int32": FieldDescriptorProto.TYPE_INT32,
    "by_uint32": FieldDescriptorProto.TYPE_UINT32,
    "by_int64": FieldDescriptorProto.TYPE_INT64,
    "by_fixed64": FieldDescriptorProto.TYPE_FIXED64,
    "by_bool": FieldDescriptorProto.TYPE_BOOL,
    "by_string": FieldDescriptorProto.TYPE_STRING,
}
WELL_KNOWN = {
    "timestamp": "Timestamp",
    "duration": "Duration",
    "field_mask": "FieldMask",
    "empty": "Empty",
    "struct": "Struct",
    "value": "Value",
    "list_value": "ListValue",
    "double_value": "DoubleValue",
    "float_value": "FloatValue",
    "int64_value": "Int64Value",
    "uint64_value": "UInt64Value",
    "int32_value": "Int32Value",
    "uint32_value": "UInt32Value",
    "bool_value": "BoolValue",
    "string_value": "StringValue",
    "bytes_value": "BytesValue",
}


def _field(
    message: descriptor_pb2.DescriptorProto,
    name: str,
    number: int,
    kind: int,
    *,
    type_name: str = "",
    label: int = FieldDescriptorProto.LABEL_OPTIONAL,
    oneof_index: int | None = None,
    proto3_optional: bool = False,
    json_name: str = "",
) -> None:
    field = message.field.add(name=name, number=number, type=kind, label=label)
    if json_name:
        field.json_name = json_name
    if type_name:
        field.type_name = type_name
    if oneof_index is not None:
        field.oneof_index = oneof_index
    if proto3_optional:
        field.proto3_optional = True


def validated_descriptor_set() -> bytes:
    """A message with `buf.validate` and legacy `validate` rules on its fields."""
    well_known = []
    for module in (descriptor_pb2, empty_pb2, wrappers_pb2, any_pb2, timestamp_pb2):
        file = descriptor_pb2.FileDescriptorProto()
        module.DESCRIPTOR.CopyToProto(file)
        well_known.append(file)
    protovalidate, legacy = _protovalidate_file(), _legacy_validate_file()
    rules = descriptor_pool.DescriptorPool()
    for file in (*well_known, protovalidate, legacy):
        rules.Add(file)

    def field_rules(field: FieldDescriptorProto, extension: str, value: dict) -> None:
        _set_extension(rules, field.options, "google.protobuf.FieldOptions", extension, value)

    validated = descriptor_pb2.FileDescriptorProto(
        name="validated.proto",
        package="validated",
        syntax="proto3",
        dependency=[*(file.name for file in well_known), protovalidate.name, legacy.name],
    )
    kinds = validated.enum_type.add(name="Kind")
    for number, name in enumerate(["KIND_UNSPECIFIED", "KIND_A", "KIND_B", "KIND_C"]):
        kinds.value.add(name=name, number=number)
    shades = validated.enum_type.add(name="Shade", options=descriptor_pb2.EnumOptions(allow_alias=True))
    for name, number in [
        ("SHADE_UNSPECIFIED", 0),
        ("SHADE_NONE", 0),
        ("SHADE_DARK", 1),
        ("SHADE_BLACK", 1),
        ("SHADE_LIGHT", 2),
    ]:
        shades.value.add(name=name, number=number)
    parent = validated.message_type.add(name="Parent")
    _field(parent, "id", 1, FieldDescriptorProto.TYPE_STRING)
    _field(parent, "enabled", 2, FieldDescriptorProto.TYPE_BOOL)

    span = validated.message_type.add(name="Span")
    labels = span.nested_type.add(name="LabelsEntry", options=descriptor_pb2.MessageOptions(map_entry=True))
    _field(labels, "key", 1, FieldDescriptorProto.TYPE_STRING)
    _field(labels, "value", 2, FieldDescriptorProto.TYPE_STRING)
    span.oneof_decl.add(name="target")
    span.oneof_decl.add(name="side")
    for oneof, extension in zip(span.oneof_decl, ["buf.validate.oneof", "validate.required"], strict=True):
        _set_extension(rules, oneof.options, "google.protobuf.OneofOptions", extension, {"required": True})
    repeated = FieldDescriptorProto.LABEL_REPEATED
    fields = [
        ("trace_id", FieldDescriptorProto.TYPE_BYTES, {}, {"bytes": {"len": 16}}),
        ("span_id", FieldDescriptorProto.TYPE_BYTES, {}, {"bytes": {"len": 8}}),
        ("payload", FieldDescriptorProto.TYPE_BYTES, {}, {"bytes": {"minLen": 2, "maxLen": 7}}),
        ("upload", FieldDescriptorProto.TYPE_BYTES, {}, {"bytes": {"minLen": 1, "maxLen": 1048576}}),
        ("preview", FieldDescriptorProto.TYPE_BYTES, {}, {"bytes": {"maxLen": 1048576}}),
        (
            "inverted",
            FieldDescriptorProto.TYPE_BYTES,
            {},
            {"ignore": "IGNORE_IF_ZERO_VALUE", "bytes": {"minLen": 5, "maxLen": 3}},
        ),
        ("id", FieldDescriptorProto.TYPE_STRING, {}, {"string": {"uuid": True}}),
        (
            "name",
            FieldDescriptorProto.TYPE_STRING,
            {},
            {"string": {"minLen": 4, "maxLen": 10, "prefix": "sp-", "notIn": ["sp-x"]}},
        ),
        ("email", FieldDescriptorProto.TYPE_STRING, {}, {"string": {"email": True}}),
        ("count", FieldDescriptorProto.TYPE_INT32, {}, {"int32": {"gt": 0, "lte": 100}}),
        ("total", FieldDescriptorProto.TYPE_INT64, {}, {"int64": {"gte": "1", "notIn": ["13"]}}),
        ("ratio", FieldDescriptorProto.TYPE_DOUBLE, {}, {"double": {"gt": 0, "lt": 1}}),
        ("outside", FieldDescriptorProto.TYPE_INT32, {}, {"int32": {"gt": 10, "lt": 5}}),
        (
            "kind",
            FieldDescriptorProto.TYPE_ENUM,
            {"type_name": ".validated.Kind"},
            {"enum": {"definedOnly": True, "notIn": [0, 3]}},
        ),
        (
            "tags",
            FieldDescriptorProto.TYPE_STRING,
            {"label": repeated},
            {"repeated": {"minItems": "1", "maxItems": "3", "unique": True, "items": {"string": {"minLen": "2"}}}},
        ),
        (
            "labels",
            FieldDescriptorProto.TYPE_MESSAGE,
            {"type_name": ".validated.Span.LabelsEntry", "label": repeated},
            {
                "required": True,
                "map": {"maxPairs": "2", "keys": {"string": {"minLen": "1"}}, "values": {"string": {"maxLen": "5"}}},
            },
        ),
        ("parent", FieldDescriptorProto.TYPE_MESSAGE, {"type_name": ".validated.Parent"}, {"required": True}),
        ("skipped", FieldDescriptorProto.TYPE_STRING, {}, {"ignore": "IGNORE_ALWAYS", "string": {"minLen": "5"}}),
        ("note", FieldDescriptorProto.TYPE_STRING, {}, {"ignore": "IGNORE_IF_ZERO_VALUE", "string": {"minLen": "3"}}),
        ("code", FieldDescriptorProto.TYPE_STRING, {}, {"required": True}),
        (
            "nickname",
            FieldDescriptorProto.TYPE_MESSAGE,
            {"type_name": ".google.protobuf.StringValue"},
            {"required": True, "string": {"maxLen": "3"}},
        ),
        ("host", FieldDescriptorProto.TYPE_STRING, {"oneof_index": 0}, {"string": {"hostname": True}}),
        ("address", FieldDescriptorProto.TYPE_STRING, {"oneof_index": 0}, {"string": {"ip": True}}),
        ("plain", FieldDescriptorProto.TYPE_STRING, {}, None),
        ("left", FieldDescriptorProto.TYPE_STRING, {"oneof_index": 1}, None),
        ("right", FieldDescriptorProto.TYPE_STRING, {"oneof_index": 1}, None),
    ]
    for number, (name, field_type, extra, value) in enumerate(fields, start=1):
        _field(span, name, number, field_type, **extra)
        if value is not None:
            field_rules(span.field[-1], "buf.validate.field", value)
    _field(span, "legacy", len(fields) + 1, FieldDescriptorProto.TYPE_STRING)
    field_rules(span.field[-1], "validate.rules", {"string": {"maxLen": "4"}})
    _field(span, "legacy_parent", len(fields) + 2, FieldDescriptorProto.TYPE_MESSAGE, type_name=".validated.Parent")
    field_rules(span.field[-1], "validate.rules", {"message": {"required": True}})

    extras = validated.message_type.add(name="Extras")
    counts = extras.nested_type.add(name="CountsEntry", options=descriptor_pb2.MessageOptions(map_entry=True))
    _field(counts, "key", 1, FieldDescriptorProto.TYPE_INT32)
    _field(counts, "value", 2, FieldDescriptorProto.TYPE_STRING)
    for name in ("only", "free"):
        extras.oneof_decl.add(name=name)
    _set_extension(
        rules, extras.oneof_decl[0].options, "google.protobuf.OneofOptions", "buf.validate.oneof", {"required": True}
    )
    string_rules = {
        "file": {
            "suffix": ".txt",
            "contains": "a.b",
            "notContains": "zz",
            "minBytes": "8",
            "maxBytes": "40",
        },
        "slug": {"pattern": "^[a-z]+$", "prefix": "ab"},
        "fixed": {"const": "x"},
        "chosen": {"in": ["a", "b"]},
        "short": {"len": "3"},
        "digest": {"minBytes": "4", "maxBytes": "8"},
        "word": {"pattern": "^[[:alpha:]]+[[:^digit:]]$"},
        "version": {"pattern": r"^\Qv1.0\E(\.[[:digit:]]+)?\z"},
        "day": {"pattern": "^(?i)mon|tue$"},
        "spaced": {"pattern": r"^[\w\s]+\s[^\s]$"},
        "v4": {"ipv4": True},
        "v6": {"ipv6": True},
        "link": {"uri": True},
        "ref": {"uriRef": True},
        "trimmed": {"tuuid": True},
        "place": {"address": True},
    }
    extra_fields = [
        *((name, FieldDescriptorProto.TYPE_STRING, {}, {"string": value}) for name, value in string_rules.items()),
        ("level", FieldDescriptorProto.TYPE_INT32, {}, {"int32": {"in": [1, 2]}}),
        ("scale", FieldDescriptorProto.TYPE_FLOAT, {}, {"float": {"const": 1.5}}),
        ("on", FieldDescriptorProto.TYPE_BOOL, {}, {"bool": {"const": True}}),
        ("flag", FieldDescriptorProto.TYPE_BOOL, {}, {"required": True}),
        ("amount", FieldDescriptorProto.TYPE_INT64, {}, {"required": True}),
        ("picked", FieldDescriptorProto.TYPE_ENUM, {"type_name": ".validated.Kind"}, {"required": True}),
        ("mode", FieldDescriptorProto.TYPE_ENUM, {"type_name": ".validated.Kind"}, {"enum": {"const": 2}}),
        (
            "modes",
            FieldDescriptorProto.TYPE_ENUM,
            {"type_name": ".validated.Kind", "label": repeated},
            {"repeated": {"items": {"enum": {"in": [1, 2]}}}},
        ),
        ("blob", FieldDescriptorProto.TYPE_BYTES, {}, {"bytes": {"minLen": "3"}}),
        ("raw", FieldDescriptorProto.TYPE_BYTES, {}, {"bytes": {"pattern": "^[a-z]+$"}}),
        (
            "counts",
            FieldDescriptorProto.TYPE_MESSAGE,
            {"type_name": ".validated.Extras.CountsEntry", "label": repeated},
            {"map": {"minPairs": "1", "keys": {"int32": {"gt": 0}}}},
        ),
        (
            "names",
            FieldDescriptorProto.TYPE_STRING,
            {"label": repeated},
            {"required": True, "repeated": {"maxItems": "2"}},
        ),
        ("solo", FieldDescriptorProto.TYPE_STRING, {"oneof_index": 0}, None),
        ("first", FieldDescriptorProto.TYPE_STRING, {"oneof_index": 1}, None),
        ("second", FieldDescriptorProto.TYPE_STRING, {"oneof_index": 1}, None),
    ]
    for number, (name, field_type, extra, value) in enumerate(extra_fields, start=1):
        _field(extras, name, number, field_type, **extra)
        if value is not None:
            field_rules(extras.field[-1], "buf.validate.field", value)

    edges = validated.message_type.add(name="Edges")
    for entry, key_type in (
        ("RankedEntry", FieldDescriptorProto.TYPE_INT32),
        ("BelowEntry", FieldDescriptorProto.TYPE_INT32),
        ("PinnedEntry", FieldDescriptorProto.TYPE_INT64),
        ("OuterEntry", FieldDescriptorProto.TYPE_INT32),
    ):
        keyed = edges.nested_type.add(name=entry, options=descriptor_pb2.MessageOptions(map_entry=True))
        _field(keyed, "key", 1, key_type)
        _field(keyed, "value", 2, FieldDescriptorProto.TYPE_STRING)
    edges.oneof_decl.add(name="pick")
    _set_extension(
        rules, edges.oneof_decl[0].options, "google.protobuf.OneofOptions", "buf.validate.oneof", {"required": True}
    )
    edge_fields = [
        (
            "anys",
            FieldDescriptorProto.TYPE_MESSAGE,
            {"type_name": ".google.protobuf.Any", "label": repeated},
            {"repeated": {"maxItems": "3"}},
        ),
        ("small", FieldDescriptorProto.TYPE_FLOAT, {}, {"float": {"gt": 0, "lt": 1, "notIn": ["NaN"]}}),
        ("nonzero", FieldDescriptorProto.TYPE_FLOAT, {}, {"required": True}),
        ("token", FieldDescriptorProto.TYPE_BYTES, {}, {"bytes": {"in": ["AAE=", "AQI="]}}),
        ("magic", FieldDescriptorProto.TYPE_BYTES, {}, {"bytes": {"const": "-_8="}}),
        ("only", FieldDescriptorProto.TYPE_STRING, {"oneof_index": 0}, {"required": True}),
        (
            "ranked",
            FieldDescriptorProto.TYPE_MESSAGE,
            {"type_name": ".validated.Edges.RankedEntry", "label": repeated},
            {"map": {"keys": {"int32": {"gte": 5, "lte": 7, "notIn": [6]}}}},
        ),
        (
            "below",
            FieldDescriptorProto.TYPE_MESSAGE,
            {"type_name": ".validated.Edges.BelowEntry", "label": repeated},
            {"map": {"keys": {"int32": {"gt": -3, "lt": 0}}}},
        ),
        ("picks", FieldDescriptorProto.TYPE_INT32, {}, {"int32": {"gte": 1, "lte": 3, "notIn": [2]}}),
        ("triple", FieldDescriptorProto.TYPE_STRING, {"label": repeated}, {"repeated": {"minItems": "3"}}),
        ("shade", FieldDescriptorProto.TYPE_ENUM, {"type_name": ".validated.Shade"}, {"required": True}),
        (
            "shades",
            FieldDescriptorProto.TYPE_ENUM,
            {"type_name": ".validated.Shade", "label": repeated},
            {"repeated": {"unique": True}},
        ),
        (
            "odds",
            FieldDescriptorProto.TYPE_FLOAT,
            {},
            {"float": {"notIn": [0.5, 1.1, 1.5, 3.4028234663852886e38, "Infinity", "-Infinity", "NaN"]}},
        ),
        (
            "pinned",
            FieldDescriptorProto.TYPE_MESSAGE,
            {"type_name": ".validated.Edges.PinnedEntry", "label": repeated},
            {"map": {"keys": {"int64": {"const": "5"}}}},
        ),
        (
            "outer",
            FieldDescriptorProto.TYPE_MESSAGE,
            {"type_name": ".validated.Edges.OuterEntry", "label": repeated},
            {"map": {"keys": {"int32": {"gt": 10, "lt": 5}}}},
        ),
        ("default_kind", FieldDescriptorProto.TYPE_ENUM, {"type_name": ".validated.Kind"}, {"enum": {"const": 0}}),
    ]
    for number, (name, field_type, extra, value) in enumerate(edge_fields, start=1):
        _field(edges, name, number, field_type, **extra)
        if value is not None:
            field_rules(edges.field[-1], "buf.validate.field", value)
    _field(
        edges,
        "created",
        len(edge_fields) + 1,
        FieldDescriptorProto.TYPE_MESSAGE,
        type_name=".google.protobuf.Timestamp",
    )
    field_rules(edges.field[-1], "validate.rules", {"timestamp": {"required": True}})

    choices = validated.message_type.add(name="Choices")
    choices.oneof_decl.add(name="boxed")
    _set_extension(
        rules, choices.oneof_decl[0].options, "google.protobuf.OneofOptions", "buf.validate.oneof", {"required": True}
    )
    choices.oneof_decl.add(name="matcher")
    _set_extension(
        rules, choices.oneof_decl[1].options, "google.protobuf.OneofOptions", "validate.required", {"required": True}
    )
    choices.oneof_decl.add(name="_amount")
    _field(
        choices, "label", 1, FieldDescriptorProto.TYPE_MESSAGE, type_name=".google.protobuf.StringValue", oneof_index=0
    )
    _field(
        choices,
        "amount",
        2,
        FieldDescriptorProto.TYPE_MESSAGE,
        type_name=".google.protobuf.Int32Value",
        oneof_index=2,
        proto3_optional=True,
    )
    _field(choices, "exact", 3, FieldDescriptorProto.TYPE_STRING, oneof_index=1)
    _field(choices, "regex", 4, FieldDescriptorProto.TYPE_MESSAGE, type_name=".validated.Parent", oneof_index=1)
    field_rules(choices.field[-1], "validate.rules", {"message": {"required": True}})
    _set_extension(
        rules,
        choices.options,
        "google.protobuf.MessageOptions",
        "buf.validate.message",
        {"oneof": [{"fields": ["short_name", "alias"], "required": True}]},
    )
    _field(choices, "short_name", 5, FieldDescriptorProto.TYPE_STRING)
    field_rules(choices.field[-1], "buf.validate.field", {"string": {"minLen": "2"}})
    _field(choices, "alias", 6, FieldDescriptorProto.TYPE_STRING)

    recursive = validated.message_type.add(name="Recursive")
    recursive.oneof_decl.add(name="kind")
    _set_extension(
        rules, recursive.oneof_decl[0].options, "google.protobuf.OneofOptions", "buf.validate.oneof", {"required": True}
    )
    recursive_group = validated.message_type.add(name="RecursiveGroup")
    _field(
        recursive_group,
        "items",
        1,
        FieldDescriptorProto.TYPE_MESSAGE,
        type_name=".validated.Recursive",
        label=repeated,
    )
    field_rules(recursive_group.field[-1], "buf.validate.field", {"repeated": {"minItems": "1"}})
    _field(
        recursive,
        "group",
        1,
        FieldDescriptorProto.TYPE_MESSAGE,
        type_name=".validated.RecursiveGroup",
        oneof_index=0,
    )
    _field(recursive, "leaf", 2, FieldDescriptorProto.TYPE_STRING, oneof_index=0)

    service = validated.service.add(name="Spans")
    service.method.add(name="Record", input_type=".validated.Span", output_type=".google.protobuf.Empty")
    service.method.add(name="Check", input_type=".validated.Extras", output_type=".google.protobuf.Empty")
    service.method.add(name="Edge", input_type=".validated.Edges", output_type=".google.protobuf.Empty")
    service.method.add(name="Choose", input_type=".validated.Choices", output_type=".google.protobuf.Empty")
    service.method.add(name="Recurse", input_type=".validated.Recursive", output_type=".google.protobuf.Empty")
    return descriptor_pb2.FileDescriptorSet(file=[*well_known, protovalidate, legacy, validated]).SerializeToString()


def old_protovalidate_descriptor_set() -> bytes:
    """A message with rules from a `buf.validate` release that names its field rules `FieldConstraints`."""
    well_known = descriptor_pb2.FileDescriptorProto()
    descriptor_pb2.DESCRIPTOR.CopyToProto(well_known)
    protovalidate = _protovalidate_file("FieldConstraints")
    rules = descriptor_pool.DescriptorPool()
    for file in (well_known, protovalidate):
        rules.Add(file)
    old = descriptor_pb2.FileDescriptorProto(
        name="old.proto", package="old", syntax="proto3", dependency=[well_known.name, protovalidate.name]
    )
    tagged = old.message_type.add(name="Tagged")
    _field(tagged, "tags", 1, FieldDescriptorProto.TYPE_STRING, label=FieldDescriptorProto.LABEL_REPEATED)
    _set_extension(
        rules,
        tagged.field[-1].options,
        "google.protobuf.FieldOptions",
        "buf.validate.field",
        {"repeated": {"items": {"string": {"minLen": "2"}}}},
    )
    return descriptor_pb2.FileDescriptorSet(file=[well_known, protovalidate, old]).SerializeToString()


def _set_extension(
    pool: descriptor_pool.DescriptorPool,
    options: message.Message,
    options_type: str,
    extension: str,
    value: dict,
) -> None:
    # The options of a pool-built descriptor proto keep extensions it does not know as unknown fields.
    known = message_factory.GetMessageClass(pool.FindMessageTypeByName(options_type))()
    definition = pool.FindExtensionByName(extension)
    if definition.message_type is None:
        known.Extensions[definition] = value["required"]
    else:
        json_format.ParseDict(value, known.Extensions[definition])
    options.MergeFromString(known.SerializeToString())


def _protovalidate_file(field_rules: str = "FieldRules") -> descriptor_pb2.FileDescriptorProto:
    """The part of `buf/validate/validate.proto` the validated messages use; releases before 0.11 name `FieldRules` `FieldConstraints`."""
    file = descriptor_pb2.FileDescriptorProto(
        name="buf/validate/validate.proto",
        package="buf.validate",
        syntax="proto2",
        dependency=["google/protobuf/descriptor.proto"],
    )
    ignore = file.enum_type.add(name="Ignore")
    for name, number in [("IGNORE_UNSPECIFIED", 0), ("IGNORE_IF_ZERO_VALUE", 1), ("IGNORE_ALWAYS", 3)]:
        ignore.value.add(name=name, number=number)
    _rules_messages(
        file,
        ".buf.validate",
        {
            field_rules: [
                ("required", 25, FieldDescriptorProto.TYPE_BOOL, ""),
                ("ignore", 27, FieldDescriptorProto.TYPE_ENUM, ".buf.validate.Ignore"),
            ],
            "Int32Rules": [
                *_numeric_rules(FieldDescriptorProto.TYPE_INT32),
                ("in", -6, FieldDescriptorProto.TYPE_INT32, ""),
            ],
            "FloatRules": [
                ("const", 1, FieldDescriptorProto.TYPE_FLOAT, ""),
                *_numeric_rules(FieldDescriptorProto.TYPE_FLOAT),
            ],
            "BoolRules": [("const", 1, FieldDescriptorProto.TYPE_BOOL, "")],
            "Int64Rules": [
                ("const", 1, FieldDescriptorProto.TYPE_INT64, ""),
                *_numeric_rules(FieldDescriptorProto.TYPE_INT64),
            ],
            "DoubleRules": _numeric_rules(FieldDescriptorProto.TYPE_DOUBLE),
            "StringRules": [
                *_length_rules(),
                ("const", 1, FieldDescriptorProto.TYPE_STRING, ""),
                ("min_bytes", 4, FieldDescriptorProto.TYPE_UINT64, ""),
                ("max_bytes", 5, FieldDescriptorProto.TYPE_UINT64, ""),
                ("pattern", 6, FieldDescriptorProto.TYPE_STRING, ""),
                ("prefix", 7, FieldDescriptorProto.TYPE_STRING, ""),
                ("suffix", 8, FieldDescriptorProto.TYPE_STRING, ""),
                ("contains", 9, FieldDescriptorProto.TYPE_STRING, ""),
                ("in", -10, FieldDescriptorProto.TYPE_STRING, ""),
                ("not_in", -11, FieldDescriptorProto.TYPE_STRING, ""),
                ("len", 19, FieldDescriptorProto.TYPE_UINT64, ""),
                ("not_contains", 23, FieldDescriptorProto.TYPE_STRING, ""),
                *(
                    (name, number, FieldDescriptorProto.TYPE_BOOL, "")
                    for name, number in [
                        ("email", 12),
                        ("hostname", 13),
                        ("ip", 14),
                        ("ipv4", 15),
                        ("ipv6", 16),
                        ("uri", 17),
                        ("uri_ref", 18),
                        ("address", 21),
                        ("uuid", 22),
                        ("tuuid", 33),
                    ]
                ),
            ],
            "BytesRules": [
                *_length_rules(),
                ("const", 1, FieldDescriptorProto.TYPE_BYTES, ""),
                ("in", -8, FieldDescriptorProto.TYPE_BYTES, ""),
                ("not_in", -9, FieldDescriptorProto.TYPE_BYTES, ""),
                ("pattern", 4, FieldDescriptorProto.TYPE_STRING, ""),
                ("len", 13, FieldDescriptorProto.TYPE_UINT64, ""),
            ],
            "EnumRules": [
                ("const", 1, FieldDescriptorProto.TYPE_INT32, ""),
                ("defined_only", 2, FieldDescriptorProto.TYPE_BOOL, ""),
                ("in", -3, FieldDescriptorProto.TYPE_INT32, ""),
                ("not_in", -4, FieldDescriptorProto.TYPE_INT32, ""),
            ],
            "RepeatedRules": [
                ("min_items", 1, FieldDescriptorProto.TYPE_UINT64, ""),
                ("max_items", 2, FieldDescriptorProto.TYPE_UINT64, ""),
                ("unique", 3, FieldDescriptorProto.TYPE_BOOL, ""),
                ("items", 4, FieldDescriptorProto.TYPE_MESSAGE, f".buf.validate.{field_rules}"),
            ],
            "MapRules": [
                ("min_pairs", 1, FieldDescriptorProto.TYPE_UINT64, ""),
                ("max_pairs", 2, FieldDescriptorProto.TYPE_UINT64, ""),
                ("keys", 4, FieldDescriptorProto.TYPE_MESSAGE, f".buf.validate.{field_rules}"),
                ("values", 5, FieldDescriptorProto.TYPE_MESSAGE, f".buf.validate.{field_rules}"),
            ],
            "OneofRules": [("required", 1, FieldDescriptorProto.TYPE_BOOL, "")],
            "MessageRules": [("oneof", -4, FieldDescriptorProto.TYPE_MESSAGE, ".buf.validate.MessageOneofRule")],
            "MessageOneofRule": [
                ("fields", -1, FieldDescriptorProto.TYPE_STRING, ""),
                ("required", 2, FieldDescriptorProto.TYPE_BOOL, ""),
            ],
        },
        field_rules=field_rules,
        groups=[
            ("float", 1),
            ("double", 2),
            ("int32", 3),
            ("int64", 4),
            ("bool", 13),
            ("string", 14),
            ("bytes", 15),
            ("enum", 16),
            ("repeated", 18),
            ("map", 19),
        ],
    )
    _extension(file, "field", 1159, ".google.protobuf.FieldOptions", f".buf.validate.{field_rules}")
    _extension(file, "oneof", 1159, ".google.protobuf.OneofOptions", ".buf.validate.OneofRules")
    _extension(file, "message", 1159, ".google.protobuf.MessageOptions", ".buf.validate.MessageRules")
    return file


def _legacy_validate_file() -> descriptor_pb2.FileDescriptorProto:
    """The part of `validate/validate.proto` the validated messages use."""
    file = descriptor_pb2.FileDescriptorProto(
        name="validate/validate.proto",
        package="validate",
        syntax="proto2",
        dependency=["google/protobuf/descriptor.proto"],
    )
    _rules_messages(
        file,
        ".validate",
        {
            "FieldRules": [("message", 17, FieldDescriptorProto.TYPE_MESSAGE, ".validate.MessageRules")],
            "StringRules": [("max_len", 3, FieldDescriptorProto.TYPE_UINT64, "")],
            "MessageRules": [("required", 2, FieldDescriptorProto.TYPE_BOOL, "")],
            "TimestampRules": [("required", 1, FieldDescriptorProto.TYPE_BOOL, "")],
        },
        groups=[("string", 14), ("timestamp", 22)],
    )
    _extension(file, "rules", 1071, ".google.protobuf.FieldOptions", ".validate.FieldRules")
    _extension(file, "required", 1071, ".google.protobuf.OneofOptions")
    return file


def _numeric_rules(kind: int) -> list[tuple[str, int, int, str]]:
    return [
        ("lt", 2, kind, ""),
        ("lte", 3, kind, ""),
        ("gt", 4, kind, ""),
        ("gte", 5, kind, ""),
        ("not_in", -7, kind, ""),
    ]


def _length_rules() -> list[tuple[str, int, int, str]]:
    return [("min_len", 2, FieldDescriptorProto.TYPE_UINT64, ""), ("max_len", 3, FieldDescriptorProto.TYPE_UINT64, "")]


def _rules_messages(
    file: descriptor_pb2.FileDescriptorProto,
    package: str,
    messages: dict[str, list[tuple[str, int, int, str]]],
    *,
    groups: list[tuple[str, int]],
    field_rules: str = "FieldRules",
) -> None:
    # A negative number marks a repeated rule.
    for name, fields in messages.items():
        rules = file.message_type.add(name=name)
        for field_name, number, kind, type_name in fields:
            label = FieldDescriptorProto.LABEL_REPEATED if number < 0 else FieldDescriptorProto.LABEL_OPTIONAL
            _field(rules, field_name, abs(number), kind, type_name=type_name, label=label)
    rules_message = next(message for message in file.message_type if message.name == field_rules)
    rules_message.oneof_decl.add(name="type")
    for name, number in groups:
        rules_type = f"{package}.{name.capitalize()}Rules"
        _field(rules_message, name, number, FieldDescriptorProto.TYPE_MESSAGE, type_name=rules_type, oneof_index=0)


def _extension(
    file: descriptor_pb2.FileDescriptorProto, name: str, number: int, extendee: str, type_name: str = ""
) -> None:
    extension = file.extension.add(
        name=name, number=number, extendee=extendee, label=FieldDescriptorProto.LABEL_OPTIONAL
    )
    if type_name:
        extension.type = FieldDescriptorProto.TYPE_MESSAGE
        extension.type_name = type_name
    else:
        extension.type = FieldDescriptorProto.TYPE_BOOL
