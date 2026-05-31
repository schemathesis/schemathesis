from __future__ import annotations

from typing import NamedTuple

import graphql

from schemathesis.specs.graphql._helpers import _unwrap

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

    __slots__ = ("_leaf_fields",)

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

    def has_object_type(self, name: str) -> bool:
        return name in self._leaf_fields

    def leaf_string_id_fields(self, type_name: str) -> frozenset[str]:
        return self._leaf_fields.get(type_name, frozenset())


def _is_leaf_handle_field(field_type: graphql.GraphQLType) -> bool:
    unwrapped = _unwrap(field_type)
    return isinstance(unwrapped, graphql.GraphQLScalarType) and unwrapped.name in HANDLE_SCALARS
