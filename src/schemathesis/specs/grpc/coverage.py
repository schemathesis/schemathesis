"""Deterministic structural cases for gRPC methods: empty and missing nested messages, oneof branches and bounds."""

from __future__ import annotations

import itertools
import math
from collections import deque
from collections.abc import Iterator
from typing import TYPE_CHECKING, Final, cast

import jsonschema_rs
from google.protobuf import json_format, message_factory
from google.protobuf.descriptor import FieldDescriptor

from schemathesis.core.cache import MISSING
from schemathesis.core.jsonschema import make_validator_for
from schemathesis.core.jsonschema.numeric import resolve_inclusive_bounds
from schemathesis.core.parameters import ParameterLocation
from schemathesis.core.timing import Instant
from schemathesis.core.transforms import deepclone
from schemathesis.generation import GenerationMode
from schemathesis.generation.hypothesis.examples import generate_one
from schemathesis.generation.jsonschema.builder import build
from schemathesis.generation.jsonschema.context import Alphabet
from schemathesis.generation.meta import CoverageScenario, PhaseInfo, uniform_metadata
from schemathesis.specs.grpc.constraints import ConstraintReader
from schemathesis.specs.grpc.converter import is_well_known, message_schema
from schemathesis.specs.grpc.formats import grpc_formats

if TYPE_CHECKING:
    from google.protobuf.descriptor import Descriptor

    from schemathesis.config import GenerationConfig
    from schemathesis.core.jsonschema.types import JsonSchemaObject, JsonValue
    from schemathesis.generation.case import Case
    from schemathesis.schemas import APIOperation

# Nesting levels of messages that cases reach.
MAX_DEPTH: Final = 3
# Wide messages reach thousands of positions; shallow fields come first, so the cap drops the deepest ones.
MAX_CASES: Final = 100
# Zero values of string, integer and boolean map keys, which travel as strings.
KEYS: Final = ("", "0", "false")
# Zero values of each JSON type.
ZEROS: Final[tuple[JsonValue, ...]] = (*KEYS, 0, False, [], {}, None)
# Fields from the message root to a field; a repeated field on the way holds a single item.
FieldPath = tuple[FieldDescriptor, ...]
# The value to send, and the value checked against the rules in its place.
Variant = tuple[CoverageScenario, str, "JsonValue", "JsonValue"]
Body = tuple[CoverageScenario, str, str | None, "JsonValue", "JsonValue"]


def iter_coverage_cases(
    operation: APIOperation, modes: list[GenerationMode], generation: GenerationConfig
) -> Iterator[Case]:
    if GenerationMode.POSITIVE not in modes:
        return
    root = operation.definition.raw.input_type
    values = _Values(
        message_schema(root).schema,
        Alphabet(allow_x00=generation.allow_x00, codec=generation.codec),
        ConstraintReader.from_pool(root.file.pool),
    )
    message_class = message_factory.GetMessageClass(root)
    # Different JSON bodies often encode to the same bytes, as default values of fields without presence vanish.
    seen: set[bytes] = set()
    instant = Instant()
    base = values.smallest(values.schema)
    full = values.full(root, values.schema, 1, (), frozenset())
    groups: list[Iterator[Body]] = [
        iter([(CoverageScenario.DEFAULT_POSITIVE_TEST, "Default positive test case", None, base, base)]),
        iter([(CoverageScenario.VALID_OBJECT, "Every nested message empty", None, full, full)]),
        *(values.bodies(root, base, path, schemas) for path, schemas in values.paths(root)),
    ]

    def accept(
        scenario: CoverageScenario, description: str, parameter: str | None, body: JsonValue, checked: JsonValue
    ) -> Case | None:
        if not values.is_valid(values.schema, checked):
            return None
        encoded = json_format.ParseDict(
            cast("dict", body), message_class(), descriptor_pool=root.file.pool
        ).SerializeToString(deterministic=True)
        if encoded in seen:
            return None
        seen.add(encoded)
        return operation.Case(
            body=deepclone(body),
            _meta=uniform_metadata(
                mode=GenerationMode.POSITIVE,
                phase=PhaseInfo.coverage(
                    scenario, description, root.full_name, parameter, parameter_location=ParameterLocation.BODY
                ),
                generation_time=instant.elapsed,
            ),
        )

    # One case per field first, so the bounds of wide messages do not crowd out the fields after them.
    for exhaust in (False, True):
        for group in groups:
            for candidate in group:
                case = accept(*candidate)
                if case is None:
                    continue
                yield case
                if len(seen) == MAX_CASES:
                    return
                instant = Instant()
                if not exhaust:
                    break


