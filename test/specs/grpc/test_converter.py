import sys

import pytest
from google.protobuf import descriptor_pb2, descriptor_pool

from schemathesis.specs.grpc.converter import ConvertedMessage, message_schema
from test.apps.catalog.grpc.descriptors import all_types_descriptor_set

INT32 = {"type": "integer", "minimum": -(2**31), "maximum": 2**31 - 1}
UINT32 = {"type": "integer", "minimum": 0, "maximum": 2**32 - 1}
INT64 = {"type": "integer", "minimum": -(2**63), "maximum": 2**63 - 1}
UINT64 = {"type": "integer", "minimum": 0, "maximum": 2**64 - 1}
FLOAT = {"type": "number", "minimum": -3.4028234663852886e38, "maximum": 3.4028234663852886e38}
DOUBLE = {"type": "number", "minimum": -sys.float_info.max, "maximum": sys.float_info.max}
VALUE = {"$ref": "#/$defs/google.protobuf.Value"}
VALUE_DEFINITION = {
    "type": ["null", "boolean", "string", "number", "array", "object"],
    "minimum": -sys.float_info.max,
    "maximum": sys.float_info.max,
    "items": VALUE,
    "additionalProperties": VALUE,
}
STRING = {"type": "string"}
EMPTY_OBJECT = {"type": "object", "properties": {}, "additionalProperties": False}
FIELD_MASK = {
    "type": "string",
    "pattern": r"^([a-z][A-Za-z0-9]*(\.[a-z][A-Za-z0-9]*)*(,[a-z][A-Za-z0-9]*(\.[a-z][A-Za-z0-9]*)*)*)?$",
}
NODE = {
    "type": "object",
    "properties": {"name": STRING, "children": {"type": "array", "items": {"$ref": "#/$defs/types.Node"}}},
    "additionalProperties": False,
}


@pytest.fixture(scope="module")
def pool():
    pool = descriptor_pool.DescriptorPool()
    for file in descriptor_pb2.FileDescriptorSet.FromString(all_types_descriptor_set()).file:
        pool.Add(file)
    return pool


def convert(pool, name):
    return message_schema(pool.FindMessageTypeByName(name))


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("int32", INT32),
        ("sint32", INT32),
        ("sfixed32", INT32),
        ("uint32", UINT32),
        ("fixed32", UINT32),
        ("int64", INT64),
        ("sint64", INT64),
        ("sfixed64", INT64),
        ("uint64", UINT64),
        ("fixed64", UINT64),
        ("float", FLOAT),
        ("double", DOUBLE),
        ("bool", {"type": "boolean"}),
        ("string", STRING),
        ("bytes", {"type": "string", "format": "byte"}),
        ("color", {"enum": ["COLOR_UNSPECIFIED", "RED"]}),
        ("tags", {"type": "array", "items": INT32}),
    ],
    ids=lambda value: value if isinstance(value, str) else None,
)
def test_scalar_bounds(pool, name, expected):
    assert convert(pool, "types.Scalars").schema["properties"][name] == expected


@pytest.mark.parametrize(
    ("name", "keys"),
    [
        ("byInt32", {"pattern": "^(0|-?[1-9][0-9]{0,8})$"}),
        ("byUint32", {"pattern": "^(0|[1-9][0-9]{0,8})$"}),
        ("byInt64", {"pattern": "^(0|-?[1-9][0-9]{0,17})$"}),
        ("byFixed64", {"pattern": "^(0|[1-9][0-9]{0,17})$"}),
        ("byBool", {"enum": ["true", "false"]}),
    ],
    ids=["int32", "uint32", "int64", "fixed64", "bool"],
)
def test_map_keys(pool, name, keys):
    assert convert(pool, "types.Maps").schema["properties"][name] == {
        "type": "object",
        "propertyNames": keys,
        "additionalProperties": STRING,
    }


def test_string_map_keys_are_unconstrained(pool):
    assert convert(pool, "types.Maps").schema["properties"]["byString"] == {
        "type": "object",
        "additionalProperties": STRING,
    }


