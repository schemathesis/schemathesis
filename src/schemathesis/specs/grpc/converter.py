from __future__ import annotations

import base64
import struct
import sys
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from google.protobuf.descriptor import FieldDescriptor

from schemathesis.core.jsonschema.numeric import FLOAT32_MAX, INT32_RANGE, INT64_RANGE
from schemathesis.core.transforms import deepclone
from schemathesis.specs.grpc.constraints import ConstraintReader, FieldConstraints

if TYPE_CHECKING:
    from collections.abc import Mapping

    from google.protobuf.descriptor import Descriptor

    from schemathesis.core.jsonschema.types import JsonSchemaObject, JsonValue
    from schemathesis.specs.grpc.constraints import RuleValue

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
    converter = _Converter(ConstraintReader.from_pool(descriptor.file.pool))
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
    __slots__ = ("rules", "definitions", "any_fields")

    def __init__(self, rules: ConstraintReader | None) -> None:
        self.rules = rules
        self.definitions: dict[str, JsonSchemaObject] = {}
        self.any_fields: list[str] = []

    def message(self, descriptor: Descriptor) -> JsonSchemaObject:
        properties: dict[str, JsonSchemaObject] = {}
        required = []
        for member in descriptor.fields:
            constraints = self.rules.field(member) if self.rules is not None else None
            present = member.is_required or (constraints is not None and _forces_presence(member, constraints))
            property_schema = self.field(member, f"{descriptor.full_name}.{member.json_name}", constraints)
            if property_schema is None and member.is_repeated:
                # A list or a map of skipped values stays empty, which is the same as leaving it out.
                continue
            if property_schema is None and present:
                # An empty value is the only one a skipped field can carry, and a required field needs one.
                property_schema = deepclone(EMPTY_OBJECT)
            if property_schema is not None:
                properties[member.json_name] = property_schema
            if present:
                required.append(member.json_name)
        groups = []
        for oneof in descriptor.oneofs:
            names = [member.json_name for member in oneof.fields if member.json_name in properties]
            oneof_required = self.rules is not None and self.rules.oneof_required(oneof)
            if len(names) > 1:
                branches: list[JsonSchemaObject] = [{"required": [name]} for name in names]
                if not oneof_required:
                    branches.append({"not": {"anyOf": [{"required": [name]} for name in names]}})
                groups.append({"oneOf": branches})
            # Other single-member groups, including the synthetic ones behind proto3 `optional`, constrain nothing.
            elif oneof_required:
                required.extend(name for name in names if name not in required)
        schema: JsonSchemaObject = {"type": "object", "properties": properties, "additionalProperties": False}
        if required:
            schema["required"] = required
        if groups:
            schema["allOf"] = groups
        return schema

    def field(self, field: FieldDescriptor, name: str, constraints: FieldConstraints | None) -> JsonSchemaObject | None:
        message = field.message_type
        if message is not None and message.GetOptions().map_entry:
            value_field = message.fields_by_name["value"]
            values = self.value(value_field, name)
            if values is None:
                return None
            mapping: JsonSchemaObject = {"type": "object", "additionalProperties": values}
            key_field = message.fields_by_name["key"]
            if key_field.type in MAP_KEYS:
                mapping["propertyNames"] = deepclone(MAP_KEYS[key_field.type])
            if constraints is not None:
                if constraints.kind == "map":
                    _map_rules(mapping, key_field, value_field, constraints.rules)
                if constraints.required:
                    _at_least(mapping, "minProperties", 1)
            return mapping
        schema = self.value(field, name)
        if schema is None:
            return None
        if field.is_repeated:
            array: JsonSchemaObject = {"type": "array", "items": schema}
            if constraints is not None:
                if constraints.kind == "repeated":
                    _repeated_rules(array, field, constraints.rules)
                if constraints.required:
                    _at_least(array, "minItems", 1)
            return array
        if constraints is not None:
            _value_rules(schema, _scalar_field(field), constraints)
            if constraints.required and not field.has_presence:
                if field.type == FieldDescriptor.TYPE_FLOAT:
                    # Smaller values round to zero as 32-bit floats.
                    _add(schema, {"anyOf": [{"minimum": FLOAT32_TINY}, {"maximum": -FLOAT32_TINY}]})
                else:
                    _add(schema, {"not": {"const": _zero(field)}})
        required = field.is_required or (constraints is not None and constraints.required)
        if message is not None and message.full_name in WRAPPERS and not required:
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