class _Values:
    """Values that the JSON Schema of a message admits, so they also satisfy its validation rules."""

    __slots__ = ("schema", "definitions", "alphabet", "constraints", "validators", "smallest_values")

    def __init__(self, schema: JsonSchemaObject, alphabet: Alphabet, constraints: ConstraintReader | None) -> None:
        self.schema = schema
        self.definitions: JsonSchemaObject = schema.get("$defs", {})
        self.alphabet = alphabet
        self.constraints = constraints
        # Keyed by the identity of subschemas of `schema`, which live as long as this object.
        self.validators: dict[int, jsonschema_rs.Validator] = {}
        self.smallest_values: dict[int, JsonValue] = {}

    def resolve(self, schema: JsonSchemaObject) -> JsonSchemaObject:
        reference = schema.get("$ref")
        if isinstance(reference, str):
            return self.definitions[reference.rsplit("/", 1)[1]]
        return schema

    def is_valid(self, schema: JsonSchemaObject, value: JsonValue) -> bool:
        validator = self.validators.get(id(schema))
        if validator is None:
            validator = make_validator_for({**schema, "$defs": self.definitions})
            self.validators[id(schema)] = validator
        return validator.is_valid(value)

    def smallest(self, schema: JsonSchemaObject) -> JsonValue:
        """A zero or the first allowed value, or one generated from the schema when none of them fits."""
        key = id(schema)
        if key not in self.smallest_values:
            value = next((value for value in self._candidates(schema) if self.is_valid(schema, value)), MISSING)
            if value is MISSING:
                value = generate_one(
                    build(
                        {**schema, "$defs": self.definitions},
                        draft=jsonschema_rs.Draft202012,
                        formats=grpc_formats(),
                        alphabet=self.alphabet,
                    )
                )
            self.smallest_values[key] = cast("JsonValue", value)
        return self.smallest_values[key]

    def _candidates(self, schema: JsonSchemaObject) -> Iterator[JsonValue]:
        target = self.resolve(schema)
        if "const" in target:
            yield target["const"]
        yield from target.get("enum", ())
        if not _has_format(target):
            yield from ZEROS
        yield from _bounds(target)
        if "properties" in target:
            yield {name: self.smallest(target["properties"][name]) for name in _required(target)}
        elif "items" in target:
            yield [self.smallest(target["items"])]
        elif "additionalProperties" in target:
            yield from self._entries(target)

    def _entries(self, mapping: JsonSchemaObject) -> list[JsonValue]:
        """Maps with one entry, under each key that a map key may fall back to."""
        names = mapping.get("propertyNames", {})
        value = self.smallest(mapping["additionalProperties"])
        return [{key: value} for key in (*names.get("enum", ()), *KEYS)]

    def full(
        self, descriptor: Descriptor, schema: JsonSchemaObject, depth: int, excluded: FieldPath, outer: frozenset[str]
    ) -> JsonValue:
        """The smallest body with every nested message present, except the one at `excluded`.

        A message inside itself stays at its smallest value, so recursive messages do not multiply.
        """
        value = self.smallest(schema)
        if is_well_known(descriptor.full_name) or depth > MAX_DEPTH or descriptor.full_name in outer:
            return value
        body = dict(cast("dict[str, JsonValue]", value))
        properties = self.resolve(schema)["properties"]
        for field in descriptor.fields:
            # Fields of `google.protobuf.Any` values are left out of the schema.
            if not _is_in_full(field) or field.json_name not in properties or excluded == (field,):
                continue
            rest = excluded[1:] if excluded[:1] == (field,) else ()
            body[field.json_name] = self.full(
                cast("Descriptor", field.message_type),
                properties[field.json_name],
                depth + 1,
                rest,
                outer | {descriptor.full_name},
            )
        return body

    def paths(self, root: Descriptor) -> Iterator[tuple[FieldPath, tuple[JsonSchemaObject, ...]]]:
        """Fields breadth-first, so shallow fields come before deep ones, with the schema of each field on the way."""
        queue: deque[tuple[Descriptor, JsonSchemaObject, FieldPath, tuple[JsonSchemaObject, ...]]] = deque(
            [(root, self.schema, (), ())]
        )
        while queue:
            descriptor, schema, prefix, schemas = queue.popleft()
            if is_well_known(descriptor.full_name):
                continue
            properties = self.resolve(schema)["properties"]
            for field in descriptor.fields:
                # Fields of `google.protobuf.Any` values are left out of the schema.
                if field.json_name not in properties:
                    continue
                path = (*prefix, field)
                field_schemas = (*schemas, properties[field.json_name])
                yield path, field_schemas
                if _map_entry(field) is None and field.message_type is not None and len(path) < MAX_DEPTH:
                    child = field_schemas[-1]["items"] if field.is_repeated else field_schemas[-1]
                    queue.append((field.message_type, child, path, field_schemas))

    def bodies(
        self, root: Descriptor, base: JsonValue, path: FieldPath, schemas: tuple[JsonSchemaObject, ...]
    ) -> Iterator[Body]:
        parameter = _render(path)
        for scenario, description, value, checked in self._variants(path[-1], schemas[-1]):
            yield (
                scenario,
                description.format(parameter),
                parameter,
                self._nest(path, schemas, value, base),
                self._nest(path, schemas, checked, base),
            )
        if all(_is_in_full(field) for field in path):
            missing = self.full(root, self.schema, 1, path, frozenset())
            yield CoverageScenario.VALID_OBJECT, f"Missing `{parameter}` message", parameter, missing, missing

    def _variants(self, field: FieldDescriptor, schema: JsonSchemaObject) -> Iterator[Variant]:
        if _map_entry(field) is not None:
            # Without a map the rules admit, the case fails them and is left out.
            entry = next((entry for entry in self._entries(schema) if self.is_valid(schema, entry)), None)
            yield CoverageScenario.VALID_OBJECT, "One default entry in `{}`", entry, entry
            return
        if field.is_repeated:
            items = [self.smallest(schema["items"])]
            yield CoverageScenario.VALID_ARRAY, "One default item in `{}`", items, items
            return
        if _is_oneof_member(field):
            value = self.smallest(schema)
            yield CoverageScenario.VALID_OBJECT, "Oneof branch `{}`", value, value
        if field.message_type is not None:
            value = self.smallest(schema)
            yield CoverageScenario.VALID_OBJECT, "Empty `{}` message", value, value
        elif field.enum_type is not None:
            for name in schema["enum"]:
                yield CoverageScenario.ENUM_VALUE, f"`{{}}` set to `{name}`", name, name
            # Open enums keep numbers they do not declare, unless the rules limit the field to declared values.
            if not field.enum_type.is_closed and (self.constraints is None or self.constraints.field(field) is None):
                numbers = {value.number for value in field.enum_type.values}
                unknown = next(number for number in itertools.count() if number not in numbers)
                yield CoverageScenario.ENUM_VALUE, f"Unknown `{{}}` number {unknown}", unknown, schema["enum"][0]
        elif field.type == FieldDescriptor.TYPE_BOOL:
            yield CoverageScenario.VALID_BOOLEAN, "`{}` set to true", True, True
        elif field.type == FieldDescriptor.TYPE_STRING:
            if not _has_format(schema):
                yield CoverageScenario.VALID_STRING, "Empty `{}`", "", ""
        elif field.type == FieldDescriptor.TYPE_BYTES:
            yield CoverageScenario.VALID_STRING, "Empty `{}`", "", ""
            yield CoverageScenario.VALID_STRING, "One-byte `{}`", "AA==", "AA=="
        else:
            minimum, maximum = _bounds(schema)
            yield CoverageScenario.MINIMUM_VALUE, "Minimum `{}`", minimum, minimum
            yield CoverageScenario.MAXIMUM_VALUE, "Maximum `{}`", maximum, maximum

    def _nest(
        self, path: FieldPath, schemas: tuple[JsonSchemaObject, ...], value: JsonValue, base: JsonValue
    ) -> JsonValue:
        """`base` with `value` at `path`; messages on the way hold their smallest values."""
        field, *rest = path
        if rest:
            inner_schema = schemas[0]["items"] if field.is_repeated else schemas[0]
            inner = self._nest(tuple(rest), schemas[1:], value, self.smallest(inner_schema))
            value = [inner] if field.is_repeated else inner
        return {**cast("dict[str, JsonValue]", base), field.json_name: value}


