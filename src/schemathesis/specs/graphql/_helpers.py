"""Shared GraphQL schema/AST traversal utilities."""

from __future__ import annotations

from typing import Any, TypeVar

import graphql

NodeT = TypeVar("NodeT", bound=graphql.Node)


def replace_node(node: NodeT, **changes: Any) -> NodeT:
    """Copy of an AST node with some fields replaced; nodes are immutable since graphql-core 3.3."""
    return type(node)(**{**{key: getattr(node, key) for key in node.keys}, **changes})


def replace_field(
    field: graphql.FieldNode,
    arguments: list[graphql.ArgumentNode],
    selection_set: graphql.SelectionSetNode | None,
) -> graphql.FieldNode:
    """The field with new arguments and selection set, or the same node when nothing changed."""
    if selection_set is field.selection_set and all(
        new is old for new, old in zip(arguments, field.arguments or (), strict=True)
    ):
        return field
    return replace_node(field, arguments=tuple(arguments), selection_set=selection_set)


def replace_selections(
    selection_set: graphql.SelectionSetNode, selections: list[graphql.SelectionNode]
) -> graphql.SelectionSetNode:
    """The selection set with new selections, or the same node when nothing changed."""
    if len(selections) == len(selection_set.selections) and all(
        new is old for new, old in zip(selections, selection_set.selections, strict=True)
    ):
        return selection_set
    return replace_node(selection_set, selections=tuple(selections))


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
