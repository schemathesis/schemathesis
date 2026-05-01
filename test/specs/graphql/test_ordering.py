from __future__ import annotations

import pytest

from schemathesis.specs.graphql.ordering import compute_graphql_layers


@pytest.fixture
def operations(graphql_schema):
    return [
        graphql_schema["Query"]["getAuthors"],
        graphql_schema["Mutation"]["addAuthor"],
        graphql_schema["Query"]["getBooks"],
        graphql_schema["Mutation"]["addBook"],
    ]


def test_layers_partition_by_role(operations):
    assert [[op.label for op in layer] for layer in compute_graphql_layers(operations)] == [
        ["Mutation.addAuthor", "Mutation.addBook"],
        ["Query.getAuthors", "Query.getBooks"],
    ]


def test_empty_operations_returns_empty_list():
    assert compute_graphql_layers([]) == []
