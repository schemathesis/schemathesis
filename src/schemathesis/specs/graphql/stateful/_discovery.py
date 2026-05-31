from __future__ import annotations

from typing import TYPE_CHECKING

import graphql

from schemathesis.specs.graphql._helpers import _unwrap, relay_node_type
from schemathesis.specs.graphql.handles import Handle, SchemaIndex
from schemathesis.specs.graphql.stateful._bundles import collect_id_typed_object_types
from schemathesis.specs.graphql.substitution import candidate_handle

if TYPE_CHECKING:
    from collections.abc import Iterator


def discover_handles(schema: graphql.GraphQLSchema, index: SchemaIndex) -> set[Handle]:
    """Active producer-consumer handles: all id handles plus wanted, producible, seedable non-id handles."""
    id_handles = {Handle(name, "id") for name in collect_id_typed_object_types(schema)}

    wanted: set[Handle] = set()
    for field_def, enclosing in _root_fields(schema):
        for handle in _arg_handles(field_def, enclosing, index):
            if handle.field_name != "id":
                wanted.add(handle)

    survivors: set[Handle] = set()
    for handle in wanted:
        producers = [field_def for field_def, _ in _root_fields(schema) if _returns_type(field_def, handle.type_name)]
        if not producers:
            continue
        if any(not _requires_handle(producer, handle, index) for producer in producers):
            survivors.add(handle)

    return id_handles | survivors


def _root_fields(schema: graphql.GraphQLSchema) -> Iterator[tuple[graphql.GraphQLField, str | None]]:
    for root in (schema.query_type, schema.mutation_type):
        if root is None:
            continue
        for field_def in root.fields.values():
            yield field_def, _return_object_name(field_def)


def _return_object_name(field_def: graphql.GraphQLField) -> str | None:
    return_type = _unwrap(field_def.type)
    node = relay_node_type(return_type)
    if node is not None:
        return node.name
    return return_type.name if isinstance(return_type, graphql.GraphQLObjectType) else None


def _returns_type(field_def: graphql.GraphQLField, type_name: str) -> bool:
    return _return_object_name(field_def) == type_name


def _arg_handles(field_def: graphql.GraphQLField, enclosing: str | None, index: SchemaIndex) -> set[Handle]:
    handles: set[Handle] = set()
    for argument_name, argument in field_def.args.items():
        handle = candidate_handle(
            scalar_name=_unwrap(argument.type).name,
            argument_name=argument_name,
            enclosing_field_type=enclosing,
            index=index,
        )
        if handle is not None:
            handles.add(handle)
    return handles


def _requires_handle(field_def: graphql.GraphQLField, handle: Handle, index: SchemaIndex) -> bool:
    # A producer requires the handle when an argument resolves to it, or is named exactly like the handle
    # field (a bare field name, e.g. `project(fullPath:): Project`, that cannot seed itself).
    for argument_name, argument in field_def.args.items():
        if argument_name == handle.field_name:
            return True
        resolved = candidate_handle(
            scalar_name=_unwrap(argument.type).name,
            argument_name=argument_name,
            enclosing_field_type=handle.type_name,
            index=index,
        )
        if resolved == handle:
            return True
    return False