def _bounds(schema: JsonSchemaObject) -> tuple[int | float, ...]:
    """The lowest and the highest number the range keywords of a number schema admit."""
    kind = schema.get("type")
    if kind not in ("integer", "number"):
        return ()
    step = _step_integer if kind == "integer" else _step_float
    ranges = [resolve_inclusive_bounds(part, step=step) for part in (schema, *schema.get("allOf", ()))]
    return max(low for low, _ in ranges if low is not None), min(high for _, high in ranges if high is not None)


def _step_integer(value: int | float, going_up: bool) -> int | float:
    return value + 1 if going_up else value - 1


def _step_float(value: int | float, going_up: bool) -> float:
    return math.nextafter(value, math.inf if going_up else -math.inf)


def _has_format(schema: JsonValue) -> bool:
    """Whether the schema checks a format, whose values then come from generation alone.

    Validation accepts more than the rules for some formats, and nothing at all for formats it does not know.
    """
    if isinstance(schema, dict):
        return "format" in schema or any(_has_format(value) for value in schema.values())
    return isinstance(schema, list) and any(_has_format(item) for item in schema)


def _required(schema: JsonSchemaObject) -> list[str]:
    """Required fields of a message schema, with the first member of each oneof that must be set."""
    required = list(schema.get("required", ()))
    for group in schema.get("allOf", ()):
        branches = group["oneOf"]
        # A oneof that may stay unset admits a branch with none of its members.
        if all("required" in branch for branch in branches):
            required.append(branches[0]["required"][0])
    return required


def _render(path: FieldPath) -> str:
    parents = "".join(f"{field.json_name}[0]." if field.is_repeated else f"{field.json_name}." for field in path[:-1])
    return parents + path[-1].json_name


def _is_in_full(field: FieldDescriptor) -> bool:
    message = field.message_type
    return message is not None and not field.is_repeated and not _is_oneof_member(field)


def _is_oneof_member(field: FieldDescriptor) -> bool:
    # Single-member groups, including the synthetic ones behind proto3 `optional`, constrain nothing.
    oneof = field.containing_oneof
    return oneof is not None and len(oneof.fields) > 1


def _map_entry(field: FieldDescriptor) -> Descriptor | None:
    message = field.message_type
    if message is not None and message.GetOptions().map_entry:
        return message
    return None