NUMERIC_RULES: Final = frozenset(
    {
        "int32",
        "sint32",
        "sfixed32",
        "uint32",
        "fixed32",
        "int64",
        "sint64",
        "sfixed64",
        "uint64",
        "fixed64",
        "float",
        "double",
    }
)
# Well-known string formats in the validation rules and the values they admit.
STRING_FORMATS: Final[Mapping[str, JsonSchemaObject]] = {
    "email": {"format": "email"},
    "hostname": {"format": "hostname"},
    "ip": {"anyOf": [{"format": "ipv4"}, {"format": "ipv6"}]},
    "ipv4": {"format": "ipv4"},
    "ipv6": {"format": "ipv6"},
    "uri": {"format": "uri"},
    "uri_ref": {"format": "uri-reference"},
    "address": {"anyOf": [{"format": "hostname"}, {"format": "ipv4"}, {"format": "ipv6"}]},
    "uuid": {"format": "uuid"},
    "tuuid": {"pattern": "^[0-9a-fA-F]{32}$"},
}
REGEX_SYNTAX: Final = frozenset("\\^$.|?*+()[]{}")
# The standard alphabet, as command-line clients reject the URL-safe one.
BASE64_CHARACTER: Final = "[A-Za-z0-9+/]"
FLOAT32_TINY: Final = 1.401298464324817e-45
# Keys offered for an integer map key with rules, from the lowest allowed value up.
MAP_KEY_CHOICES: Final = 10
# Base64 endings of a value whose length leaves one or two bytes after the last full group of three.
BASE64_ENDINGS: Final = ("", f"{BASE64_CHARACTER}[AQgw]==", f"{BASE64_CHARACTER}{{2}}[AEIMQUYcgkosw048]=")


def _forces_presence(field: FieldDescriptor, constraints: FieldConstraints) -> bool:
    # Without presence, an omitted field carries its zero value, and the rules check it too.
    return constraints.required or (
        constraints.kind is not None and not constraints.ignore_empty and not field.has_presence
    )


def _scalar_field(field: FieldDescriptor) -> FieldDescriptor:
    """The field whose type the rules on `field` constrain: the wrapped value for wrapper types."""
    message = field.message_type
    if message is not None and message.full_name in WRAPPERS:
        return message.fields_by_name["value"]
    return field


def _zero(field: FieldDescriptor) -> JsonValue:
    if field.type in (FieldDescriptor.TYPE_STRING, FieldDescriptor.TYPE_BYTES):
        return ""
    if field.type == FieldDescriptor.TYPE_BOOL:
        return False
    if field.enum_type is not None:
        return field.enum_type.values_by_number[0].name
    return 0


def _value_rules(schema: JsonSchemaObject, field: FieldDescriptor, constraints: FieldConstraints) -> None:
    kind, rules = constraints.kind, constraints.rules
    if kind in NUMERIC_RULES:
        if field.type == FieldDescriptor.TYPE_FLOAT:
            rules = _float32_bounds(rules)
        _range(schema, rules)
        _choices(schema, rules)
    elif kind == "string":
        _string_rules(schema, rules)
        _choices(schema, rules)
    elif kind == "bytes":
        _bytes_rules(schema, rules)
        _choices(schema, _base64_values(rules))
    elif kind == "enum":
        _enum_rules(schema, field, rules)
    elif kind == "bool":
        _choices(schema, rules)


def _repeated_rules(array: JsonSchemaObject, field: FieldDescriptor, rules: dict[str, RuleValue]) -> None:
    items = rules.get("items")
    if isinstance(items, FieldConstraints):
        _value_rules(array["items"], _scalar_field(field), items)
    _at_least(array, "minItems", rules.get("min_items"))
    _at_most(array, "maxItems", rules.get("max_items"))
    if rules.get("unique"):
        array["uniqueItems"] = True


def _map_rules(
    mapping: JsonSchemaObject, key_field: FieldDescriptor, value_field: FieldDescriptor, rules: dict[str, RuleValue]
) -> None:
    keys = rules.get("keys")
    if isinstance(keys, FieldConstraints):
        if key_field.type == FieldDescriptor.TYPE_STRING:
            names: JsonSchemaObject = {}
            _value_rules(names, key_field, keys)
            mapping["propertyNames"] = names
        elif key_field.type in INTEGER_BOUNDS and keys.kind in NUMERIC_RULES:
            # Integer keys travel as strings, so the allowed values are listed.
            choices = _integer_choices(INTEGER_BOUNDS[key_field.type], keys.rules)
            if choices:
                mapping["propertyNames"] = {"enum": [str(value) for value in choices]}
    values = rules.get("values")
    if isinstance(values, FieldConstraints):
        _value_rules(mapping["additionalProperties"], _scalar_field(value_field), values)
    _at_least(mapping, "minProperties", rules.get("min_pairs"))
    _at_most(mapping, "maxProperties", rules.get("max_pairs"))


