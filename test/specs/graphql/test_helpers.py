from __future__ import annotations

import graphql
import pytest

from schemathesis.specs.graphql._helpers import relay_node_type

_SDL = """
type Product { id: ID! }
type ProductEdge { node: Product }
type ProductConnection { edges: [ProductEdge!]! }
type Brand { edges: [String!]! }
type Bag { edges: [BagEdge!]! }
type BagEdge { cursor: String }
type Query { _: Boolean }
"""


@pytest.mark.parametrize(
    ("type_name", "expected"),
    [
        ("ProductConnection", "Product"),
        ("Product", None),
        ("Brand", None),
        ("Bag", None),
    ],
    ids=["connection-yields-node", "plain-object", "edges-not-objects", "edges-without-node"],
)
def test_relay_node_type(type_name, expected):
    node = relay_node_type(graphql.build_schema(_SDL).type_map[type_name])
    assert (node.name if node is not None else None) == expected
