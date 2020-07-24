import pytest

from schemathesis.specs.openapi import converter
from schemathesis.utils import traverse_schema


@pytest.mark.parametrize(
    "schema, expected",
    (
        ({"type": "file"}, {"type": "string", "format": "binary"}),
        (
            {"type": "integer", "maximum": 10, "minimum": 1, "format": "int64"},
            {"type": "integer", "maximum": 10, "minimum": 1, "format": "int64"},
        ),
        ({"x-nullable": True, "type": "string"}, {"anyOf": [{"type": "string"}, {"type": "null"}]}),
        (
            {"x-nullable": True, "type": "integer", "enum": [1, 2]},
            {"anyOf": [{"type": "integer", "enum": [1, 2]}, {"type": "null"}]},
        ),
        (
            {"in": "body", "name": "foo", "schema": {"type": "string"}, "x-nullable": True},
            {"in": "body", "name": "foo", "schema": {"anyOf": [{"type": "string"}, {"type": "null"}]}},
        ),
    ),
)
def test_to_jsonschema(schema, expected):
    assert converter.to_json_schema(schema, "x-nullable") == expected


@pytest.mark.parametrize(
    "schema, expected",
    (
        (
            {
                "type": "object",
                "properties": {"success": {"type": "boolean", "x-nullable": True}},
                "required": ["success"],
            },
            {
                "type": "object",
                "properties": {"success": {"anyOf": [{"type": "boolean"}, {"type": "null"}]}},
                "required": ["success"],
            },
        ),
        (
            {
                "type": "object",
                "properties": {"success": {"type": "array", "items": [{"type": "boolean", "x-nullable": True}]}},
                "required": ["success"],
            },
            {
                "type": "object",
                "properties": {
                    "success": {"type": "array", "items": [{"anyOf": [{"type": "boolean"}, {"type": "null"}]}]}
                },
                "required": ["success"],
            },
        ),
    ),
)
def test_to_jsonschema_recursive(schema, expected):
    assert traverse_schema(schema, converter.to_json_schema, "x-nullable") == expected
