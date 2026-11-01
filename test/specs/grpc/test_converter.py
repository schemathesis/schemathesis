import sys

import pytest
from google.protobuf import descriptor_pb2, descriptor_pool

from schemathesis.specs.grpc.converter import ConvertedMessage, message_schema
from test.apps.catalog.grpc.descriptors import (
    all_types_descriptor_set,
    old_protovalidate_descriptor_set,
    validated_descriptor_set,
)

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


BASE64 = "[A-Za-z0-9+/]"
WHOLE_GROUPS = f"^(?:{BASE64}{{4}})*$"
ONE_BYTE_LEFT = f"^(?:{BASE64}{{4}})*{BASE64}[AQgw]==$"
TWO_BYTES_LEFT = f"^(?:{BASE64}{{4}})*{BASE64}{{2}}[AEIMQUYcgkosw048]=$"


def load_pool(data):
    pool = descriptor_pool.DescriptorPool()
    for file in descriptor_pb2.FileDescriptorSet.FromString(data).file:
        pool.Add(file)
    return pool


@pytest.fixture(scope="module")
def pool():
    return load_pool(all_types_descriptor_set())


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
            "$defs": {"types.Meta": {**EMPTY_OBJECT, "properties": {"title": STRING}}},
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


def test_validation_rules():
    assert convert(load_pool(validated_descriptor_set()), "validated.Span").schema == {
        "type": "object",
        "properties": {
            "traceId": {"type": "string", "pattern": ONE_BYTE_LEFT, "minLength": 24, "maxLength": 24},
            "spanId": {"type": "string", "pattern": TWO_BYTES_LEFT, "minLength": 12, "maxLength": 12},
            "payload": {
                "type": "string",
                "anyOf": [
                    {"pattern": WHOLE_GROUPS, "minLength": 4, "maxLength": 8},
                    {"pattern": ONE_BYTE_LEFT, "minLength": 8, "maxLength": 12},
                    {"pattern": TWO_BYTES_LEFT, "minLength": 4, "maxLength": 8},
                ],
            },
            "upload": {
                "type": "string",
                "anyOf": [
                    {"pattern": WHOLE_GROUPS, "minLength": 4, "maxLength": 1398100},
                    {"pattern": ONE_BYTE_LEFT, "minLength": 4, "maxLength": 1398104},
                    {"pattern": TWO_BYTES_LEFT, "minLength": 4, "maxLength": 1398100},
                ],
            },
            "preview": {
                "type": "string",
                "anyOf": [
                    {"pattern": WHOLE_GROUPS, "maxLength": 1398100},
                    {"pattern": ONE_BYTE_LEFT, "minLength": 4, "maxLength": 1398104},
                    {"pattern": TWO_BYTES_LEFT, "minLength": 4, "maxLength": 1398100},
                ],
            },
            "inverted": {"type": "string", "not": {}},
            "id": {"type": "string", "format": "uuid"},
            "name": {"type": "string", "minLength": 4, "maxLength": 10, "pattern": "^sp-", "not": {"enum": ["sp-x"]}},
            "email": {"type": "string", "format": "email"},
            "count": {**INT32, "maximum": 100, "exclusiveMinimum": 0},
            "total": {**INT64, "minimum": 1, "not": {"enum": [13]}},
            "ratio": {**DOUBLE, "exclusiveMinimum": 0, "exclusiveMaximum": 1},
            "outside": {**INT32, "anyOf": [{"exclusiveMinimum": 10}, {"exclusiveMaximum": 5}]},
            "kind": {"enum": ["KIND_A", "KIND_B"]},
            "tags": {
                "type": "array",
                "items": {"type": "string", "minLength": 2},
                "minItems": 1,
                "maxItems": 3,
                "uniqueItems": True,
            },
            "labels": {
                "type": "object",
                "additionalProperties": {"type": "string", "maxLength": 5},
                "propertyNames": {"minLength": 1},
                "maxProperties": 2,
                "minProperties": 1,
            },
            "parent": {"$ref": "#/$defs/validated.Parent"},
            "skipped": STRING,
            "note": {"type": "string", "minLength": 3},
            "code": {"type": "string", "not": {"const": ""}},
            "nickname": {"type": "string", "maxLength": 3},
            "host": {"type": "string", "format": "hostname"},
            "address": {"type": "string", "anyOf": [{"format": "ipv4"}, {"format": "ipv6"}]},
            "plain": STRING,
            "left": STRING,
            "right": STRING,
            "legacy": {"type": "string", "maxLength": 4},
            "legacyParent": {"$ref": "#/$defs/validated.Parent"},
        },
        "additionalProperties": False,
        "required": [
            "traceId",
            "spanId",
            "payload",
            "upload",
            "preview",
            "id",
            "name",
            "email",
            "count",
            "total",
            "ratio",
            "outside",
            "kind",
            "tags",
            "labels",
            "parent",
            "code",
            "nickname",
            "legacy",
            "legacyParent",
        ],
        "allOf": [
            {"oneOf": [{"required": ["host"]}, {"required": ["address"]}]},
            {"oneOf": [{"required": ["left"]}, {"required": ["right"]}]},
        ],
        "$defs": {"validated.Parent": {"type": "object", "properties": {"id": STRING}, "additionalProperties": False}},
    }


