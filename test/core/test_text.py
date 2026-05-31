from __future__ import annotations

import pytest

from schemathesis.core.text import to_pascal_case, to_snake_case


@pytest.mark.parametrize(
    ("text", "expected"),
    [("user_id", "UserId"), ("full-path", "FullPath"), ("project", "Project"), ("", "")],
    ids=["snake", "kebab", "single", "empty"],
)
def test_to_pascal_case(text, expected):
    assert to_pascal_case(text) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [("fullPath", "full_path"), ("ProjectID", "project_i_d"), ("name", "name")],
    ids=["camel", "acronym", "single"],
)
def test_to_snake_case(text, expected):
    assert to_snake_case(text) == expected
