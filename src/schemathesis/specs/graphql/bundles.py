from __future__ import annotations

import graphql


def collect_id_typed_object_types(schema: graphql.GraphQLSchema) -> set[str]:
    """Names of object types with an `id` field, excluding introspection types (`__*`)."""
    result: set[str] = set()
    for name, type_ in schema.type_map.items():
        if name.startswith("__"):
            continue
        if not isinstance(type_, graphql.GraphQLObjectType):
            continue
        if "id" in type_.fields:
            result.add(name)
    return result
