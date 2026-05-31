from __future__ import annotations

import graphql
import pytest

from schemathesis.specs.graphql.handles import Handle, SchemaIndex, bundle_name, deleted_bundle_name


@pytest.mark.parametrize(
    ("handle", "expected"),
    [
        (Handle("Book", "id"), "Book_ids"),
        (Handle("Project", "fullPath"), "Project__fullPath"),
        (Handle("Work_Pool", "slug"), "Work_Pool__slug"),
    ],
    ids=["id-handle-keeps-legacy-name", "non-id-handle-double-underscore", "type-with-underscore"],
)
def test_bundle_name(handle, expected):
    assert bundle_name(handle) == expected


def test_only_id_handles_have_deleted_twin():
    assert deleted_bundle_name(Handle("Book", "id")) == "deleted_Book_ids"


def test_schema_index_leaf_string_id_fields():
    schema = graphql.build_schema("""
        type Project { id: ID! fullPath: String! stars: Int! owner: Owner }
        type Owner { id: ID! login: String! }
        type Query { projects: [Project!]! }
        type Mutation { _: Boolean }
    """)
    index = SchemaIndex(schema)
    assert index.has_object_type("Project")
    assert not index.has_object_type("Missing")
    assert index.leaf_string_id_fields("Project") == frozenset({"id", "fullPath"})
    assert index.leaf_string_id_fields("Missing") == frozenset()
