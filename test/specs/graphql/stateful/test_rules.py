from __future__ import annotations

import pytest

from schemathesis.specs.graphql.inference import CLEANUP_PREFIXES, extract_entity


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
