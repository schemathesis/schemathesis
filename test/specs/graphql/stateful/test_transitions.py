from __future__ import annotations

import graphql
import pytest

import schemathesis
from schemathesis.specs.graphql.schemas import GraphQLSchema


def _build(sdl: str) -> GraphQLSchema:
    introspection = graphql.introspection_from_schema(graphql.build_schema(sdl))
    return schemathesis.graphql.from_dict(introspection)


_PRODUCER_TO_READER = """
    type Book { id: ID! }
    type Query { book(id: ID!): Book }
    type Mutation { addBook(title: String!): Book! }
"""

_DIFFERENT_TYPES = """
    type Book { id: ID! }
    type Author { id: ID! }
    type Query { author(id: ID!): Author }
    type Mutation { addBook(title: String!): Book! }
"""

_PRODUCER_PLUS_CLEANUP = """
    type Book { id: ID! }
    type Query { book(id: ID!): Book }
    type Mutation {
        addBook(title: String!): Book!
        deleteBook(id: ID!): Boolean
    }
"""

_NO_PRODUCER = """
    type Book { id: ID! }
    type Query { book(id: ID!): Book }
    type Mutation { _: Boolean }
"""


@pytest.mark.parametrize(
    ("sdl", "expected_edges"),
    [
        (_PRODUCER_TO_READER, [("Mutation.addBook", "Query.book")]),
        (_DIFFERENT_TYPES, []),
        (
            _PRODUCER_PLUS_CLEANUP,
            [("Mutation.addBook", "Query.book"), ("Mutation.addBook", "Mutation.deleteBook")],
        ),
        (_NO_PRODUCER, []),
    ],
    ids=["producer-to-reader", "no-match-different-types", "cleanup-is-not-producer", "no-producer-no-edges"],
)
def test_transition_edges(sdl, expected_edges):
    transitions = _build(sdl).analysis.transitions
    edges = [(label, edge.target.label) for label, ops in transitions.operations.items() for edge in ops.outgoing]
    assert sorted(edges) == sorted(expected_edges)


def test_outgoing_and_incoming_are_consistent():
    # For each outgoing edge from operation A, operation B should list it as incoming.
    transitions = _build(_PRODUCER_TO_READER).analysis.transitions
    for source, ops in transitions.operations.items():
        for edge in ops.outgoing:
            target_incoming = transitions.operations[edge.target.label].incoming
            assert any(
                incoming.source.label == source and incoming.target.label == edge.target.label
                for incoming in target_incoming
            )
