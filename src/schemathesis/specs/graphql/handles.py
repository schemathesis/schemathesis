from __future__ import annotations

from typing import NamedTuple

import graphql

from schemathesis.specs.graphql._helpers import _unwrap, relay_node_type
from schemathesis.specs.graphql.inference import CLEANUP_PREFIXES, PRODUCER_PREFIXES, extract_entity

# Scalar names whose values substitution can emit as a StringValueNode.
HANDLE_SCALARS = ("String", "ID")


class Handle(NamedTuple):
    type_name: str
    field_name: str


def bundle_name(handle: Handle) -> str:
    if handle.field_name == "id":
        return f"{handle.type_name}_ids"
    return f"{handle.type_name}__{handle.field_name}"


def deleted_bundle_name(handle: Handle) -> str:
    # Only `id` handles carry lifecycle; non-`id` handles never call this.
    return f"deleted_{handle.type_name}_ids"


class SchemaIndex:
    """Object-type lookups for the lexical handle matcher, computed once per schema."""

    __slots__ = ("_leaf_fields", "_envelopes")

    def __init__(self, schema: graphql.GraphQLSchema) -> None:
        leaf: dict[str, frozenset[str]] = {}
        for name, type_ in schema.type_map.items():
            if name.startswith("__") or not isinstance(type_, graphql.GraphQLObjectType):
                continue
            fields = {
                field_name for field_name, field_def in type_.fields.items() if _is_leaf_handle_field(field_def.type)
            }
            leaf[name] = frozenset(fields)
        self._leaf_fields = leaf
        self._envelopes = _collect_envelopes(schema, leaf)

    def has_object_type(self, name: str) -> bool:
        return name in self._leaf_fields

    def leaf_string_id_fields(self, type_name: str) -> frozenset[str]:
        return self._leaf_fields.get(type_name, frozenset())

    def envelope_resource(self, type_name: str) -> str | None:
        """Resource a mutation result envelope names (`DeleteNote` -> `Note`), or None for genuine resources."""
        return self._envelopes.get(type_name)


def _collect_envelopes(schema: graphql.GraphQLSchema, object_types: dict[str, frozenset[str]]) -> dict[str, str]:
    # A type returned by a mutation that no query reads back, whose name minus a CRUD verb is another object type.
    readable: set[str] = set()
    if schema.query_type is not None:
        for field_def in schema.query_type.fields.values():
            return_type = _unwrap(field_def.type)
            node = relay_node_type(return_type)
            readable.add(return_type.name if node is None else node.name)
    envelopes: dict[str, str] = {}
    if schema.mutation_type is not None:
        for field_def in schema.mutation_type.fields.values():
            name = _unwrap(field_def.type).name
            if name in readable:
                continue
            entity = extract_entity(name, prefixes=PRODUCER_PREFIXES | CLEANUP_PREFIXES)
            if entity is not None and entity in object_types:
                envelopes[name] = entity
    return envelopes


def _is_leaf_handle_field(field_type: graphql.GraphQLType) -> bool:
    unwrapped = _unwrap(field_type)
    return isinstance(unwrapped, graphql.GraphQLScalarType) and unwrapped.name in HANDLE_SCALARS
