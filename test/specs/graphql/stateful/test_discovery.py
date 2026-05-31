from __future__ import annotations

import graphql
import pytest

from schemathesis.specs.graphql.handles import Handle, SchemaIndex
from schemathesis.specs.graphql.stateful._discovery import discover_handles


def _discover(sdl: str) -> set[Handle]:
    schema = graphql.build_schema(sdl)
    return discover_handles(schema, SchemaIndex(schema))


@pytest.mark.parametrize(
    ("sdl", "handle", "present"),
    [
        (
            """
            type Project { id: ID! fullPath: String! }
            type Query { projects: [Project!]! }
            type Mutation { moveIssue(projectPath: String!, title: String!): Boolean }
            """,
            Handle("Project", "fullPath"),
            True,
        ),
        (
            """
            type Project { id: ID! slug: String! }
            type Query { projects: [Project!]! project(slug: String!): Project }
            type Mutation { _: Boolean }
            """,
            Handle("Project", "slug"),
            True,
        ),
        (
            """
            type Product { id: ID! slug: String! }
            type ProductEdge { node: Product }
            type ProductConnection { edges: [ProductEdge!]! }
            type Query { products: ProductConnection! product(slug: String!): Product }
            type Mutation { _: Boolean }
            """,
            Handle("Product", "slug"),
            True,
        ),
        (
            """
            type Book { id: ID! }
            type Query { book(id: ID!): Book }
            type Mutation { addBook(title: String!): Book! }
            """,
            Handle("Book", "id"),
            True,
        ),
        (
            """
            type Project { id: ID! fullPath: String! }
            type Query { project(fullPath: String!): Project }
            type Mutation { moveIssue(projectPath: String!): Boolean }
            """,
            Handle("Project", "fullPath"),
            False,
        ),
        (
            """
            type Project { id: ID! slug: String! }
            type Query { getProject(projectSlug: String!): Project }
            type Mutation { tagProject(projectSlug: String!): Boolean }
            """,
            Handle("Project", "slug"),
            False,
        ),
        (
            """
            type Project { id: ID! fullPath: String! }
            type Query { projects: [Project!]! }
            type Mutation { _: Boolean }
            """,
            Handle("Project", "fullPath"),
            False,
        ),
        (
            """
            type Project { id: ID! slug: String! }
            type Query { ping: Boolean }
            type Mutation { moveIssue(projectSlug: String!): Boolean }
            """,
            Handle("Project", "slug"),
            False,
        ),
        (
            """
            type Project { id: ID! description: String! }
            type Query { projects: [Project!]! }
            type Mutation { annotate(projectDescription: String!): Boolean }
            """,
            Handle("Project", "description"),
            False,
        ),
    ],
    ids=[
        "seedable-via-token-arg",
        "seedable-via-bare-slug",
        "seedable-via-relay-connection",
        "id-handle-always-present",
        "dropped-only-producer-requires-it-by-name",
        "dropped-only-producer-requires-it-by-match",
        "dropped-produced-but-never-consumed",
        "dropped-no-producer-returns-type",
        "dropped-free-text-field",
    ],
)
def test_discover_handles(sdl, handle, present):
    assert (handle in _discover(sdl)) is present
