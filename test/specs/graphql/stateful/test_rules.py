from __future__ import annotations

import random

import graphql
import pytest

from schemathesis.specs.graphql.inference import CLEANUP_PREFIXES, extract_entity
from schemathesis.specs.graphql.stateful._rules import _inject_field


def _operation(query: str) -> graphql.OperationDefinitionNode:
    node = graphql.parse(query).definitions[0]
    assert isinstance(node, graphql.OperationDefinitionNode)
    return node


def test_inject_field_adds_missing_field():
    operation = _operation("query { projects { id } }")
    _inject_field("projects", "fullPath", via_edges=False)(operation, random.Random(0))
    assert "fullPath" in graphql.print_ast(operation)


def test_inject_field_idempotent_when_present():
    operation = _operation("query { projects { fullPath } }")
    _inject_field("projects", "fullPath", via_edges=False)(operation, random.Random(0))
    assert graphql.print_ast(operation).count("fullPath") == 1


def test_inject_field_through_relay_connection():
    operation = _operation("query { products { totalCount } }")
    _inject_field("products", "slug", via_edges=True)(operation, random.Random(0))
    printed = graphql.print_ast(operation)
    assert "edges" in printed and "node" in printed and "slug" in printed


def test_inject_field_reuses_existing_edges_node():
    operation = _operation("query { products { edges { node { id } } } }")
    _inject_field("products", "slug", via_edges=True)(operation, random.Random(0))
    printed = graphql.print_ast(operation)
    assert printed.count("edges") == 1 and printed.count("node") == 1 and "slug" in printed


@pytest.mark.parametrize(
    ("field_name", "expected"),
    [
        ("deleteBook", "Book"),
        ("bookDelete", "Book"),
        ("removePost", "Post"),
        ("postRemove", "Post"),
        ("book", None),
        ("addBook", None),
        ("delete", None),
    ],
    ids=[
        "verb-first-camel",
        "verb-last-camel",
        "verb-first-camel-alt",
        "verb-last-camel-alt",
        "single-token",
        "non-cleanup-prefix",
        "verb-only",
    ],
)
def test_extract_entity(field_name, expected):
    assert extract_entity(field_name, prefixes=CLEANUP_PREFIXES) == expected
