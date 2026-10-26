from __future__ import annotations

import json

import pytest

from schemathesis.specs.graphql.stateful._extract import iter_handle_values


def _body(payload: dict) -> bytes:
    return json.dumps(payload).encode("utf-8")


@pytest.mark.parametrize(
    ("body", "field_name", "expected"),
    [
        (_body({"data": {"addBook": {"id": "abc-1"}}}), "addBook", ["abc-1"]),
        (_body({"data": {"authors": [{"id": "a-1"}, {"id": "a-2"}]}}), "authors", ["a-1", "a-2"]),
        (_body({"data": None, "errors": [{"message": "boom"}]}), "addBook", []),
        (_body({"data": {"addBook": None}}), "addBook", []),
        (_body({"data": {"addBook": {"title": "x"}}}), "addBook", []),
        (_body({"data": {"addBook": {"id": 123}}}), "addBook", []),
        (_body({"data": {"addBook": {"id": "abc-1"}}, "errors": [{"message": "boom"}]}), "addBook", []),
        (_body({"data": [1, 2, 3]}), "addBook", []),
        (
            _body({"data": {"authors": [{"id": "a-1"}, "string-instead", None, {"id": "a-2"}]}}),
            "authors",
            ["a-1", "a-2"],
        ),
        (
            _body({"data": {"authors": [{"id": "a-1"}, {"id": 42}, {"id": None}, {"id": "a-2"}]}}),
            "authors",
            ["a-1", "a-2"],
        ),
        (_body({"data": {"addBook": {"id": ""}}}), "addBook", [""]),
        (_body({"data": {"otherField": {"id": "x"}}}), "addBook", []),
        (b"not-json", "addBook", []),
        (b"[]", "addBook", []),
        (b"null", "addBook", []),
        (b'"some string"', "addBook", []),
        (b"", "addBook", []),
    ],
    ids=[
        "single-object",
        "list-of-objects",
        "errors-only",
        "field-value-null",
        "id-missing",
        "id-not-string",
        "data-and-errors-mixed",
        "data-not-a-dict",
        "list-with-non-dict-items",
        "list-items-with-non-string-id",
        "empty-string-id-still-counts",
        "different-field-name-no-match",
        "malformed-json",
        "top-level-list",
        "top-level-null",
        "top-level-string",
        "empty-bytes",
    ],
)
def test_iter_handle_values(body, field_name, expected):
    values = [
        value for _field, value in iter_handle_values(body, field_name=field_name, handle_fields=frozenset({"id"}))
    ]
    assert values == expected


@pytest.mark.parametrize(
    ("body", "field_name", "path", "expected"),
    [
        (
            _body({"data": {"books": {"edges": [{"node": None}, {"node": {"id": "b-1"}}]}}}),
            "books",
            ("edges", "node"),
            ["b-1"],
        ),
        (
            _body({"data": {"createNote": {"note": {"id": "n-1"}, "clientMutationId": "x"}}}),
            "createNote",
            ("note",),
            ["n-1"],
        ),
        (_body({"data": {"createNote": {"note": None}}}), "createNote", ("note",), []),
        (_body({"data": {"createNote": {"id": "n-1"}}}), "createNote", ("note",), []),
    ],
    ids=["connection-edge-with-null-node", "mutation-payload", "payload-record-null", "record-outside-path"],
)
def test_iter_handle_values_through_path(body, field_name, path, expected):
    values = [
        value
        for _field, value in iter_handle_values(body, field_name=field_name, handle_fields=frozenset({"id"}), path=path)
    ]
    assert values == expected