def _range(schema: JsonSchemaObject, rules: dict[str, RuleValue]) -> None:
    lower = _bound(rules, "gt", "exclusiveMinimum", "gte", "minimum")
    upper = _bound(rules, "lt", "exclusiveMaximum", "lte", "maximum")
    if lower is not None and upper is not None:
        (low, low_keyword), (high, high_keyword) = lower, upper
        exclusive = low_keyword.startswith("exclusive") or high_keyword.startswith("exclusive")
        if high < low or (high == low and exclusive):
            # An upper bound below the lower one admits the values outside the range.
            _add(schema, {"anyOf": [{low_keyword: low}, {high_keyword: high}]})
            return
    if lower is not None:
        value, keyword = lower
        if keyword == "minimum":
            _at_least(schema, keyword, value)
        else:
            _add(schema, {keyword: value})
    if upper is not None:
        value, keyword = upper
        if keyword == "maximum":
            _at_most(schema, keyword, value)
        else:
            _add(schema, {keyword: value})


def _bound(
    rules: dict[str, RuleValue], exclusive: str, exclusive_keyword: str, inclusive: str, inclusive_keyword: str
) -> tuple[float, str] | None:
    value = rules.get(exclusive)
    if isinstance(value, (int, float)):
        return value, exclusive_keyword
    value = rules.get(inclusive)
    if isinstance(value, (int, float)):
        return value, inclusive_keyword
    return None


def _float32_bounds(rules: dict[str, RuleValue]) -> dict[str, RuleValue]:
    """Exclusive bounds as the nearest 32-bit floats inside them, as values between round onto the bound."""
    rules = dict(rules)
    for exclusive, inclusive, upward in (("gt", "gte", True), ("lt", "lte", False)):
        value = rules.pop(exclusive, None)
        if isinstance(value, (int, float)):
            rules[inclusive] = _next_float32(float(value), upward)
    return rules


def _next_float32(value: float, upward: bool) -> float:
    if value == 0:
        return FLOAT32_TINY if upward else -FLOAT32_TINY
    (bits,) = struct.unpack("<i", struct.pack("<f", value))
    # Magnitude grows with the bits for both signs.
    bits += 1 if (value > 0) == upward else -1
    return struct.unpack("<f", struct.pack("<i", bits))[0]


def _integer_choices(bounds: tuple[int, int], rules: dict[str, RuleValue]) -> list[int]:
    low, high = bounds
    allowed = rules.get("in")
    excluded = rules.get("not_in", [])
    gt, gte, lt, lte = (rules.get(name) for name in ("gt", "gte", "lt", "lte"))
    if isinstance(gt, int):
        low = max(low, gt + 1)
    if isinstance(gte, int):
        low = max(low, gte)
    if isinstance(lt, int):
        high = min(high, lt - 1)
    if isinstance(lte, int):
        high = min(high, lte)
    candidates = allowed if isinstance(allowed, list) else range(low, min(high, low + MAP_KEY_CHOICES - 1) + 1)
    return [
        value
        for value in candidates
        if isinstance(value, int) and low <= value <= high and not (isinstance(excluded, list) and value in excluded)
    ]


def _base64_values(rules: dict[str, RuleValue]) -> dict[str, RuleValue]:
    """`const`, `in` and `not_in` byte values as the base64 text that carries them."""
    encoded: dict[str, RuleValue] = {}
    for name in ("const", "in", "not_in"):
        value = rules.get(name)
        if isinstance(value, bytes):
            encoded[name] = base64.b64encode(value).decode()
        elif isinstance(value, list):
            encoded[name] = [base64.b64encode(item).decode() for item in value if isinstance(item, bytes)]
    return encoded


def _choices(schema: JsonSchemaObject, rules: dict[str, RuleValue]) -> None:
    if "const" in rules:
        _add(schema, {"const": rules["const"]})
    if "in" in rules:
        _add(schema, {"enum": rules["in"]})
    if "not_in" in rules:
        _add(schema, {"not": {"enum": rules["not_in"]}})


