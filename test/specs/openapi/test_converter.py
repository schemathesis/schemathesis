import json

import jsonschema_rs
import pytest
from hypothesis import given, settings

import schemathesis
from schemathesis.core.jsonschema import make_validator, make_validator_for
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


CONTAINS_ONE = {"contains": {"const": 1}}
CONTAINS_TWO = {"contains": {"const": 2}}


@pytest.mark.parametrize(
    ("version", "schema", "expected"),
    [
        pytest.param(
            "3.0.2",
            {"type": "array", "items": {"type": "integer"}, "allOf": [CONTAINS_ONE, CONTAINS_TWO, {"maxItems": 4}]},
            {
                "type": "array",
                "items": [{"enum": [1]}, {"enum": [2]}],
                "additionalItems": {"type": "integer"},
                "allOf": [{"maxItems": 4}],
                "minItems": 2,
            },
            id="items-and-other-allof-entries-kept",
        ),
        pytest.param(
            "3.0.2",
            {"type": "array", "allOf": [CONTAINS_ONE, CONTAINS_TWO], "minItems": 3},
            {"type": "array", "items": [{"enum": [1]}, {"enum": [2]}], "minItems": 3},
            id="larger-min-items-kept",
        ),
        pytest.param(
            "3.0.2",
            {"type": "array", "allOf": [CONTAINS_ONE, {"maxItems": 4}]},
            {"type": "array", "allOf": [CONTAINS_ONE, {"maxItems": 4}]},
            id="single-contains-untouched",
        ),
        pytest.param(
            "3.0.2",
            {"type": "array", "items": [{"type": "integer"}], "allOf": [CONTAINS_ONE, CONTAINS_TWO]},
            {"type": "array", "items": [{"type": "integer"}], "allOf": [CONTAINS_ONE, CONTAINS_TWO]},
            id="positional-items-untouched",
        ),
    ],
)
def test_allof_of_contains_consts_placed_positionally(ctx, version, schema, expected):
    # Draft 4 ignores `contains`, so each required value needs its own position to land in generated arrays.
    loaded = ctx.openapi.load_schema(
        {
            "/items": {
                "post": {
                    "requestBody": {"required": True, "content": {"application/json": {"schema": schema}}},
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
        version=version,
    )
    assert loaded["/items"]["POST"].body[0].optimized_schema == expected


@pytest.mark.parametrize(
    ("schema", "expected"),
    [
        pytest.param(
            {
                "type": "object",
                "properties": {"kind": {"type": "string"}},
                "if": {"properties": {"kind": {"const": "number"}}},
                "then": {"properties": {"value": {"type": "integer"}}, "required": ["value"]},
                "else": {"properties": {"value": {"type": "string"}}, "required": ["value"]},
            },
            {
                "type": "object",
                "properties": {"kind": {"type": "string"}},
                "anyOf": [
                    {
                        "allOf": [
                            {"properties": {"kind": {"const": "number"}}},
                            {"properties": {"value": {"type": "integer"}}, "required": ["value"]},
                        ]
                    },
                    {
                        "allOf": [
                            {"not": {"properties": {"kind": {"const": "number"}}}},
                            {"properties": {"value": {"type": "string"}}, "required": ["value"]},
                        ]
                    },
                ],
            },
            id="if_then_else_full",
        ),
        pytest.param(
            {
                "type": "object",
                "if": {"properties": {"kind": {"const": "number"}}},
                "then": {"properties": {"value": {"type": "integer"}}, "required": ["value"]},
            },
            {
                "type": "object",
                "anyOf": [
                    {
                        "allOf": [
                            {"properties": {"kind": {"const": "number"}}},
                            {"properties": {"value": {"type": "integer"}}, "required": ["value"]},
                        ]
                    },
                    {"not": {"properties": {"kind": {"const": "number"}}}},
                ],
            },
            id="if_then_only",
        ),
        pytest.param(
            {
                "type": "object",
                "if": {"properties": {"kind": {"const": "number"}}},
                "else": {"properties": {"value": {"type": "string"}}, "required": ["value"]},
            },
            {
                "type": "object",
                "anyOf": [
                    {"properties": {"kind": {"const": "number"}}},
                    {
                        "allOf": [
                            {"not": {"properties": {"kind": {"const": "number"}}}},
                            {"properties": {"value": {"type": "string"}}, "required": ["value"]},
                        ]
                    },
                ],
            },
            id="if_else_only",
        ),
        pytest.param(
            # `if` alone is a tautology — drop it.
            {"type": "object", "if": {"properties": {"kind": {"const": "number"}}}},
            {"type": "object"},
            id="if_alone",
        ),
        pytest.param(
            # Existing `anyOf` is preserved by composing through `allOf`.
            {
                "type": "object",
                "anyOf": [{"required": ["a"]}, {"required": ["b"]}],
                "if": {"properties": {"kind": {"const": "number"}}},
                "then": {"properties": {"value": {"type": "integer"}}, "required": ["value"]},
                "else": {"properties": {"value": {"type": "string"}}, "required": ["value"]},
            },
            {
                "type": "object",
                "allOf": [
                    {"anyOf": [{"required": ["a"]}, {"required": ["b"]}]},
                    {
                        "anyOf": [
                            {
                                "allOf": [
                                    {"properties": {"kind": {"const": "number"}}},
                                    {"properties": {"value": {"type": "integer"}}, "required": ["value"]},
                                ]
                            },
                            {
                                "allOf": [
                                    {"not": {"properties": {"kind": {"const": "number"}}}},
                                    {"properties": {"value": {"type": "string"}}, "required": ["value"]},
                                ]
                            },
                        ]
                    },
                ],
            },
            id="if_then_else_with_existing_anyof",
        ),
        pytest.param(
            # Existing `allOf` is extended with the new conditional anyOf.
            {
                "type": "object",
                "allOf": [{"required": ["a"]}],
                "if": {"properties": {"kind": {"const": "number"}}},
                "then": {"properties": {"value": {"type": "integer"}}, "required": ["value"]},
            },
            {
                "type": "object",
                "allOf": [
                    {"required": ["a"]},
                    {
                        "anyOf": [
                            {
                                "allOf": [
                                    {"properties": {"kind": {"const": "number"}}},
                                    {"properties": {"value": {"type": "integer"}}, "required": ["value"]},
                                ]
                            },
                            {"not": {"properties": {"kind": {"const": "number"}}}},
                        ]
                    },
                ],
            },
            id="if_then_with_existing_allof",
        ),
        pytest.param(
            # Nested if/then/else inside `properties` is also rewritten via the recursive walker.
            {
                "type": "object",
                "properties": {
                    "inner": {
                        "type": "object",
                        "if": {"properties": {"k": {"const": "x"}}},
                        "then": {"required": ["v"]},
                    }
                },
            },
            {
                "type": "object",
                "properties": {
                    "inner": {
                        "type": "object",
                        "anyOf": [
                            {
                                "allOf": [
                                    {"properties": {"k": {"const": "x"}}},
                                    {"required": ["v"]},
                                ]
                            },
                            {"not": {"properties": {"k": {"const": "x"}}}},
                        ],
                    }
                },
            },
            id="if_then_nested_in_properties",
        ),
    ],
)
def test_if_then_else_rewrite(schema, expected):
    result = transform(schema, converter.to_json_schema, nullable_keyword="x-nullable")
    assert result == expected


def test_validation_schema_preserves_if_then_else():
    # Validation paths keep the originals so JSON Schema validators evaluate the conditional natively.
    raw = {
        "type": "object",
        "if": {"properties": {"kind": {"const": "number"}}},
        "then": {"required": ["value"]},
    }
    result = transform(
        raw,
        converter.to_json_schema,
        nullable_keyword="x-nullable",
        convert_if_then_else=False,
    )
    assert result == {
        "type": "object",
        "if": {"properties": {"kind": {"const": "number"}}},
        "then": {"required": ["value"]},
    }


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
        # `^[abc\d]$` matches exactly one character, so a wider length range is unsatisfiable
        # and both bounds have to stay as keywords.
        (
            {
                "minLength": 3,
                "maxLength": 40,
                "pattern": r"^[abc\d]$",
            },
            {"minLength": 3, "maxLength": 40, "pattern": r"^[abc\d]$"},
        ),
        (
            {
                "maxLength": 40,
                "pattern": r"^[abc\d]$",
            },
            {"pattern": r"^[abc\d]{1}$"},
        ),
        (
            {
                "minLength": 3,
                "pattern": r"^[abc\d]$",
            },
            {"minLength": 3, "pattern": r"^[abc\d]$"},
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


@pytest.mark.parametrize(
    ("schema", "expected"),
    [
        (
            {"type": "array", "items": [{"type": "string"}, {"type": "integer"}]},
            {"type": "array", "prefixItems": [{"type": "string"}, {"type": "integer"}]},
        ),
        (
            {"type": "array", "items": [{"type": "string"}], "additionalItems": {"type": "boolean"}},
            {"type": "array", "prefixItems": [{"type": "string"}], "items": {"type": "boolean"}},
        ),
        (
            {"type": "array", "items": {"type": "string"}, "additionalItems": False},
            {"type": "array", "items": {"type": "string"}, "additionalItems": False},
        ),
    ],
    ids=["tuple", "tuple-with-additional-items", "single-items-schema"],
)
def test_upgrade_legacy_tuple_items(schema, expected):
    assert (
        transform(
            schema,
            converter.to_json_schema,
            nullable_keyword="nullable",
            upgrade_legacy_exclusive_bounds=True,
            convert_prefix_items=False,
        )
        == expected
    )


def test_does_not_upgrade_legacy_exclusive_bounds_by_default():
    schema = {"type": "number", "minimum": 0, "exclusiveMinimum": True}

    result = transform(schema, converter.to_json_schema, nullable_keyword="nullable")

    assert result == schema


def test_legacy_exclusive_flag_without_bound_dropped_on_openapi_3_1(ctx):
    schema = ctx.openapi.load_schema(
        {
            "/items": {
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {"application/json": {"schema": {"type": "number", "exclusiveMinimum": True}}},
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
        version="3.1.0",
    )
    assert schema["/items"]["POST"].body[0].optimized_schema == {"type": "number"}


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
        pytest.param(
            {
                "allOf": [
                    {"type": "object", "properties": {"legacy": False}},
                ],
                "required": ["legacy"],
            },
            False,
            {
                "allOf": [
                    {"type": "object", "properties": {"legacy": False}},
                ],
            },
            id="boolean_false_property",
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


@pytest.mark.parametrize(
    "pattern",
    [
        r"^[A-Za-z \p{Tibetan}\p{Thaana}]*$",
        r"[\p{Tibetan}&&[^|:/]]+",
        r"\p{Script=Latin}",
    ],
)
def test_pattern_the_validator_enforces_is_kept(pattern):
    # Dropping it would draw values the API turns down, and nothing downstream would notice.
    schema = {"type": "string", "pattern": pattern}
    result = transform(schema, converter.to_json_schema, nullable_keyword="x-nullable")
    assert result["pattern"] == pattern


def test_posix_class_keeps_its_posix_meaning_for_the_validator():
    # ECMA reads `[[:alnum:]]` as literal characters, but authors mean the POSIX class their server enforces.
    schema = {"type": "string", "pattern": "^[[:alnum:]]+$"}
    result = transform(schema, converter.to_json_schema, nullable_keyword="x-nullable")
    validator = jsonschema_rs.validator_for(result)
    assert [validator.is_valid(value) for value in ("abc1", ":]")] == [True, False]


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


@pytest.mark.parametrize(
    "value",
    [
        ["N", "E", None],
        [None],
    ],
)
def test_nullable_type_array_enum_accepts_null_in_responses(value):
    # A documented null is accepted when validating a response, matching `nullable: true`.
    converted = to_json_schema(
        {"type": "array", "items": {"type": ["string", "null"], "enum": ["N", "E", "S", "W"]}},
        nullable_keyword="nullable",
        is_response_schema=True,
    )
    validator = make_validator_for(converted)
    assert validator.is_valid(value)
    assert not validator.is_valid(["unknown"])


def test_nullable_type_array_enum_keeps_enum_for_requests():
    assert to_json_schema({"type": ["string", "null"], "enum": ["N", "E"]}, nullable_keyword="nullable") == {
        "type": ["string", "null"],
        "enum": ["N", "E"],
    }


@pytest.mark.parametrize("nullable_keyword", ["nullable", "x-nullable"], ids=["openapi-3.0", "swagger-2.0"])
def test_nullable_keyword_enum_keeps_enum_for_requests(nullable_keyword):
    assert to_json_schema(
        {"type": "string", "enum": ["N", "E"], nullable_keyword: True}, nullable_keyword=nullable_keyword
    ) == {"type": "string", "enum": ["N", "E"]}


@pytest.mark.parametrize("nullable_keyword", ["nullable", "x-nullable"], ids=["openapi-3.0", "swagger-2.0"])
def test_nullable_keyword_enum_accepts_null_in_responses(nullable_keyword):
    # A documented null is accepted when validating a response, matching `nullable: true`.
    validator = make_validator_for(
        to_json_schema(
            {"type": "string", "enum": ["N", "E"], nullable_keyword: True},
            nullable_keyword=nullable_keyword,
            is_response_schema=True,
        )
    )
    assert validator.is_valid(None)
    assert not validator.is_valid("unknown")


@pytest.mark.parametrize(
    "schema",
    [{"type": "string"}, {"type": "string", "enum": ["N", None]}],
    ids=["no-enum", "enum-with-null"],
)
@pytest.mark.parametrize("nullable_keyword", ["nullable", "x-nullable"], ids=["openapi-3.0", "swagger-2.0"])
def test_nullable_keyword_without_conflicting_enum_admits_null(schema, nullable_keyword):
    assert to_json_schema({**schema, nullable_keyword: True}, nullable_keyword=nullable_keyword) == {
        "anyOf": [schema, {"type": "null"}]
    }


def test_non_nullable_type_enum_still_rejects_null():
    converted = to_json_schema({"type": "string", "enum": ["N", "E"]}, nullable_keyword="nullable")
    validator = make_validator_for(converted)
    assert not validator.is_valid(None)


def test_discriminator_pin_reads_nullable_anyof_tag(ctx):
    # OpenAPI 3.1 spells a nullable tag as `anyOf`, putting its literal one level below the property.
    schema = ctx.openapi.load_schema(
        {
            "/items": {
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "anyOf": [{"$ref": "#/components/schemas/ItemReference"}],
                                    "discriminator": {"propertyName": "type"},
                                }
                            }
                        },
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
        version="3.1.0",
        components={
            "schemas": {
                "ItemReference": {
                    "type": "object",
                    "properties": {
                        "type": {
                            "anyOf": [
                                {"type": "string", "enum": ["item_reference"], "default": "item_reference"},
                                {"type": "null"},
                            ]
                        }
                    },
                    "required": ["type"],
                }
            }
        },
    )
    validator = make_validator(
        schema["/items"]["POST"].body[0].optimized_schema, schema.adapter.jsonschema_validator_cls
    )
    assert validator.is_valid({"type": "item_reference"})


def test_discriminator_pin_skipped_when_tag_has_several_candidates(ctx):
    # Two literals under one tag are ambiguous, so neither may be picked - and the schema name
    # is not one the tag accepts, so the branch is left unpinned rather than made unsatisfiable.
    schema = ctx.openapi.load_schema(
        {
            "/items": {
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "anyOf": [{"$ref": "#/components/schemas/Ambiguous"}],
                                    "discriminator": {"propertyName": "type"},
                                }
                            }
                        },
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
        version="3.1.0",
        components={
            "schemas": {
                "Ambiguous": {
                    "type": "object",
                    "properties": {"type": {"anyOf": [{"const": "first"}, {"const": "second"}]}},
                    "required": ["type"],
                }
            }
        },
    )
    validator = make_validator(
        schema["/items"]["POST"].body[0].optimized_schema, schema.adapter.jsonschema_validator_cls
    )
    assert validator.is_valid({"type": "first"})
    assert validator.is_valid({"type": "second"})
    assert not validator.is_valid({"type": "Ambiguous"})


def test_discriminator_pin_skipped_for_polymorphic_branch_target(ctx):
    # Schema-name fallback is wrong when the $ref target is itself polymorphic.
    schema = ctx.openapi.load_schema(
        {
            "/items": {
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "oneOf": [{"$ref": "#/components/schemas/Item"}],
                                    "discriminator": {"propertyName": "type"},
                                }
                            }
                        },
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
        version="3.1.0",
        components={
            "schemas": {
                "Item": {
                    "oneOf": [
                        {"$ref": "#/components/schemas/Msg"},
                        {"$ref": "#/components/schemas/Resp"},
                    ],
                },
                "Msg": {
                    "type": "object",
                    "properties": {"type": {"const": "msg"}},
                    "required": ["type"],
                },
                "Resp": {
                    "type": "object",
                    "properties": {"type": {"const": "resp"}},
                    "required": ["type"],
                },
            }
        },
    )
    validator = make_validator(
        schema["/items"]["POST"].body[0].optimized_schema, schema.adapter.jsonschema_validator_cls
    )
    assert validator.is_valid({"type": "msg"})
    assert validator.is_valid({"type": "resp"})
    assert not validator.is_valid({"type": "Item"})


def test_discriminator_pin_uses_file_name_for_whole_file_ref(tmp_path):
    (tmp_path / "cat.json").write_text(
        json.dumps(
            {
                "type": "object",
                "properties": {"kind": {"type": "string"}, "meow": {"type": "integer"}},
                "required": ["kind"],
            }
        )
    )
    (tmp_path / "openapi.json").write_text(
        json.dumps(
            {
                "openapi": "3.0.2",
                "info": {"title": "Test", "version": "1"},
                "paths": {
                    "/pets": {
                        "post": {
                            "requestBody": {
                                "required": True,
                                "content": {
                                    "application/json": {
                                        "schema": {
                                            "oneOf": [{"$ref": "cat.json"}],
                                            "discriminator": {"propertyName": "kind"},
                                        }
                                    }
                                },
                            },
                            "responses": {"200": {"description": "OK"}},
                        }
                    }
                },
            }
        )
    )
    schema = schemathesis.openapi.from_path(tmp_path / "openapi.json")
    kinds = set()

    @given(case=schema["/pets"]["POST"].as_strategy())
    @settings(max_examples=10, deadline=None)
    def test(case):
        kinds.add(case.body["kind"])

    test()
    assert kinds == {"cat"}


def test_discriminator_pin_applies_to_boolean_branch_target(ctx):
    schema = ctx.openapi.load_schema(
        {
            "/items": {
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "oneOf": [{"$ref": "#/components/schemas/Any"}],
                                    "discriminator": {"propertyName": "kind"},
                                }
                            }
                        },
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
        version="3.1.0",
        components={"schemas": {"Any": True}},
    )
    assert schema["/items"]["POST"].body[0].optimized_schema == {
        "oneOf": [{"allOf": [{"$ref": "#/x-bundled/schema000001"}, {"properties": {"kind": {"enum": ["Any"]}}}]}],
        "discriminator": {"propertyName": "kind"},
        "x-bundled": {"schema000001": True},
    }


def test_discriminator_pin_validates_with_openapi_3_0_draft4(ctx):
    # OpenAPI 3.0 uses Draft 4, which silently ignores `const`. Pin keyword must use `enum` so the
    # discriminator branches are actually disambiguated at validation time.
    schema = ctx.openapi.load_schema(
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
    body = schema["/rules"]["POST"].body[0]
    validator = make_validator(body.optimized_schema, schema.adapter.jsonschema_validator_cls)
    assert validator.is_valid({"type": "allow"}) is True, "branch-disambiguating pin must work under Draft 4"


INT32_MIN, INT32_MAX = -(2**31), 2**31 - 1
INT64_MIN, INT64_MAX = -(2**63), 2**63 - 1


@pytest.mark.parametrize(
    ("schema", "expected"),
    [
        pytest.param(
            {"type": "integer", "format": "int64"},
            {"type": "integer", "format": "int64", "minimum": INT64_MIN, "maximum": INT64_MAX},
            id="int64_unbounded",
        ),
        pytest.param(
            {"type": "integer", "format": "int32"},
            {"type": "integer", "format": "int32", "minimum": INT32_MIN, "maximum": INT32_MAX},
            id="int32_unbounded",
        ),
        pytest.param(
            {"type": "integer", "format": "int64", "minimum": 0, "maximum": 100},
            {"type": "integer", "format": "int64", "minimum": 0, "maximum": 100},
            id="explicit_tighter_bounds_kept",
        ),
        pytest.param(
            {"type": "integer", "format": "int64", "maximum": 10**30},
            {
                "type": "integer",
                "format": "int64",
                "minimum": INT64_MIN,
                "maximum": INT64_MAX,
                converter.DECLARED_MAXIMUM_KEY: 10**30,
            },
            id="explicit_looser_maximum_tightened",
        ),
        pytest.param(
            {"type": "integer", "format": "int32", "minimum": -(10**30)},
            {
                "type": "integer",
                "format": "int32",
                "minimum": INT32_MIN,
                "maximum": INT32_MAX,
                converter.DECLARED_MINIMUM_KEY: -(10**30),
            },
            id="explicit_looser_minimum_tightened",
        ),
        pytest.param(
            {"type": "object", "properties": {"a": {"type": "integer", "format": "int64"}}},
            {
                "type": "object",
                "properties": {"a": {"type": "integer", "format": "int64", "minimum": INT64_MIN, "maximum": INT64_MAX}},
            },
            id="nested_in_properties",
        ),
        pytest.param(
            {"type": "array", "items": {"type": "integer", "format": "int32"}},
            {
                "type": "array",
                "items": {"type": "integer", "format": "int32", "minimum": INT32_MIN, "maximum": INT32_MAX},
            },
            id="nested_in_items",
        ),
        pytest.param(
            {"type": ["integer", "null"], "format": "int64"},
            {"type": ["integer", "null"], "format": "int64", "minimum": INT64_MIN, "maximum": INT64_MAX},
            id="nullable_type_union",
        ),
        pytest.param({"type": "integer"}, {"type": "integer"}, id="no_format_untouched"),
        pytest.param(
            {"type": "string", "format": "uuid"}, {"type": "string", "format": "uuid"}, id="string_format_untouched"
        ),
        pytest.param(
            {"type": "string", "format": "int64"}, {"type": "string", "format": "int64"}, id="type_mismatch_untouched"
        ),
        pytest.param(
            {"type": "integer", "format": "unknown"},
            {"type": "integer", "format": "unknown"},
            id="unknown_format_untouched",
        ),
    ],
)
def test_integer_format_bounds(schema, expected):
    assert transform(schema, converter.to_json_schema, nullable_keyword="x-nullable") == expected


@pytest.mark.parametrize(
    "schema",
    [
        pytest.param({"type": "object", "properties": ["x"]}, id="properties_as_list"),
        pytest.param({"type": "object", "properties": "x"}, id="properties_as_string"),
        pytest.param(
            {"type": "object", "properties": ["x"], "additionalProperties": False, "required": ["y"]},
            id="properties_as_list_with_required_and_no_additional",
        ),
        pytest.param(
            {"type": "object", "required": ["a"], "allOf": [{"properties": ["x"]}]},
            id="properties_as_list_inside_allof",
        ),
    ],
)
def test_malformed_properties_left_alone(schema):
    assert to_json_schema(schema, nullable_keyword="nullable") == schema


@pytest.mark.parametrize(
    "body_schema",
    [
        pytest.param({"type": "object", "properties": ["x"]}, id="top_level"),
        pytest.param({"type": "object", "allOf": [{"type": "object", "properties": ["x"]}]}, id="inside_allof"),
    ],
)
def test_malformed_properties_do_not_raise_for_operation(ctx, body_schema):
    schema = ctx.openapi.load_schema(
        {
            "/test": {
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {"application/json": {"schema": body_schema}},
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            }
        }
    )
    assert schema["/test"]["POST"].get_strategies_from_examples() == []