def test_more_validation_rules():
    assert convert(load_pool(validated_descriptor_set()), "validated.Extras").schema == {
        "type": "object",
        "properties": {
            "file": {
                "type": "string",
                "minLength": 8,
                "maxLength": 10,
                "pattern": r"a\.b.*\.txt$",
                "not": {"pattern": "zz"},
            },
            "slug": {"type": "string", "pattern": "^[a-z]+$", "allOf": [{"pattern": "^ab"}]},
            "fixed": {"type": "string", "const": "x"},
            "chosen": {"type": "string", "enum": ["a", "b"]},
            "short": {"type": "string", "minLength": 3, "maxLength": 3},
            "digest": {"type": "string", "minLength": 4, "maxLength": 8, "not": {"pattern": "[^\\x00-\\x7f]"}},
            "word": {"type": "string", "pattern": "^[A-Za-z]+[^0-9]$"},
            "version": {"type": "string", "pattern": r"^v1\.0(\.[0-9]+)?$"},
            "day": {"type": "string", "pattern": "^(?i:mon)|(?i:tue$)"},
            "spaced": {"type": "string", "pattern": r"^[\w\t\n\f\r ]+[\t\n\f\r ][^\t\n\f\r ]$"},
            "v4": {"type": "string", "format": "ipv4"},
            "v6": {"type": "string", "format": "ipv6"},
            "link": {"type": "string", "format": "uri"},
            "ref": {"type": "string", "format": "uri-reference"},
            "trimmed": {"type": "string", "pattern": "^[0-9a-fA-F]{32}$"},
            "place": {"type": "string", "anyOf": [{"format": "hostname"}, {"format": "ipv4"}, {"format": "ipv6"}]},
            "level": {**INT32, "enum": [1, 2]},
            "scale": {**FLOAT, "const": 1.5},
            "on": {"type": "boolean", "const": True},
            "flag": {"type": "boolean", "not": {"const": False}},
            "amount": {**INT64, "not": {"const": 0}},
            "picked": {
                "enum": ["KIND_UNSPECIFIED", "KIND_A", "KIND_B", "KIND_C"],
                "not": {"const": "KIND_UNSPECIFIED"},
            },
            "mode": {"enum": ["KIND_B"]},
            "modes": {"type": "array", "items": {"enum": ["KIND_A", "KIND_B"]}},
            "blob": {
                "type": "string",
                "anyOf": [
                    {"pattern": WHOLE_GROUPS, "minLength": 4},
                    {"pattern": ONE_BYTE_LEFT, "minLength": 8},
                    {"pattern": TWO_BYTES_LEFT, "minLength": 8},
                ],
            },
            "raw": {"type": "string", "format": "byte"},
            "counts": {
                "type": "object",
                "additionalProperties": STRING,
                "propertyNames": {"enum": ["1", "2", "3", "4", "5", "6", "7", "8", "9", "10"]},
                "minProperties": 1,
            },
            "names": {"type": "array", "items": STRING, "maxItems": 2, "minItems": 1},
            "solo": STRING,
            "first": STRING,
            "second": STRING,
        },
        "additionalProperties": False,
        "required": [
            "file",
            "slug",
            "fixed",
            "chosen",
            "short",
            "digest",
            "word",
            "version",
            "day",
            "spaced",
            "v4",
            "v6",
            "link",
            "ref",
            "trimmed",
            "place",
            "level",
            "scale",
            "on",
            "flag",
            "amount",
            "picked",
            "mode",
            "modes",
            "blob",
            "raw",
            "counts",
            "names",
            "solo",
        ],
        "allOf": [
            {
                "oneOf": [
                    {"required": ["first"]},
                    {"required": ["second"]},
                    {"not": {"anyOf": [{"required": ["first"]}, {"required": ["second"]}]}},
                ]
            }
        ],
    }


FLOAT32_TINY = 1.401298464324817e-45


def test_validation_rules_on_edge_cases():
    assert convert(load_pool(validated_descriptor_set()), "validated.Edges").schema == {
        "type": "object",
        "properties": {
            "small": {"type": "number", "minimum": FLOAT32_TINY, "maximum": 0.9999999403953552},
            "nonzero": {**FLOAT, "anyOf": [{"minimum": FLOAT32_TINY}, {"maximum": -FLOAT32_TINY}]},
            "token": {"type": "string", "format": "byte", "enum": ["AAE=", "AQI="]},
            "magic": {"type": "string", "format": "byte", "const": "+/8="},
            "only": STRING,
            "ranked": {"type": "object", "additionalProperties": STRING, "propertyNames": {"enum": ["5", "7"]}},
            "below": {"type": "object", "additionalProperties": STRING, "propertyNames": {"enum": ["-2", "-1"]}},
            "picks": {"type": "integer", "minimum": 1, "maximum": 3, "not": {"enum": [2]}},
            "triple": {"type": "array", "items": STRING, "minItems": 3},
            "created": {"type": "string", "format": "date-time"},
        },
        "additionalProperties": False,
        "required": ["small", "nonzero", "token", "magic", "only", "ranked", "below", "picks", "triple", "created"],
    }


def test_rules_from_releases_before_field_rules_rename():
    assert convert(load_pool(old_protovalidate_descriptor_set()), "old.Tagged").schema == {
        "type": "object",
        "properties": {"tags": {"type": "array", "items": {"type": "string", "minLength": 2}}},
        "additionalProperties": False,
        "required": ["tags"],
    }
