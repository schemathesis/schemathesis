from __future__ import annotations

import graphql
import pytest

from schemathesis.specs.graphql.handles import Handle, SchemaIndex
from schemathesis.specs.graphql.substitution import candidate_handle

_SDL = """
type Project { id: ID! fullPath: String! slug: String! title: String! description: String! stars: Int }
type Owner { id: ID! login: String! }
type Query { projects: [Project!]! }
type Mutation { _: Boolean }
"""


@pytest.fixture
def index():
    return SchemaIndex(graphql.build_schema(_SDL))


@pytest.mark.parametrize(
    ("scalar", "argument", "enclosing", "expected"),
    [
        ("ProjectID", "anything", None, Handle("Project", "id")),
        ("ID", "ownerId", None, Handle("Owner", "id")),
        ("ID", "id", "Project", Handle("Project", "id")),
        ("ID", "ids", "Owner", Handle("Owner", "id")),
        ("ID", "unrelated", None, None),
        ("String", "projectPath", None, Handle("Project", "fullPath")),
        ("ID", "projectPath", None, Handle("Project", "fullPath")),
        ("String", "targetProjectPath", None, Handle("Project", "fullPath")),
        ("String", "sourceProjectPath", None, Handle("Project", "fullPath")),
        ("String", "projectDescription", None, None),
        ("String", "widgetPath", None, None),
        ("String", "path", None, None),
        ("Int", "projectPath", None, None),
        ("String", "slug", "Project", Handle("Project", "slug")),
        ("String", "slug", None, None),
        ("String", "title", "Project", None),
    ],
    ids=[
        "bespoke-type-id-scalar",
        "generic-id-arg-token",
        "bare-id-enclosing",
        "bare-ids-enclosing",
        "unrelated-id-arg",
        "project-path",
        "project-path-id-scalar",
        "target-role-prefix",
        "source-role-prefix",
        "free-text-description-rejected",
        "unknown-type-rejected",
        "single-token-rejected",
        "non-string-scalar-rejected",
        "bare-slug-via-enclosing",
        "bare-slug-no-enclosing-rejected",
        "bare-title-not-identifier-rejected",
    ],
)
def test_candidate_handle(index, scalar, argument, enclosing, expected):
    assert (
        candidate_handle(scalar_name=scalar, argument_name=argument, enclosing_field_type=enclosing, index=index)
        == expected
    )


def test_id_fast_paths_unchanged_without_index():
    # With no index only the id fast paths run, matching pre-handle behavior.
    assert candidate_handle(scalar_name="ID", argument_name="ownerId", enclosing_field_type=None, index=None) == Handle(
        "Owner", "id"
    )
    assert (
        candidate_handle(scalar_name="String", argument_name="projectPath", enclosing_field_type=None, index=None)
        is None
    )
