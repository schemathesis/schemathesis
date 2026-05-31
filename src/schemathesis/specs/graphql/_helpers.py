"""Shared GraphQL schema/AST traversal utilities."""

from __future__ import annotations

import graphql


def _unwrap(t: graphql.GraphQLType) -> graphql.GraphQLNamedType:
    while isinstance(t, (graphql.GraphQLNonNull, graphql.GraphQLList)):
        t = t.of_type
    assert isinstance(t, graphql.GraphQLNamedType)
    return t


def relay_node_type(type_: graphql.GraphQLNamedType) -> graphql.GraphQLObjectType | None:
    """Return the node type of a Relay connection (`edges { node }`), or None if not a connection."""
    if not isinstance(type_, graphql.GraphQLObjectType):
        return None
    edges = type_.fields.get("edges")
    if edges is None:
        return None
    edge = _unwrap(edges.type)
    if not isinstance(edge, graphql.GraphQLObjectType) or "node" not in edge.fields:
        return None
    node = _unwrap(edge.fields["node"].type)
    return node if isinstance(node, graphql.GraphQLObjectType) else None


def _root_type_for(schema: graphql.GraphQLSchema, operation: graphql.OperationType) -> graphql.GraphQLObjectType | None:
    if operation == graphql.OperationType.QUERY:
        return schema.query_type
    if operation == graphql.OperationType.MUTATION:
        return schema.mutation_type
    return None