def test_conversions_share_no_state(pool):
    first = convert(pool, "types.Maps").schema
    first["properties"]["byBool"]["propertyNames"]["enum"].append("maybe")
    assert convert(pool, "types.Maps").schema["properties"]["byBool"]["propertyNames"] == {"enum": ["true", "false"]}


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("timestamp", {"type": "string", "format": "date-time"}),
        ("duration", {"type": "string", "format": "_duration"}),
        ("fieldMask", FIELD_MASK),
        ("empty", {"$ref": "#/$defs/google.protobuf.Empty"}),
        ("struct", {"type": "object", "additionalProperties": VALUE}),
        ("value", VALUE),
        ("listValue", {"type": "array", "items": VALUE}),
        ("doubleValue", {**DOUBLE, "type": ["number", "null"]}),
        ("floatValue", {**FLOAT, "type": ["number", "null"]}),
        ("int64Value", {**INT64, "type": ["integer", "null"]}),
        ("uint64Value", {**UINT64, "type": ["integer", "null"]}),
        ("int32Value", {**INT32, "type": ["integer", "null"]}),
        ("uint32Value", {**UINT32, "type": ["integer", "null"]}),
        ("boolValue", {"type": ["boolean", "null"]}),
        ("stringValue", {"type": ["string", "null"]}),
        ("bytesValue", {"type": ["string", "null"], "format": "byte"}),
        ("values", {"type": "array", "items": INT32}),
    ],
    ids=lambda value: value if isinstance(value, str) else None,
)
def test_well_known_types(pool, name, expected):
    assert convert(pool, "types.WellKnown").schema["properties"][name] == expected


def test_well_known_definitions(pool):
    assert convert(pool, "types.WellKnown").schema["$defs"] == {
        "google.protobuf.Empty": EMPTY_OBJECT,
        "google.protobuf.Value": VALUE_DEFINITION,
    }


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("google.protobuf.StringValue", STRING),
        ("google.protobuf.Int64Value", INT64),
        ("google.protobuf.Timestamp", {"type": "string", "format": "date-time"}),
        ("google.protobuf.Duration", {"type": "string", "format": "_duration"}),
        ("google.protobuf.FieldMask", FIELD_MASK),
        (
            "google.protobuf.Struct",
            {"type": "object", "additionalProperties": VALUE, "$defs": {"google.protobuf.Value": VALUE_DEFINITION}},
        ),
        ("google.protobuf.Value", {**VALUE, "$defs": {"google.protobuf.Value": VALUE_DEFINITION}}),
        (
            "google.protobuf.ListValue",
            {"type": "array", "items": VALUE, "$defs": {"google.protobuf.Value": VALUE_DEFINITION}},
        ),
    ],
    ids=["string-value", "int64-value", "timestamp", "duration", "field-mask", "struct", "value", "list-value"],
)
def test_well_known_request_message(pool, name, expected):
    assert convert(pool, name) == ConvertedMessage(schema=expected, any_fields=[])


def test_any_request_message_is_skipped(pool):
    assert convert(pool, "google.protobuf.Any") == ConvertedMessage(
        schema=EMPTY_OBJECT, any_fields=["google.protobuf.Any"]
    )


def test_recursive_message_uses_defs(pool):
    assert convert(pool, "types.Node") == ConvertedMessage(
        schema={"$ref": "#/$defs/types.Node", "$defs": {"types.Node": NODE}}, any_fields=[]
    )


def test_any_fields_are_skipped(pool):
    assert convert(pool, "types.Envelope") == ConvertedMessage(
        schema={
            "type": "object",
            "properties": {
                "id": STRING,
                "first": {"$ref": "#/$defs/types.Meta"},
                "second": {"$ref": "#/$defs/types.Meta"},
            },
            "additionalProperties": False,
            "$defs": {"types.Meta": EMPTY_OBJECT},
        },
        any_fields=["types.Envelope.payload", "types.Envelope.extras", "types.Meta.extra"],
    )


def test_oneof_allows_at_most_one_member(pool):
    assert convert(pool, "types.Choice").schema == {
        "type": "object",
        "properties": {"email": STRING, "node": {"$ref": "#/$defs/types.Node"}, "note": STRING},
        "additionalProperties": False,
        "allOf": [
            {
                "oneOf": [
                    {"required": ["email"]},
                    {"required": ["node"]},
                    {"not": {"anyOf": [{"required": ["email"]}, {"required": ["node"]}]}},
                ]
            }
        ],
        "$defs": {"types.Node": NODE},
    }


def test_proto2_required_fields(pool):
    assert convert(pool, "legacy.Legacy") == ConvertedMessage(
        schema={
            "type": "object",
            "properties": {"id": INT32, "level": {"enum": ["LOW"]}, "blob": EMPTY_OBJECT, "label": STRING},
            "additionalProperties": False,
            "required": ["id", "blob", "label"],
        },
        any_fields=["legacy.Legacy.blob"],
    )


def test_editions_legacy_required_fields(pool):
    assert convert(pool, "editions.Edition").schema["required"] == ["id"]
