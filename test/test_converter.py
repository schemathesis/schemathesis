import pytest

from schemathesis.internal.jsonschema import traverse_schema
from schemathesis.specs.openapi import converter
from schemathesis.specs.openapi.converter import forbid_properties, is_read_only, is_write_only, rewrite_properties


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
    assert traverse_schema(schema, converter.to_json_schema, nullable_name="x-nullable") == expected


@pytest.mark.parametrize(
    "schema, forbidden, expected",
    (
        ({}, ["foo"], {"not": {"required": {"foo"}}}),
        ({"not": {"type": "array"}}, ["foo"], {"not": {"required": {"foo"}, "type": "array"}}),
        ({"not": {"required": ["bar"]}}, ["foo"], {"not": {"required": {"bar", "foo"}}}),
        ({"not": {"required": ["foo"]}}, ["foo"], {"not": {"required": {"foo"}}}),
        ({"not": {"required": ["bar", "foo"]}}, ["foo"], {"not": {"required": {"bar", "foo"}}}),
    ),
)
def test_forbid_properties(schema, forbidden, expected):
    forbid_properties(schema, forbidden)
    schema["not"]["required"] = set(schema["not"]["required"])
    assert schema == expected


@pytest.mark.parametrize(
    "schema, expected",
    (
        ({"properties": {"a": {"readOnly": True}}}, {"not": {"required": ["a"]}}),
        ({"properties": {"a": {"readOnly": True}}, "required": ["a"]}, {"not": {"required": ["a"]}}),
    ),
)
def test_rewrite_read_only(schema, expected):
    rewrite_properties(schema, is_read_only)
    assert schema == expected


@pytest.mark.parametrize(
    "schema, expected",
    (
        ({"properties": {"a": {"writeOnly": True}}}, {"not": {"required": ["a"]}}),
        ({"properties": {"a": {"writeOnly": True}}, "required": ["a"]}, {"not": {"required": ["a"]}}),
    ),
)
def test_rewrite_write_only(schema, expected):
    rewrite_properties(schema, is_write_only)
    assert schema == expected
