"""Shared GraphQL schema/AST traversal utilities."""

from __future__ import annotations

import graphql


def _unwrap(t: graphql.GraphQLType) -> graphql.GraphQLNamedType:
    while isinstance(t, (graphql.GraphQLNonNull, graphql.GraphQLList)):
        t = t.of_type
    assert isinstance(t, graphql.GraphQLNamedType)
    return t


def _root_type_for(schema: graphql.GraphQLSchema, operation: graphql.OperationType) -> graphql.GraphQLObjectType | None:
    if operation == graphql.OperationType.QUERY:
        return schema.query_type
    if operation == graphql.OperationType.MUTATION:
        return schema.mutation_type
    return None
