from __future__ import annotations

import base64
import math
import re
import sys
from dataclasses import dataclass, replace
from functools import lru_cache
from typing import TYPE_CHECKING, Final, cast

from google.protobuf import json_format, message_factory
from google.protobuf.descriptor import FieldDescriptor

from schemathesis.core import NotSet
from schemathesis.core.jsonschema.numeric import FLOAT32_MAX, INT32_RANGE, INT64_RANGE, next_float32
from schemathesis.core.transforms import deepclone
from schemathesis.specs.grpc.constraints import ConstraintReader, FieldConstraints

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from google.protobuf import any_pb2
    from google.protobuf.descriptor import Descriptor, EnumDescriptor
    from google.protobuf.descriptor_pool import DescriptorPool
    from google.protobuf.message import Message

    from schemathesis.core import Body
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


def is_well_known(name: str) -> bool:
    """Whether the message has its own JSON form rather than an object of its fields."""
    return name == ANY or name in WRAPPERS or name in WELL_KNOWN or name in STRUCTURED


def message_schema(descriptor: Descriptor) -> ConvertedMessage:
    converter = _Converter(ConstraintReader.from_pool(descriptor.file.pool))
    name = descriptor.full_name
    schema: JsonSchemaObject | None
    if is_well_known(name):
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
        message_oneofs = self.rules.message_oneofs(descriptor) if self.rules is not None else []
        message_oneof_members = {name for fields, _ in message_oneofs for name in fields}
        for member in descriptor.fields:
            constraints = self.rules.field(member) if self.rules is not None else None
            if constraints is not None and member.name in message_oneof_members:
                # The rules of a message-level oneof member hold only for the member that is set.
                constraints = replace(constraints, ignore_empty=True)
            present = member.is_required or (constraints is not None and _forces_presence(member, constraints))
            property_schema = self.field(member, f"{descriptor.full_name}.{member.json_name}", constraints)
            if property_schema is None and member.is_repeated:
                # A list or a map of skipped values stays empty, which is the same as leaving it out.
                continue
            if property_schema is None and present:
                # An empty value is the only one a skipped field can carry, and a required field needs one.
                property_schema = deepclone(EMPTY_OBJECT)
            if property_schema is not None:
                if member.name in message_oneof_members:
                    # A member in the body counts towards its oneof, so it holds a value that leaves it set.
                    require_set(property_schema, member)
                properties[member.json_name] = property_schema
            if present:
                required.append(member.json_name)
        oneofs = [
            ([member.json_name for member in oneof.fields], self.rules is not None and self.rules.oneof_required(oneof))
            for oneof in descriptor.oneofs
        ]
        oneofs.extend(
            ([descriptor.fields_by_name[name].json_name for name in fields], oneof_required)
            for fields, oneof_required in message_oneofs
        )
        groups = []
        for members, oneof_required in oneofs:
            names = [name for name in members if name in properties]
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
        entry = map_entry(field)
        if entry is not None:
            return self._map(field, entry, name, constraints)
        schema = self.value(field, name)
        if schema is None:
            return None
        if field.is_repeated:
            array: JsonSchemaObject = {"type": "array", "items": schema}
            if constraints is not None:
                if constraints.kind == "repeated":
                    _repeated_rules(array, field, constraints.rules)
                if constraints.required:
                    require_set(array, field)
            return array
        if constraints is not None:
            _value_rules(schema, _scalar_field(field), constraints)
            if constraints.required and not field.has_presence:
                require_set(schema, field)
        required = field.is_required or (constraints is not None and constraints.required)
        message = field.message_type
        if message is not None and message.full_name in WRAPPERS and not required and field.containing_oneof is None:
            # JSON `null` clears a singular field; a list, a map or a required field rejects it.
            # A oneof member set to `null` leaves its oneof unset, and command-line clients fail to send it.
            schema["type"] = [schema["type"], "null"]
        return schema

    def _map(
        self, field: FieldDescriptor, entry: Descriptor, name: str, constraints: FieldConstraints | None
    ) -> JsonSchemaObject | None:
        value_field = entry.fields_by_name["value"]
        values = self.value(value_field, name)
        if values is None:
            return None
        mapping: JsonSchemaObject = {"type": "object", "additionalProperties": values}
        key_field = entry.fields_by_name["key"]
        if key_field.type in MAP_KEYS:
            mapping["propertyNames"] = deepclone(MAP_KEYS[key_field.type])
        if constraints is not None:
            _map_constraints(mapping, field, key_field, value_field, constraints)
        return mapping

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


