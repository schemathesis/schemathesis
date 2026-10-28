from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from google.protobuf.descriptor import FieldDescriptor

from schemathesis.core.jsonschema.numeric import FLOAT32_MAX, INT32_RANGE, INT64_RANGE
from schemathesis.core.transforms import deepclone

if TYPE_CHECKING:
    from collections.abc import Mapping

    from google.protobuf.descriptor import Descriptor

    from schemathesis.core.jsonschema.types import JsonSchemaObject

DOUBLE_MAX: Final = sys.float_info.max
INTEGER_BOUNDS: Final[Mapping[int, tuple[int, int]]] = {
    FieldDescriptor.TYPE_INT32: INT32_RANGE,
    FieldDescriptor.TYPE_SINT32: INT32_RANGE,
    FieldDescriptor.TYPE_SFIXED32: INT32_RANGE,
    FieldDescriptor.TYPE_UINT32: (0, 2**32 - 1),
    FieldDescriptor.TYPE_FIXED32: (0, 2**32 - 1),
    FieldDescriptor.TYPE_INT64: INT64_RANGE,
    FieldDescriptor.TYPE_SINT64: INT64_RANGE,
    FieldDescriptor.TYPE_SFIXED64: INT64_RANGE,
    FieldDescriptor.TYPE_UINT64: (0, 2**64 - 1),
    FieldDescriptor.TYPE_FIXED64: (0, 2**64 - 1),
}
# Map keys travel as JSON strings; these patterns admit a subset of each key type that always fits its range.
MAP_KEYS: Final[Mapping[int, JsonSchemaObject]] = {
    FieldDescriptor.TYPE_INT32: {"pattern": "^(0|-?[1-9][0-9]{0,8})$"},
    FieldDescriptor.TYPE_SINT32: {"pattern": "^(0|-?[1-9][0-9]{0,8})$"},
    FieldDescriptor.TYPE_SFIXED32: {"pattern": "^(0|-?[1-9][0-9]{0,8})$"},
    FieldDescriptor.TYPE_UINT32: {"pattern": "^(0|[1-9][0-9]{0,8})$"},
    FieldDescriptor.TYPE_FIXED32: {"pattern": "^(0|[1-9][0-9]{0,8})$"},
    FieldDescriptor.TYPE_INT64: {"pattern": "^(0|-?[1-9][0-9]{0,17})$"},
    FieldDescriptor.TYPE_SINT64: {"pattern": "^(0|-?[1-9][0-9]{0,17})$"},
    FieldDescriptor.TYPE_SFIXED64: {"pattern": "^(0|-?[1-9][0-9]{0,17})$"},
    FieldDescriptor.TYPE_UINT64: {"pattern": "^(0|[1-9][0-9]{0,17})$"},
    FieldDescriptor.TYPE_FIXED64: {"pattern": "^(0|[1-9][0-9]{0,17})$"},
    FieldDescriptor.TYPE_BOOL: {"enum": ["true", "false"]},
}
ANY: Final = "google.protobuf.Any"
VALUE: Final = "google.protobuf.Value"
VALUE_REFERENCE: Final[JsonSchemaObject] = {"$ref": f"#/$defs/{VALUE}"}
VALUE_SCHEMA: Final[JsonSchemaObject] = {
    "type": ["null", "boolean", "string", "number", "array", "object"],
    "minimum": -DOUBLE_MAX,
    "maximum": DOUBLE_MAX,
    "items": VALUE_REFERENCE,
    "additionalProperties": VALUE_REFERENCE,
}
FIELD_MASK_PATH: Final = r"[a-z][A-Za-z0-9]*(\.[a-z][A-Za-z0-9]*)*"
WELL_KNOWN: Final[Mapping[str, JsonSchemaObject]] = {
    "google.protobuf.Timestamp": {"type": "string", "format": "date-time"},
    "google.protobuf.Duration": {"type": "string", "format": "_duration"},
    "google.protobuf.FieldMask": {"type": "string", "pattern": f"^({FIELD_MASK_PATH}(,{FIELD_MASK_PATH})*)?$"},
}
STRUCTURED: Final[Mapping[str, JsonSchemaObject]] = {
    "google.protobuf.Struct": {"type": "object", "additionalProperties": VALUE_REFERENCE},
    "google.protobuf.ListValue": {"type": "array", "items": VALUE_REFERENCE},
    VALUE: VALUE_REFERENCE,
}
EMPTY_OBJECT: Final[JsonSchemaObject] = {"type": "object", "properties": {}, "additionalProperties": False}
WRAPPERS: Final[frozenset[str]] = frozenset(
    {
        "google.protobuf.DoubleValue",
        "google.protobuf.FloatValue",
        "google.protobuf.Int64Value",
        "google.protobuf.UInt64Value",
        "google.protobuf.Int32Value",
        "google.protobuf.UInt32Value",
        "google.protobuf.BoolValue",
        "google.protobuf.StringValue",
        "google.protobuf.BytesValue",
    }
)


@dataclass(slots=True)
class ConvertedMessage:
    """JSON Schema of a message in its JSON mapping."""

    schema: JsonSchemaObject
    # `google.protobuf.Any` values the schema leaves out: `<message>.<field>`, or the message name itself.
    any_fields: list[str]


