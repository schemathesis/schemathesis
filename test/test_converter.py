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
    ),
)
def test_to_jsonschema(schema, expected):
    assert converter.to_json_schema(schema, "x-nullable") == expected