def require_set(schema: JsonSchemaObject, field: FieldDescriptor) -> None:
    """Constrain `schema` of `field` to values that leave the field set."""
    if field.is_repeated:
        # Maps are JSON objects, lists are arrays; both are unset when empty.
        _at_least(schema, "minProperties" if schema.get("type") == "object" else "minItems", 1)
    elif field.has_presence:
        # JSON `null` clears a field that tracks presence.
        _add(schema, {"not": {"const": None}})
    elif field.type == FieldDescriptor.TYPE_FLOAT:
        # Smaller values round to zero as 32-bit floats.
        _add(schema, {"anyOf": [{"minimum": FLOAT32_TINY}, {"maximum": -FLOAT32_TINY}]})
    else:
        _add(schema, {"not": {"const": _zero(field)}})


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
        return {"enum": list(_enum_names(field.enum_type).values())}
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
NON_ASCII: Final = "[^\\x00-\\x7f]"
# Validation rule patterns use RE2 syntax; these are the ASCII ranges of its POSIX classes.
POSIX_CLASSES: Final[Mapping[str, str]] = {
    "alnum": "0-9A-Za-z",
    "alpha": "A-Za-z",
    "ascii": "\\x00-\\x7f",
    "blank": "\\t ",
    "cntrl": "\\x00-\\x1f\\x7f",
    "digit": "0-9",
    "graph": "!-~",
    "lower": "a-z",
    "print": " -~",
    "punct": "!-/:-@\\[-`{-~",
    "space": "\\t\\n\\v\\f\\r ",
    "upper": "A-Z",
    "word": "0-9A-Za-z_",
    "xdigit": "0-9A-Fa-f",
}
POSIX_CLASS: Final = re.compile(rf"\[:(\^?)({'|'.join(POSIX_CLASSES)}):\]")
# RE2 flags hold from where they stand to the end of the enclosing group.
INLINE_FLAGS: Final = re.compile(r"\(\?([imsU-]+)\)")
# RE2 whitespace, which leaves out the vertical tab.
RE2_SPACE: Final = "\\t\\n\\f\\r "
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
        return _enum_names(field.enum_type)[0]
    return 0


def _enum_names(enum: EnumDescriptor) -> dict[int, str]:
    """Names of the values of `enum` by number; aliases encode the same number, so only the first name is kept."""
    names: dict[int, str] = {}
    for value in enum.values:
        names.setdefault(value.number, value.name)
    return names


def _value_rules(schema: JsonSchemaObject, field: FieldDescriptor, constraints: FieldConstraints) -> None:
    kind, rules = constraints.kind, constraints.rules
    if kind in NUMERIC_RULES:
        if field.type == FieldDescriptor.TYPE_FLOAT:
            rules = _float32_bounds(rules)
            excluded = rules.pop("not_in", None)
            if isinstance(excluded, list):
                # A number that rounds onto an excluded 32-bit float is sent as that float.
                # Non-finite values lie outside the generated range already.
                rounded = [
                    _float32_rounding(value) for value in excluded if isinstance(value, float) and math.isfinite(value)
                ]
                if rounded:
                    _add(schema, {"not": {"anyOf": rounded}})
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


def _map_constraints(
    mapping: JsonSchemaObject,
    field: FieldDescriptor,
    key_field: FieldDescriptor,
    value_field: FieldDescriptor,
    constraints: FieldConstraints,
) -> None:
    if constraints.kind == "map":
        _map_rules(mapping, key_field, value_field, constraints.rules)
    if constraints.required:
        require_set(mapping, field)


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
            rules[inclusive] = next_float32(value, going_up=upward)
    return rules


def _float32_rounding(value: float) -> JsonSchemaObject:
    """Numbers that round to the 32-bit float `value`, with the halfway points on both sides."""
    return {
        "minimum": max((next_float32(value, going_up=False) + value) / 2, -FLOAT32_MAX),
        "maximum": min((next_float32(value, going_up=True) + value) / 2, FLOAT32_MAX),
    }


