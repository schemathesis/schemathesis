from __future__ import annotations

import graphql
import pytest

from schemathesis.specs.graphql.stateful._bundles import collect_id_typed_object_types


@pytest.mark.parametrize(
    ("sdl", "expected"),
    [
        (
            """
            type Book { id: ID! title: String! }
            type Query { book(id: ID!): Book }
            type Mutation { addBook(title: String!): Book! }
            """,
            {"Book"},
        ),
        (
            """
            type Author { name: String! }
            type Query { author: Author }
            type Mutation { _: Boolean }
            """,
            set(),
        ),
        (
            """
            type Book { id: ID! }
            type Author { id: ID! }
            type Query { book(id: ID!): Book author(id: ID!): Author }
            type Mutation { _: Boolean }
            """,
            {"Book", "Author"},
        ),
        (
            """
            type Book { id: ID! identifier: String! }
            type Author { name: String! identifier: String! }
            type Query { book(id: ID!): Book author: Author }
            type Mutation { _: Boolean }
            """,
            {"Book"},
        ),
        (
            """
            type Book { id: ID }
            type Query { book(id: ID!): Book }
            type Mutation { _: Boolean }
            """,
            {"Book"},
        ),
        (
            """
            interface Node { id: ID! }
            type Book implements Node { id: ID! title: String! }
            type Query { book(id: ID!): Book }
            type Mutation { _: Boolean }
            """,
            {"Book"},
        ),
    ],
    ids=[
        "single-object-with-id",
        "no-types-with-id",
        "multiple-types-with-id",
        "type-without-id-field-is-excluded",
        "nullable-id-counts",
        "interface-not-collected-only-implementer",
    ],
)
def test_collect_id_typed_object_types(sdl, expected):
    assert collect_id_typed_object_types(graphql.build_schema(sdl)) == expected


def test_introspection_types_excluded():
    # `__Schema`, `__Type`, etc. expose `name` fields and would otherwise look like Object types.
    schema = graphql.build_schema("""
        type Book { id: ID! }
        type Query { book(id: ID!): Book }
        type Mutation { _: Boolean }
    """)
    result = collect_id_typed_object_types(schema)
    assert all(not name.startswith("__") for name in result)
