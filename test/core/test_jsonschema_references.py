from typing import Any

import pytest

from schemathesis.core.jsonschema.references import sanitize


def ref_schema(ref: str) -> dict[str, Any]:
    return {"$ref": ref}


def object_schema(properties: dict[str, Any] = None, required: list[str] = None, **kwargs) -> dict[str, Any]:
    schema = {"type": "object"}
    if properties:
        schema["properties"] = properties
    if required:
        schema["required"] = required
    schema.update(kwargs)
    return schema


def array_schema(items: Any, **kwargs) -> dict[str, Any]:
    """Helper to create an array schema."""
    return {"type": "array", "items": items, **kwargs}


@pytest.mark.parametrize(
    ["schema", "expected"],
    [
        (True, set()),
        # Optional properties - should be removed
        (object_schema({"optional": ref_schema("#/ref1")}), set()),
        # Required properties - should remain
        (object_schema({"required": ref_schema("#/ref1")}, required=["required"]), {"#/ref1"}),
        # Mixed properties
        (
            object_schema({"optional": ref_schema("#/ref1"), "required": ref_schema("#/ref2")}, required=["required"]),
            {"#/ref2"},
        ),
        # Optional array items - should be converted to empty array
        (array_schema(ref_schema("#/ref1")), set()),
        (array_schema([ref_schema("#/ref1"), ref_schema("#/ref2")]), set()),
        # Required array items - should remain
        (array_schema(ref_schema("#/ref1"), minItems=1), {"#/ref1"}),
        # Additional properties - should be disabled
        ({"type": "object", "additionalProperties": ref_schema("#/ref1")}, set()),
        # Single-item combinators - should be removed
        ({"allOf": [ref_schema("#/ref1")]}, set()),
        ({"oneOf": [ref_schema("#/ref1")]}, set()),
        ({"anyOf": [ref_schema("#/ref1")]}, set()),
        # Multi-item combinators - should remain
        ({"allOf": [ref_schema("#/ref1"), {"type": "string"}]}, {"#/ref1"}),
        # Nested structures
        (
            object_schema(
                {
                    "optional": {"allOf": [ref_schema("#/ref1")]},
                    "required": array_schema(ref_schema("#/ref2"), minItems=1),
                },
                required=["required"],
            ),
            {"#/ref2"},
        ),
        # Complex nested case - all optional references removed
        (object_schema({"level1": object_schema({"level2": array_schema(ref_schema("#/ref1"))})}), set()),
        (object_schema({"$ref": ref_schema("#/ref1")}, required=["$ref"]), {"#/ref1"}),
        (object_schema({"key": True}), set()),
        (object_schema(additionalProperties=True), set()),
        (object_schema({"key": {"anyOf": [True]}}), set()),
        (object_schema(anyOf=[True]), set()),
        (object_schema(anyOf=[{"type": "object"}]), set()),
        # Incorrect but should not fail
        (object_schema({"key": []}), set()),
        ([], set()),
    ],
)
def test_sanitize(schema, expected):
    assert sanitize(schema) == expected


@pytest.mark.parametrize(
    "schema, expected_modification",
    [
        # Array items should be converted to empty array
        (array_schema(ref_schema("#/ref1")), {"maxItems": 0}),
        # Additional properties should be disabled
        ({"additionalProperties": ref_schema("#/ref1")}, {"additionalProperties": False}),
    ],
)
def test_sanitize_schema_modifications(schema, expected_modification):
    sanitize(schema)
    for key, expected_value in expected_modification.items():
        assert schema[key] == expected_value


def test_sanitize_preserves_schema_without_references():
    original = object_schema({"name": {"type": "string"}}, required=["name"])
    schema = original.copy()

    assert sanitize(schema) == set()
    assert schema == original
