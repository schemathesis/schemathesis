from google.protobuf import (
    any_pb2,
    descriptor_pb2,
    duration_pb2,
    empty_pb2,
    field_mask_pb2,
    struct_pb2,
    timestamp_pb2,
    wrappers_pb2,
)

FieldDescriptorProto = descriptor_pb2.FieldDescriptorProto


def users_descriptor_set() -> bytes:
    timestamp = descriptor_pb2.FileDescriptorProto()
    timestamp_pb2.DESCRIPTOR.CopyToProto(timestamp)
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
        name="google/longrunning/operations.proto", package="google.longrunning", syntax="proto3"
    )
    operations.message_type.add(name="Operation")
    operations.message_type.add(name="GetOperationRequest")
    operations.service.add(name="Operations").method.add(
        name="GetOperation",
        input_type=".google.longrunning.GetOperationRequest",
        output_type=".google.longrunning.Operation",
    )
    return descriptor_pb2.FileDescriptorSet(file=[timestamp, users, operations]).SerializeToString()


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
        ".editions.Edition",
    ):
        service.method.add(
            name=input_type.rsplit(".", 1)[1], input_type=input_type, output_type=".google.protobuf.Empty"
        )
    return descriptor_pb2.FileDescriptorSet(file=[*well_known, legacy, editions, types]).SerializeToString()


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
) -> None:
    field = message.field.add(name=name, number=number, type=kind, label=label)
    if type_name:
        field.type_name = type_name
    if oneof_index is not None:
        field.oneof_index = oneof_index
    if proto3_optional:
        field.proto3_optional = True
