from __future__ import annotations

import json

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
        json.loads(line)["ScenarioFinished"]["recorder"]["label"]
        for line in report.read_text().splitlines()
        if line.startswith('{"ScenarioFinished"')
    ] == ["Query.getBooks", "Query.getAuthors", "Mutation.addBook", "Mutation.addAuthor"]
