import pytest

import schemathesis
from schemathesis.core.jsonschema import make_validator
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
            {"pattern": r"^[abc\d]{3,40}$"},
        ),
        (
            {
                "maxLength": 40,
                "pattern": r"^[abc\d]$",
            },
            {"pattern": r"^[abc\d]{1,40}$"},
        ),
        (
            {
                "minLength": 3,
                "pattern": r"^[abc\d]$",
            },
            {"pattern": r"^[abc\d]{3,}$"},
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
        pytest.param(
            {
                "maxLength": 10,
                "minLength": 0,
                "pattern": r"^(?:[A-Z0-9](?:[A-Z0-9][- ]?)*[A-Z0-9])?$",
                "type": "string",
            },
            {
                "maxLength": 10,
                "minLength": 0,
                "pattern": r"^(?:[A-Z0-9](?:[A-Z0-9][- ]?)*[A-Z0-9])?$",
                "type": "string",
            },
            id="complex_pattern_preserves_max_length_when_not_encoded",
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


@pytest.mark.parametrize(
    ("schema", "is_response_schema", "expected"),
    [
        pytest.param(
            {
                "allOf": [
                    {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]},
                    {"type": "object", "properties": {"id": {"type": "integer", "readOnly": True}}},
                ],
                "required": ["id", "name"],
            },
            False,
            {
                "allOf": [
                    {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]},
                    {"type": "object", "properties": {"id": {"not": {}}}},
                ],
                "required": ["name"],
            },
            id="readOnly_request",
        ),
        pytest.param(
            {
                "allOf": [
                    {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]},
                    {"type": "object", "properties": {"secret": {"type": "string", "writeOnly": True}}},
                ],
                "required": ["secret", "name"],
            },
            True,
            {
                "allOf": [
                    {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]},
                    {"type": "object", "properties": {"secret": {"not": {}}}},
                ],
                "required": ["name"],
            },
            id="writeOnly_response",
        ),
        pytest.param(
            {
                "allOf": [
                    {"allOf": [{"type": "object", "properties": {"id": {"type": "integer", "readOnly": True}}}]},
                    {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]},
                ],
                "required": ["id", "name"],
            },
            False,
            {
                "allOf": [
                    {"allOf": [{"type": "object", "properties": {"id": {"not": {}}}}]},
                    {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]},
                ],
                "required": ["name"],
            },
            id="readOnly_nested_allOf",
        ),
        pytest.param(
            {
                "allOf": [
                    {"type": "object", "properties": {"id": {"type": "integer", "readOnly": True}}},
                ],
                "required": ["id"],
            },
            False,
            {
                "allOf": [
                    {"type": "object", "properties": {"id": {"not": {}}}},
                ],
            },
            id="required_emptied_is_dropped",
        ),
        pytest.param(
            {
                "allOf": [
                    True,
                    {"type": "object", "properties": {"id": {"type": "integer", "readOnly": True}}},
                ],
                "required": ["id"],
            },
            False,
            {
                "allOf": [
                    True,
                    {"type": "object", "properties": {"id": {"not": {}}}},
                ],
            },
            id="non_dict_allOf_branch_is_skipped",
        ),
    ],
)
def test_forbidden_in_allof_branch_strips_outer_required(schema, is_response_schema, expected):
    result = transform(
        schema, converter.to_json_schema, nullable_keyword="x-nullable", is_response_schema=is_response_schema
    )
    assert result == expected


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
                    {"allOf": [{"$ref": "#/components/schemas/Cat"}, {"properties": {"petType": {"enum": ["Cat"]}}}]},
                    {"allOf": [{"$ref": "#/components/schemas/Dog"}, {"properties": {"petType": {"enum": ["Dog"]}}}]},
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
                    {
                        "allOf": [
                            {"$ref": "#/components/schemas/Cat"},
                            {"properties": {"petType": {"enum": ["feline"]}}},
                        ]
                    },
                    {
                        "allOf": [
                            {"$ref": "#/components/schemas/Dog"},
                            {"properties": {"petType": {"enum": ["canine"]}}},
                        ]
                    },
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
                    {"allOf": [{"$ref": "#/components/schemas/Cat"}, {"properties": {"petType": {"enum": ["Cat"]}}}]},
                    True,
                ],
                "discriminator": {"propertyName": "petType"},
            },
            id="boolean-schema-skipped",
        ),
    ],
)
def test_discriminator_property_pinned(schema, expected):
    assert to_json_schema(schema, nullable_keyword="nullable") == expected


def test_discriminator_pin_validates_with_openapi_3_0_draft4(ctx):
    # OpenAPI 3.0 uses Draft 4, which silently ignores `const`. Pin keyword must use `enum` so the
    # discriminator branches are actually disambiguated at validation time.
    raw = ctx.openapi.build_schema(
        {
            "/rules": {
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "oneOf": [
                                        {"$ref": "#/components/schemas/Allow"},
                                        {"$ref": "#/components/schemas/Deny"},
                                    ],
                                    "discriminator": {
                                        "propertyName": "type",
                                        "mapping": {
                                            "allow": "#/components/schemas/Allow",
                                            "deny": "#/components/schemas/Deny",
                                        },
                                    },
                                }
                            }
                        },
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
        components={
            "schemas": {
                "Allow": {
                    "type": "object",
                    "properties": {"type": {"type": "string"}},
                    "required": ["type"],
                },
                "Deny": {
                    "type": "object",
                    "properties": {"type": {"type": "string"}},
                    "required": ["type"],
                },
            }
        },
    )
    schema = schemathesis.openapi.from_dict(raw)
    body = schema["/rules"]["POST"].body[0]
    validator = make_validator(body.optimized_schema, schema.adapter.jsonschema_validator_cls)
    assert validator.is_valid({"type": "allow"}) is True, "branch-disambiguating pin must work under Draft 4"
