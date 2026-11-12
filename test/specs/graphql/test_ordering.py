from __future__ import annotations

import pytest

import schemathesis
from schemathesis.specs.graphql.ordering import compute_graphql_layers
from test.utils import load_ndjson


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


def test_query_named_like_a_producer_stays_a_reader(ctx):
    schema = ctx.graphql.load_sdl(
        "type Book { id: ID! } type Query { createToken: String } type Mutation { addBook: Book }"
    )
    operations = [schema["Query"]["createToken"], schema["Mutation"]["addBook"]]
    assert [[op.label for op in layer] for layer in compute_graphql_layers(operations)] == [
        ["Mutation.addBook"],
        ["Query.createToken"],
    ]


def test_empty_operations_returns_empty_list():
    assert compute_graphql_layers([]) == []


def test_operation_ordering_none_keeps_schema_order(ctx, cli, tmp_path):
    report = tmp_path / "events.ndjson"
    cli.run(
        ctx.graphql.apps.books().schema_url,
        "--phases=fuzzing",
        "--max-examples=1",
        f"--report-ndjson-path={report}",
        config={"phases": {"fuzzing": {"operation-ordering": "none"}}},
    )
    assert [
        event["ScenarioFinished"]["recorder"]["label"] for event in load_ndjson(report) if "ScenarioFinished" in event
    ] == ["Query.getBooks", "Query.getAuthors", "Mutation.addBook", "Mutation.addAuthor"]
