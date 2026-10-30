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

FieldDescriptorProto = descriptor_pb2.FieldDescriptorProto


def write_descriptor_set(directory: Path, data: bytes) -> Path:
    path = directory / "api.binpb"
    path.write_bytes(data)
    return path


def users_descriptor_set() -> bytes:
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
    operations.message_type.add(name="GetOperationRequest")
    operations.service.add(name="Operations").method.add(
        name="GetOperation",
        input_type=".google.longrunning.GetOperationRequest",
        output_type=".google.longrunning.Operation",
    )
    return descriptor_pb2.FileDescriptorSet(file=[timestamp, users, operations, any_file]).SerializeToString()


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
    parent = validated.message_type.add(name="Parent")
    _field(parent, "id", 1, FieldDescriptorProto.TYPE_STRING)

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
    for entry in ("RankedEntry", "BelowEntry"):
        keyed = edges.nested_type.add(name=entry, options=descriptor_pb2.MessageOptions(map_entry=True))
        _field(keyed, "key", 1, FieldDescriptorProto.TYPE_INT32)
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
        ("small", FieldDescriptorProto.TYPE_FLOAT, {}, {"float": {"gt": 0, "lt": 1}}),
        ("nonzero", FieldDescriptorProto.TYPE_FLOAT, {}, {"required": True}),
        ("token", FieldDescriptorProto.TYPE_BYTES, {}, {"bytes": {"in": ["AAE=", "AQI="]}}),
        ("magic", FieldDescriptorProto.TYPE_BYTES, {}, {"bytes": {"const": "AQI="}}),
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

    service = validated.service.add(name="Spans")
    service.method.add(name="Record", input_type=".validated.Span", output_type=".google.protobuf.Empty")
    service.method.add(name="Check", input_type=".validated.Extras", output_type=".google.protobuf.Empty")
    service.method.add(name="Edge", input_type=".validated.Edges", output_type=".google.protobuf.Empty")
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
            "Int64Rules": _numeric_rules(FieldDescriptorProto.TYPE_INT64),
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