def _string_rules(schema: JsonSchemaObject, rules: dict[str, RuleValue]) -> None:
    length = rules.get("len")
    _at_least(schema, "minLength", length)
    _at_most(schema, "maxLength", length)
    _at_least(schema, "minLength", rules.get("min_len"))
    _at_most(schema, "maxLength", rules.get("max_len"))
    # A character takes one to four bytes in UTF-8.
    _at_least(schema, "minLength", rules.get("min_bytes"))
    max_bytes = rules.get("max_bytes")
    if isinstance(max_bytes, int):
        _at_most(schema, "maxLength", max_bytes // 4)
    pattern = rules.get("pattern")
    if isinstance(pattern, str):
        _add(schema, {"pattern": pattern})
    # One pattern for all three, as separate ones leave generation to filter for their overlap.
    parts = [
        template.format(_literal(value))
        for name, template in (("prefix", "^{}"), ("contains", "{}"), ("suffix", "{}$"))
        if isinstance(value := rules.get(name), str)
    ]
    if parts:
        _add(schema, {"pattern": ".*".join(parts)})
    not_contains = rules.get("not_contains")
    if isinstance(not_contains, str):
        _add(schema, {"not": {"pattern": _literal(not_contains)}})
    for name, subschema in STRING_FORMATS.items():
        if rules.get(name):
            _add(schema, deepclone(subschema))


def _bytes_rules(schema: JsonSchemaObject, rules: dict[str, RuleValue]) -> None:
    length = rules.get("len")
    minimum = length if isinstance(length, int) else rules.get("min_len", 0)
    maximum = length if isinstance(length, int) else rules.get("max_len")
    if not isinstance(minimum, int) or (minimum == 0 and not isinstance(maximum, int)):
        return
    # Base64 text with a pattern on it, as a length on the text cannot count the bytes it encodes.
    del schema["format"]
    schema["pattern"] = _base64_pattern(minimum, maximum if isinstance(maximum, int) else None)


def _base64_pattern(minimum: int, maximum: int | None) -> str:
    """Base64 values that decode to `minimum` to `maximum` bytes."""
    branches = []
    for remainder, ending in enumerate(BASE64_ENDINGS):
        # Full groups of three bytes before the ending.
        low = max(0, -(-(minimum - remainder) // 3))
        if maximum is None:
            groups = f"(?:{BASE64_CHARACTER}{{4}}){{{low},}}"
        else:
            high = (maximum - remainder) // 3
            if high < low:
                continue
            if low == high:
                groups = f"{BASE64_CHARACTER}{{{4 * low}}}" if low else ""
            else:
                groups = f"(?:{BASE64_CHARACTER}{{4}}){{{low},{high}}}"
        branches.append(groups + ending)
    if len(branches) == 1:
        return f"^{branches[0]}$"
    return f"^(?:{'|'.join(branches)})$"


def _enum_rules(schema: JsonSchemaObject, field: FieldDescriptor, rules: dict[str, RuleValue]) -> None:
    # Only declared values are generated, so `defined_only` holds already.
    const = rules.get("const")
    allowed = rules.get("in")
    excluded = rules.get("not_in", [])
    assert field.enum_type is not None
    schema["enum"] = [
        value.name
        for value in field.enum_type.values
        if (const is None or value.number == const)
        and (not isinstance(allowed, list) or value.number in allowed)
        and (not isinstance(excluded, list) or value.number not in excluded)
    ]


def _literal(text: str) -> str:
    """A pattern matching `text` literally."""
    return "".join(f"\\{character}" if character in REGEX_SYNTAX else character for character in text)


def _at_least(schema: JsonSchemaObject, keyword: str, value: RuleValue | None) -> None:
    if isinstance(value, (int, float)):
        current = schema.get(keyword)
        schema[keyword] = max(current, value) if isinstance(current, (int, float)) else value


def _at_most(schema: JsonSchemaObject, keyword: str, value: RuleValue | None) -> None:
    if isinstance(value, (int, float)):
        current = schema.get(keyword)
        schema[keyword] = min(current, value) if isinstance(current, (int, float)) else value


def _add(schema: JsonSchemaObject, subschema: JsonSchemaObject) -> None:
    """Constrain `schema` by `subschema` too."""
    for keyword, value in subschema.items():
        if keyword in schema:
            schema.setdefault("allOf", []).append({keyword: value})
        else:
            schema[keyword] = value
