import pytest

from schemathesis.core.transforms import transform
from schemathesis.specs.openapi import converter
from schemathesis.specs.openapi.converter import is_read_only, is_write_only, rewrite_properties, to_json_schema


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


def test_upgrade_legacy_exclusive_bounds():
    schema = {
        "type": "object",
        "properties": {
            "value": {
                "oneOf": [
                    {"type": "number", "minimum": 0, "exclusiveMinimum": True},
                    {"type": "number", "maximum": 10, "exclusiveMaximum": False},
                ]
            }
        },
    }

    result = transform(
        schema,
        converter.to_json_schema,
        nullable_keyword="nullable",
        upgrade_legacy_exclusive_bounds=True,
    )

    assert result == {
        "type": "object",
        "properties": {
            "value": {
                "oneOf": [
                    {"type": "number", "exclusiveMinimum": 0},
                    {"type": "number", "maximum": 10},
                ]
            }
        },
    }


def test_does_not_upgrade_legacy_exclusive_bounds_by_default():
    schema = {"type": "number", "minimum": 0, "exclusiveMinimum": True}

    result = transform(schema, converter.to_json_schema, nullable_keyword="nullable")

    assert result == schema


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


def test_pattern_translation_success():
    # When a schema contains a PCRE pattern that can be translated to Python regex
    schema = {"type": "string", "pattern": r"\p{L}+"}
    result = transform(schema, converter.to_json_schema, nullable_keyword="x-nullable")
    # Then the pattern should be translated
    assert result == {"type": "string", "pattern": r"[a-zA-Z\u00C0-\u00D6\u00D8-\u00F6\u00F8-\u024F]+"}


def test_pattern_translation_invalid_result():
    # When a PCRE pattern translates to an invalid Python regex
    # `\p{L}` gets translated but the `[` at the end makes the result invalid
    schema = {"type": "string", "pattern": r"\p{L}["}
    result = transform(schema, converter.to_json_schema, nullable_keyword="x-nullable")
    # Then the pattern should be removed (translation failed validation)
    assert result == {"type": "string"}


def test_nested_object_required_array_not_duplicated():
    # GH-3460: Nested `required` arrays should not cause duplicates in parent's `required`
    schema = {
        "type": "object",
        "properties": {
            "propOne": {
                "type": "object",
                "properties": {"innerPropOne": {"type": "integer"}},
                "required": ["innerPropOne"],
            },
        },
        "required": ["propOne"],
    }
    result = transform(schema, converter.to_json_schema, nullable_keyword="nullable")
    assert result["required"] == ["propOne"]
    assert result["properties"]["propOne"]["required"] == ["innerPropOne"]


@pytest.mark.parametrize(
    ("schema", "expected"),
    [
        pytest.param(
            {
                "oneOf": [
                    {"$ref": "#/components/schemas/Cat"},
                    {"$ref": "#/components/schemas/Dog"},
                ],
                "discriminator": {"propertyName": "petType"},
            },
            {
                "oneOf": [
                    {"allOf": [{"$ref": "#/components/schemas/Cat"}, {"properties": {"petType": {"const": "Cat"}}}]},
                    {"allOf": [{"$ref": "#/components/schemas/Dog"}, {"properties": {"petType": {"const": "Dog"}}}]},
                ],
                "discriminator": {"propertyName": "petType"},
            },
            id="implicit-mapping",
        ),
        pytest.param(
            {
                "anyOf": [
                    {"$ref": "#/components/schemas/Cat"},
                    {"$ref": "#/components/schemas/Dog"},
                ],
                "discriminator": {
                    "propertyName": "petType",
                    "mapping": {"feline": "#/components/schemas/Cat", "canine": "#/components/schemas/Dog"},
                },
            },
            {
                "anyOf": [
                    {"allOf": [{"$ref": "#/components/schemas/Cat"}, {"properties": {"petType": {"const": "feline"}}}]},
                    {"allOf": [{"$ref": "#/components/schemas/Dog"}, {"properties": {"petType": {"const": "canine"}}}]},
                ],
                "discriminator": {
                    "propertyName": "petType",
                    "mapping": {"feline": "#/components/schemas/Cat", "canine": "#/components/schemas/Dog"},
                },
            },
            id="explicit-mapping",
        ),
        pytest.param(
            {
                "oneOf": [{"$ref": "#/components/schemas/Cat"}],
                "discriminator": {},
            },
            {
                "oneOf": [{"$ref": "#/components/schemas/Cat"}],
                "discriminator": {},
            },
            id="no-property-name-skip",
        ),
        pytest.param(
            {
                "anyOf": [
                    {"$ref": "#/components/schemas/Cat"},
                    True,
                ],
                "discriminator": {"propertyName": "petType"},
            },
            {
                "anyOf": [
                    {"allOf": [{"$ref": "#/components/schemas/Cat"}, {"properties": {"petType": {"const": "Cat"}}}]},
                    True,
                ],
                "discriminator": {"propertyName": "petType"},
            },
            id="boolean-schema-skipped",
        ),
    ],
)
def test_discriminator_const_injection(schema, expected):
    assert to_json_schema(schema, nullable_keyword="nullable") == expected
