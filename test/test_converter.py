import pytest

from schemathesis.specs.openapi import converter
from schemathesis.utils import traverse_schema


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