def message_schema(descriptor: Descriptor) -> ConvertedMessage:
    converter = _Converter()
    name = descriptor.full_name
    schema: JsonSchemaObject | None
    if name == ANY or name in WRAPPERS or name in WELL_KNOWN or name in STRUCTURED:
        schema = converter.message_type(descriptor, name)
    else:
        schema = converter.message(descriptor)
        if name in converter.definitions:
            # The message contains itself, and its definition already holds the same schema.
            schema = {"$ref": f"#/$defs/{name}"}
    if schema is None:
        schema = deepclone(EMPTY_OBJECT)
    if converter.definitions:
        schema["$defs"] = converter.definitions
    return ConvertedMessage(schema=schema, any_fields=converter.any_fields)


class _Converter:
    __slots__ = ("definitions", "any_fields")

    def __init__(self) -> None:
        self.definitions: dict[str, JsonSchemaObject] = {}
        self.any_fields: list[str] = []

    def message(self, descriptor: Descriptor) -> JsonSchemaObject:
        properties: dict[str, JsonSchemaObject] = {}
        for member in descriptor.fields:
            property_schema = self.field(member, f"{descriptor.full_name}.{member.json_name}")
            if property_schema is None and member.is_required:
                # An empty value is the only one a skipped field can carry, and a required field needs one.
                property_schema = deepclone(EMPTY_OBJECT)
            if property_schema is not None:
                properties[member.json_name] = property_schema
        schema: JsonSchemaObject = {"type": "object", "properties": properties, "additionalProperties": False}
        required = [member.json_name for member in descriptor.fields if member.is_required]
        if required:
            schema["required"] = required
        # Single-member groups, including the synthetic ones behind proto3 `optional`, constrain nothing.
        groups = [[member.json_name for member in oneof.fields] for oneof in descriptor.oneofs if len(oneof.fields) > 1]
        if groups:
            schema["allOf"] = [
                {
                    "oneOf": [
                        *({"required": [name]} for name in names),
                        {"not": {"anyOf": [{"required": [name]} for name in names]}},
                    ]
                }
                for names in groups
            ]
        return schema

    def field(self, field: FieldDescriptor, name: str) -> JsonSchemaObject | None:
        message = field.message_type
        if message is not None and message.GetOptions().map_entry:
            values = self.value(message.fields_by_name["value"], name)
            if values is None:
                return None
            mapping: JsonSchemaObject = {"type": "object", "additionalProperties": values}
            key = message.fields_by_name["key"].type
            if key in MAP_KEYS:
                mapping["propertyNames"] = deepclone(MAP_KEYS[key])
            return mapping
        schema = self.value(field, name)
        if schema is None:
            return None
        if field.is_repeated:
            return {"type": "array", "items": schema}
        if message is not None and message.full_name in WRAPPERS and not field.is_required:
            # JSON `null` clears a singular field; a list, a map or a required field rejects it.
            schema["type"] = [schema["type"], "null"]
        return schema

    def value(self, field: FieldDescriptor, name: str) -> JsonSchemaObject | None:
        if field.message_type is None:
            return scalar(field)
        return self.message_type(field.message_type, name)

    def message_type(self, message: Descriptor, name: str) -> JsonSchemaObject | None:
        """Schema of a value of the `message` type; `None` when it is skipped and recorded as `name`."""
        full_name = message.full_name
        if full_name == ANY:
            self.any_fields.append(name)
            return None
        if full_name in WRAPPERS:
            return scalar(message.fields_by_name["value"])
        if full_name in WELL_KNOWN:
            return deepclone(WELL_KNOWN[full_name])
        if full_name in STRUCTURED:
            if VALUE not in self.definitions:
                self.definitions[VALUE] = deepclone(VALUE_SCHEMA)
            return deepclone(STRUCTURED[full_name])
        if full_name not in self.definitions:
            # Reserve the slot first, so a message that contains itself references it instead of recursing.
            self.definitions[full_name] = {}
            self.definitions[full_name] = self.message(message)
        return {"$ref": f"#/$defs/{full_name}"}


def scalar(field: FieldDescriptor) -> JsonSchemaObject:
    kind = field.type
    if kind in INTEGER_BOUNDS:
        minimum, maximum = INTEGER_BOUNDS[kind]
        return {"type": "integer", "minimum": minimum, "maximum": maximum}
    if kind == FieldDescriptor.TYPE_FLOAT:
        return {"type": "number", "minimum": -FLOAT32_MAX, "maximum": FLOAT32_MAX}
    # Larger values do not fit a double and fail to encode.
    if kind == FieldDescriptor.TYPE_DOUBLE:
        return {"type": "number", "minimum": -DOUBLE_MAX, "maximum": DOUBLE_MAX}
    if kind == FieldDescriptor.TYPE_BOOL:
        return {"type": "boolean"}
    if kind == FieldDescriptor.TYPE_BYTES:
        return {"type": "string", "format": "byte"}
    if field.enum_type is not None:
        return {"enum": [value.name for value in field.enum_type.values]}
    return {"type": "string"}
