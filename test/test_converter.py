import pytest

from schemathesis.core.transforms import transform
from schemathesis.specs.openapi import converter
from schemathesis.specs.openapi.converter import is_read_only, is_write_only, rewrite_properties


@pytest.mark.parametrize(
    ("schema", "expected"),
    [
        pytest.param(
            {"type": "array", "prefixItems": [{"type": "string"}, {"type": "integer"}]},
            {"type": "array", "items": [{"type": "string"}, {"type": "integer"}]},
            id="prefixItems_only",
        ),
        pytest.param(
            {"type": "array", "prefixItems": [{"type": "string"}], "items": {"type": "number"}},
            {"type": "array", "items": [{"type": "string"}], "additionalItems": {"type": "number"}},
            id="prefixItems_with_items_schema",
        ),
        pytest.param(
            {"type": "array", "prefixItems": [{"type": "string"}], "items": False},
            {"type": "array", "items": [{"type": "string"}], "additionalItems": False},
            id="prefixItems_with_items_false",
        ),
        pytest.param(
            {
                "type": "object",
                "properties": {"data": {"type": "array", "prefixItems": [{"type": "string"}]}},
            },
            {
                "type": "object",
                "properties": {"data": {"type": "array", "items": [{"type": "string"}]}},
            },
            id="prefixItems_nested_in_properties",
        ),
        pytest.param(
            {"type": "array", "prefixItems": [{"type": "string"}, {"$ref": "#/$defs/MyType"}]},
            {"type": "array", "items": [{"type": "string"}, {"$ref": "#/$defs/MyType"}]},
            id="prefixItems_with_ref",
        ),
    ],
)
def test_prefix_items_to_items_array(schema, expected):
    result = transform(schema, converter.to_json_schema, nullable_keyword="x-nullable")
    assert result == expected


@pytest.mark.parametrize(
    ("schema", "expected"),
    [
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
        (
            {
                "minLength": 3,
                "maxLength": 40,
                "pattern": r"^[abc\d]$",
            },
            {"pattern": r"^([abc\d]){3,40}$"},
        ),
        (
            {
                "maxLength": 40,
                "pattern": r"^[abc\d]$",
            },
            {"pattern": r"^([abc\d]){1,40}$"},
        ),
        (
            {
                "minLength": 3,
                "pattern": r"^[abc\d]$",
            },
            {"pattern": r"^([abc\d]){3,}$"},
        ),
        (
            {
                "minLength": 10,
                "pattern": r"^[abc\d]{1,3}$",
            },
            {
                "minLength": 10,
                "pattern": r"^[abc\d]{1,3}$",
            },
        ),
    ],
)
def test_to_jsonschema_recursive(schema, expected):
    assert transform(schema, converter.to_json_schema, nullable_keyword="x-nullable") == expected


@pytest.mark.parametrize(
    ("schema", "expected"),
    [
        (
            {"properties": {"a": {"readOnly": True}}},
            {
                "properties": {
                    "a": {
                        "not": {},
                    }
                }
            },
        ),
        (
            {"properties": {"a": {"readOnly": True}}, "required": ["a"]},
            {
                "properties": {
                    "a": {
                        "not": {},
                    }
                }
            },
        ),
    ],
)
def test_rewrite_read_only(schema, expected):
    rewrite_properties(schema, is_read_only)
    assert schema == expected


@pytest.mark.parametrize(
    ("schema", "expected"),
    [
        (
            {"properties": {"a": {"writeOnly": True}}},
            {
                "properties": {
                    "a": {
                        "not": {},
                    }
                }
            },
        ),
        (
            {"properties": {"a": {"writeOnly": True}}, "required": ["a"]},
            {
                "properties": {
                    "a": {
                        "not": {},
                    }
                }
            },
        ),
    ],
)
def test_rewrite_write_only(schema, expected):
    rewrite_properties(schema, is_write_only)
    assert schema == expected