def _integer_choices(bounds: tuple[int, int], rules: dict[str, RuleValue]) -> list[int]:
    minimum, maximum = bounds
    const = rules.get("const")
    allowed = [const] if isinstance(const, int) else rules.get("in")
    excluded = rules.get("not_in", [])
    gt, gte, lt, lte = (rules.get(name) for name in ("gt", "gte", "lt", "lte"))
    low = gt + 1 if isinstance(gt, int) else gte if isinstance(gte, int) else None
    high = lt - 1 if isinstance(lt, int) else lte if isinstance(lte, int) else None
    if low is not None and high is not None and low > high:
        # An upper bound below the lower one admits the values outside the range.
        ranges = [(minimum, high), (low, maximum)]
    else:
        ranges = [(minimum if low is None else max(minimum, low), maximum if high is None else min(maximum, high))]
    candidates = (
        allowed
        if isinstance(allowed, list)
        else [value for start, end in ranges for value in range(start, min(end, start + MAP_KEY_CHOICES - 1) + 1)]
    )
    return [
        value
        for value in candidates
        if isinstance(value, int)
        and any(start <= value <= end for start, end in ranges)
        and not (isinstance(excluded, list) and value in excluded)
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
        if max_bytes // 4 >= cast("int", schema.get("minLength", 0)):
            _at_most(schema, "maxLength", max_bytes // 4)
        else:
            # Too few characters for the minimum if each may take four bytes; one-byte characters leave room.
            _at_most(schema, "maxLength", max_bytes)
            _add(schema, {"not": {"pattern": NON_ASCII}})
    pattern = rules.get("pattern")
    if isinstance(pattern, str):
        _add(schema, {"pattern": _re2_pattern(pattern)})
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
    del schema["format"]
    branches = _base64_branches(minimum, maximum if isinstance(maximum, int) else None)
    if len(branches) == 1:
        schema.update(branches[0])
    elif branches:
        schema["anyOf"] = branches
    else:
        # No byte count fits both bounds.
        schema["not"] = {}


def _base64_branches(minimum: int, maximum: int | None) -> list[JsonSchemaObject]:
    """Base64 values that decode to `minimum` to `maximum` bytes, one branch per ending.

    Within a branch the text length counts the bytes, so the length keywords bound them; repetition counts in
    the pattern would compile to one copy per repetition.
    """
    branches = []
    for remainder, ending in enumerate(BASE64_ENDINGS):
        # Full groups of three bytes before the ending, which takes four characters of its own.
        low = max(0, -(-(minimum - remainder) // 3))
        tail = 4 if remainder else 0
        branch: JsonSchemaObject = {"pattern": f"^(?:{BASE64_CHARACTER}{{4}})*{ending}$"}
        if low or tail:
            branch["minLength"] = 4 * low + tail
        if maximum is not None:
            high = (maximum - remainder) // 3
            if high < low:
                continue
            branch["maxLength"] = 4 * high + tail
        branches.append(branch)
    return branches


def _enum_rules(schema: JsonSchemaObject, field: FieldDescriptor, rules: dict[str, RuleValue]) -> None:
    # Only declared values are generated, so `defined_only` holds already.
    const = rules.get("const")
    allowed = rules.get("in")
    excluded = rules.get("not_in", [])
    assert field.enum_type is not None
    schema["enum"] = [
        name
        for number, name in _enum_names(field.enum_type).items()
        if (const is None or number == const)
        and (not isinstance(allowed, list) or number in allowed)
        and (not isinstance(excluded, list) or number not in excluded)
    ]


def _literal(text: str) -> str:
    """A pattern matching `text` literally."""
    return "".join(f"\\{character}" if character in REGEX_SYNTAX else character for character in text)


def _re2_pattern(pattern: str) -> str:
    """An RE2 `pattern` in the syntax of JSON Schema patterns; anything without a translation stays as written."""
    branches, end = _re2_branches(pattern, 0)
    # A stray closing parenthesis keeps the pattern invalid, so the error still names it.
    return "|".join(branches) + pattern[end:]


def _re2_branches(pattern: str, index: int) -> tuple[list[str], int]:
    """Alternatives from `index` up to the parenthesis closing their group, and the index where they stop."""
    branches = [""]
    while index < len(pattern) and pattern[index] != ")":
        if pattern[index] == "|":
            branches.append("")
            index += 1
        elif (flags := INLINE_FLAGS.match(pattern, index)) is not None:
            # Bare flags are valid only at the start of a pattern, so every alternative they reach gets a scoped group.
            rest, index = _re2_branches(pattern, flags.end())
            scoped = [f"(?{flags.group(1)}:{branch})" for branch in rest]
            branches[-1] += scoped[0]
            branches.extend(scoped[1:])
        elif pattern[index] == "(":
            inner, index = _re2_branches(pattern, index + 1)
            branches[-1] += f"({'|'.join(inner)}{pattern[index : index + 1]}"
            index += 1
        else:
            atom, index = _re2_atom(pattern, index)
            branches[-1] += atom
    return branches, index


def _re2_atom(pattern: str, index: int) -> tuple[str, int]:
    """One escape, character class or character at `index`, and the index after it."""
    if pattern.startswith("\\Q", index):
        end = pattern.find("\\E", index + 2)
        end = len(pattern) if end == -1 else end
        return _literal(pattern[index + 2 : end]), end + 2
    if pattern.startswith("\\s", index):
        return f"[{RE2_SPACE}]", index + 2
    if pattern.startswith("\\z", index):
        return "$", index + 2
    if pattern[index] == "\\":
        return pattern[index : index + 2], index + 2
    if pattern[index] == "[":
        return _re2_class(pattern, index)
    return pattern[index], index + 1


def _re2_class(pattern: str, index: int) -> tuple[str, int]:
    r"""The character class opening at `index`, with POSIX classes and `\s` spelled out, and the index after it."""
    negated = pattern.startswith("[^", index)
    index += 2 if negated else 1
    members: list[str] = []
    while index < len(pattern) and pattern[index] != "]":
        posix = POSIX_CLASS.match(pattern, index)
        # A negated POSIX class has a range to stand for it only when it is the whole class.
        if posix is not None and (not posix.group(1) or (not members and pattern.startswith("]", posix.end()))):
            members.append(POSIX_CLASSES[posix.group(2)])
            negated ^= bool(posix.group(1))
            index = posix.end()
        elif pattern.startswith("\\s", index):
            members.append(RE2_SPACE)
            index += 2
        elif pattern[index] == "\\":
            members.append(pattern[index : index + 2])
            index += 2
        else:
            members.append(pattern[index])
            index += 1
    return f"{'[^' if negated else '['}{''.join(members)}{pattern[index : index + 1]}", index + 1


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


# Python's protobuf parser decodes bytes only in the URL-safe alphabet; generated values use the standard one.
URL_SAFE_ALPHABET = str.maketrans("+/", "-_")
# Type name the JSON body walker passes for `bytes` values.
BYTES = "bytes"
# The JSON parser rejects messages nested deeper than this.
MAX_MESSAGE_DEPTH = 100


def request_message(body: object, input_type: Descriptor) -> Message:
    """The message a JSON request body describes."""
    request = message_factory.GetMessageClass(input_type)()
    # Well-known messages such as wrappers map to any JSON value, not only to objects.
    return json_format.ParseDict(
        cast("dict[str, object]", rewrite_json(body, input_type, _url_safe)),
        request,
        descriptor_pool=input_type.file.pool,
    )


def body_message(body: Body | NotSet, input_type: Descriptor) -> Message:
    """The message a request body describes, whether JSON or already serialized."""
    if isinstance(body, bytes):
        return message_factory.GetMessageClass(input_type).FromString(body)
    return request_message({} if isinstance(body, NotSet) else body, input_type)


def message_to_json(message: Message, pool: DescriptorPool) -> JsonValue:
    """`message` in its JSON form, with `Any` values of types `pool` lacks kept as raw bytes."""
    try:
        return json_format.MessageToDict(message, descriptor_pool=pool)
    except TypeError:
        # Only an `Any` whose type the pool lacks fails to render.
        pass
    if message.DESCRIPTOR.full_name == ANY:
        packed = cast("any_pb2.Any", message)
        return {"@type": packed.type_url, "value": base64.b64encode(packed.value).decode()}
    rest = type(message)()
    rest.CopyFrom(message)
    nested: dict[str, JsonValue] = {}
    for field, value in message.ListFields():
        item_type = field.message_type
        is_map = False
        if item_type is not None and item_type.GetOptions().map_entry:
            is_map = True
            item_type = item_type.fields_by_name["value"].message_type
        # Extensions render with the plain fields, as only regular fields can be cleared by name.
        if item_type is None or field.is_extension:
            continue
        if is_map:
            # JSON spells boolean map keys in lowercase.
            nested[field.json_name] = {
                str(key).lower() if isinstance(key, bool) else str(key): message_to_json(item, pool)
                for key, item in value.items()
            }
        elif field.is_repeated:
            nested[field.json_name] = [message_to_json(item, pool) for item in value]
        else:
            nested[field.json_name] = message_to_json(value, pool)
        rest.ClearField(field.name)
    return {**json_format.MessageToDict(rest, descriptor_pool=pool), **nested}


def _url_safe(value: object, type_name: str) -> object:
    if type_name == BYTES and isinstance(value, str):
        return value.translate(URL_SAFE_ALPHABET)
    return value


def rewrite_json(
    value: object, message_type: Descriptor, leaf: Callable[[object, str], object], depth: int = 1
) -> object:
    """Apply `leaf` to every `bytes` value and well-known message in a JSON body, passing its type name."""
    name = message_type.full_name
    # Deeper bodies fail to parse anyway, so the parser reports them rather than the walk.
    if depth > MAX_MESSAGE_DEPTH:
        return value
    if name == ANY:
        return _rewrite_any(value, message_type, leaf, depth)
    if name in WRAPPERS:
        return _rewrite_field(value, message_type.fields_by_name["value"], leaf, depth)
    if is_well_known(name):
        return leaf(value, name)
    if not isinstance(value, dict):
        return value
    fields = json_fields(message_type)
    rewritten = {}
    for key, item in value.items():
        member = fields.get(key) or _extension(message_type, key)
        nested = None if member is None else member.message_type
        if member is None:
            rewritten[key] = item
        elif nested is not None and nested.GetOptions().map_entry and isinstance(item, dict):
            values = nested.fields_by_name["value"]
            rewritten[key] = {name: _rewrite_field(entry, values, leaf, depth) for name, entry in item.items()}
        elif member.is_repeated and isinstance(item, list):
            rewritten[key] = [_rewrite_field(entry, member, leaf, depth) for entry in item]
        else:
            rewritten[key] = _rewrite_field(item, member, leaf, depth)
    return rewritten


def map_entry(field: FieldDescriptor) -> Descriptor | None:
    """The synthetic entry message of a `map` field."""
    message = field.message_type
    if message is not None and message.GetOptions().map_entry:
        return message
    return None


@lru_cache(maxsize=1024)
def json_fields(message_type: Descriptor) -> dict[str, FieldDescriptor]:
    """Fields by both of the names a JSON body may use for them."""
    return {**message_type.fields_by_name, **{member.json_name: member for member in message_type.fields}}


def _rewrite_field(value: object, member: FieldDescriptor, leaf: Callable[[object, str], object], depth: int) -> object:
    if member.message_type is not None:
        return rewrite_json(value, member.message_type, leaf, depth + 1)
    if member.type == FieldDescriptor.TYPE_BYTES:
        return leaf(value, BYTES)
    return value


def _rewrite_any(value: object, any_type: Descriptor, leaf: Callable[[object, str], object], depth: int) -> object:
    if not isinstance(value, dict) or not isinstance(value.get("@type"), str):
        return value
    try:
        packed = any_type.file.pool.FindMessageTypeByName(value["@type"].rsplit("/", 1)[-1])
    except KeyError:
        return value
    # A packed well-known message keeps its own JSON form under `value`.
    if is_well_known(packed.full_name):
        return {
            key: rewrite_json(item, packed, leaf, depth + 1) if key == "value" else item for key, item in value.items()
        }
    return rewrite_json(value, packed, leaf, depth + 1)


def _extension(message_type: Descriptor, key: str) -> FieldDescriptor | None:
    """The extension a `[full.name]` JSON key sets."""
    if not (key.startswith("[") and key.endswith("]")):
        return None
    try:
        return message_type.file.pool.FindExtensionByName(key[1:-1])
    except KeyError:
        return None
