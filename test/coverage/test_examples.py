import pytest

from schemathesis.generation.coverage import push_examples_to_properties


@pytest.mark.parametrize(
    ("schema", "expected"),
    [
        (
            {
                "type": "object",
                "properties": {"name": {"type": "string"}, "age": {"type": "integer"}},
                "examples": [{"name": "John", "age": 30}, {"name": "Jane", "age": 25}],
            },
            {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "examples": ["John", "Jane"]},
                    "age": {"type": "integer", "examples": [30, 25]},
                },
                "examples": [{"name": "John", "age": 30}, {"name": "Jane", "age": 25}],
            },
        ),
        (
            {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "examples": ["Alice"]},
                    "age": {"type": "integer", "examples": [20]},
                },
                "examples": [{"name": "John", "age": 30}, {"name": "Jane", "age": 25}],
            },
            {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "examples": ["Alice", "John", "Jane"]},
                    "age": {"type": "integer", "examples": [20, 30, 25]},
                },
                "examples": [{"name": "John", "age": 30}, {"name": "Jane", "age": 25}],
            },
        ),
        (
            {
                "type": "object",
                "properties": {"name": {"type": "string"}, "age": {"type": "integer"}, "city": {"type": "string"}},
                "examples": [{"name": "John", "age": 30}, {"name": "Jane", "city": "New York"}],
            },
            {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "examples": ["John", "Jane"]},
                    "age": {"type": "integer", "examples": [30]},
                    "city": {"type": "string", "examples": ["New York"]},
                },
                "examples": [{"name": "John", "age": 30}, {"name": "Jane", "city": "New York"}],
            },
        ),
        (
            {"type": "object", "properties": {"name": {"type": "string"}, "age": {"type": "integer"}}},
            {"type": "object", "properties": {"name": {"type": "string"}, "age": {"type": "integer"}}},
        ),
        (
            {"type": "object", "properties": {"name": {"type": "string"}, "age": {"type": "integer"}}, "examples": []},
            {"type": "object", "properties": {"name": {"type": "string"}, "age": {"type": "integer"}}, "examples": []},
        ),
        ({"type": "string", "examples": ["foo", "bar"]}, {"type": "string", "examples": ["foo", "bar"]}),
        (
            {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "age": {"type": "integer"},
                    "hobbies": {"type": "array"},
                    "address": {"type": "object"},
                },
                "examples": [
                    {"name": "John", "age": 30, "hobbies": ["reading", "swimming"], "address": {"city": "New York"}},
                    {"name": "Jane", "age": 25, "hobbies": ["painting"], "address": {"city": "London"}},
                    {"name": "John", "age": 30, "hobbies": ["reading", "swimming"], "address": {"city": "New York"}},
                ],
            },
            {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "examples": ["John", "Jane"]},
                    "age": {"type": "integer", "examples": [30, 25]},
                    "hobbies": {"type": "array", "examples": [["reading", "swimming"], ["painting"]]},
                    "address": {"type": "object", "examples": [{"city": "New York"}, {"city": "London"}]},
                },
                "examples": [
                    {"name": "John", "age": 30, "hobbies": ["reading", "swimming"], "address": {"city": "New York"}},
                    {"name": "Jane", "age": 25, "hobbies": ["painting"], "address": {"city": "London"}},
                    {"name": "John", "age": 30, "hobbies": ["reading", "swimming"], "address": {"city": "New York"}},
                ],
            },
        ),
        (
            {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "examples": ["Alice"]},
                    "age": {"type": "integer", "examples": [20]},
                    "hobbies": {"type": "array", "examples": [["reading"]]},
                    "address": {"type": "object", "examples": [{"city": "Paris"}]},
                },
                "examples": [
                    {"name": "John", "age": 30, "hobbies": ["reading"], "address": {"city": "New York"}},
                    {"name": "Jane", "age": 25, "hobbies": ["painting"], "address": {"city": "London"}},
                    {"name": "Alice", "age": 20, "hobbies": ["reading"], "address": {"city": "Paris"}},
                ],
            },
            {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "examples": ["Alice", "John", "Jane"]},
                    "age": {"type": "integer", "examples": [20, 30, 25]},
                    "hobbies": {"type": "array", "examples": [["reading"], ["painting"]]},
                    "address": {
                        "type": "object",
                        "examples": [{"city": "Paris"}, {"city": "New York"}, {"city": "London"}],
                    },
                },
                "examples": [
                    {"name": "John", "age": 30, "hobbies": ["reading"], "address": {"city": "New York"}},
                    {"name": "Jane", "age": 25, "hobbies": ["painting"], "address": {"city": "London"}},
                    {"name": "Alice", "age": 20, "hobbies": ["reading"], "address": {"city": "Paris"}},
                ],
            },
        ),
        (
            {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "examples": ["Alice"]},
                },
                "examples": [2],
            },
            {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "examples": ["Alice"]},
                },
                "examples": [2],
            },
        ),
    ],
)
def test_push_examples_to_properties(schema, expected):
    push_examples_to_properties(schema)
    assert schema == expected
