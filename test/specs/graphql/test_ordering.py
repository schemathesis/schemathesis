from __future__ import annotations

import pytest

import schemathesis
from schemathesis.specs.graphql.ordering import compute_graphql_layers


@pytest.fixture
def operations(ctx):
    schema = schemathesis.graphql.from_url(ctx.graphql.apps.books().schema_url)
    return [
        schema["Query"]["getAuthors"],
        schema["Mutation"]["addAuthor"],
        schema["Query"]["getBooks"],
        schema["Mutation"]["addBook"],
    ]


def test_layers_partition_by_role(operations):
    assert [[op.label for op in layer] for layer in compute_graphql_layers(operations)] == [
        ["Mutation.addAuthor", "Mutation.addBook"],
        ["Query.getAuthors", "Query.getBooks"],
    ]


def test_empty_operations_returns_empty_list():
    assert compute_graphql_layers([]) == []
