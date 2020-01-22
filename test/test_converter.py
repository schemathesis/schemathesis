import pytest

from schemathesis import converter


@pytest.mark.parametrize(
    "schema, expected",
    (
        ({"type": "file"}, {"type": "string", "format": "binary"}),
        (
            {"type": "integer", "maximum": 10, "minimum": 1, "format": "int64",},
            {"type": "integer", "maximum": 10, "minimum": 1, "format": "int64"},
        ),
        ({"x-nullable": True, "type": "string"}, {"anyOf": [{"type": "string"}, {"type": "null"}]}),
        (
            {"x-nullable": True, "type": "integer", "enum": [1, 2]},
            {"anyOf": [{"type": "integer", "enum": [1, 2]}, {"type": "null"}]},
        ),
        ({"type": "integer", "minimum": 0, "exclusiveMinimum": True}, {"type": "integer", "exclusiveMinimum": 0},),
        ({"type": "integer", "maximum": 0, "exclusiveMaximum": True}, {"type": "integer", "exclusiveMaximum": 0},),
        ({"type": "integer", "maximum": 0, "exclusiveMaximum": False}, {"type": "integer", "maximum": 0},),
    ),
)
def test_to_jsonschema(schema, expected):
    assert converter.to_json_schema(schema, "x-nullable") == expected
