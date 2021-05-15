from copy import deepcopy

import pytest
from hypothesis import given
from hypothesis import strategies as st
from hypothesis_jsonschema import from_schema
from jsonschema import Draft7Validator

from schemathesis.specs.openapi.constants import LOCATION_TO_CONTAINER
from schemathesis.specs.openapi.negative import negative_schema
from schemathesis.specs.openapi.negative.mutations import (
    MutationResult,
    change_items,
    change_properties,
    change_type,
    negate_constraints,
    negate_schema,
    remove_required_property,
)

OBJECT_SCHEMA = {
    "type": "object",
    "properties": {
        "foo": {"type": "string"},
        "bar": {"type": "integer"},
        "baf": {"type": ["integer"]},
        "baz": {"type": ["array", "object"]},
        "bad": {},
    },
    "required": [
        "foo",
        "bar",
        "baf",
        "baz",
    ],
}
ARRAY_SCHEMA = {"type": "array", "items": OBJECT_SCHEMA}
EMPTY_OBJECT_SCHEMA = {
    "type": "object",
}
INTEGER_SCHEMA = {
    "type": "integer",
}


def validate_schema(schema):
    Draft7Validator.check_schema(schema)


@pytest.mark.parametrize(
    "location, schema",
    [(location, OBJECT_SCHEMA) for location in sorted(LOCATION_TO_CONTAINER)]
    + [
        # These schemas are only possible for "body"
        ("body", EMPTY_OBJECT_SCHEMA),
        ("body", ARRAY_SCHEMA),
        ("body", INTEGER_SCHEMA),
    ],
)
@given(data=st.data())
def test_top_level_strategy(data, location, schema):
    validate_schema(schema)
    validator = Draft7Validator(schema)
    instance = data.draw(negative_schema(schema, location=location, custom_formats={}))
    assert not validator.is_valid(instance)


@pytest.mark.parametrize(
    "mutation, schema",
    (
        # No constraints besides `type`
        (negate_constraints, {"type": "integer"}),
        # Accept-everything schema
        (negate_schema, {}),
        # Equivalent to accept-everything schema
        (negate_schema, {"uniqueItems": False}),
        # Missing type (i.e all types are possible)
        (change_type, {}),
        # All types explicitly
        (change_type, {"type": ["string", "integer", "number", "object", "array", "boolean", "null"]}),
        # No properties to remove
        (remove_required_property, {}),
        # Non-"object" type
        (remove_required_property, {"type": "array"}),
        # No properties at all
        (change_properties, {}),
        # No properties that can be mutated
        (change_properties, {"properties": {"foo": {}}}),
        # No items
        (change_items, {"type": "array"}),
        # `items` accept everything
        (change_items, {"type": "array", "items": True}),
        (change_items, {"type": "array", "items": {}}),
        # `items` is equivalent to accept-everything schema
        (change_items, {"type": "array", "items": {"uniqueItems": False}}),
        # The first element could be anything
        (change_items, {"type": "array", "items": [{}]}),
    ),
)
@given(data=st.data())
def test_failing_mutations(data, mutation, schema):
    validate_schema(schema)
    original_schema = deepcopy(schema)
    # When mutation can't be applied
    # Then it returns "failure"
    assert mutation(data.draw, schema) == MutationResult.FAILURE
    # And doesn't mutate the input schema
    assert schema == original_schema


@pytest.mark.parametrize(
    "mutation, schema",
    (
        (negate_constraints, {"type": "integer", "minimum": 42}),
        (negate_constraints, {"minimum": 42}),
        (negate_schema, {"type": "object"}),
        (change_type, {"type": "object"}),
        (change_type, {"type": ["object", "array"]}),
        (remove_required_property, {"properties": {"foo": {}}, "required": ["foo"]}),
        (remove_required_property, {"properties": {"foo": {}, "bar": {}}, "required": ["foo"]}),
        (change_items, {"type": "array", "items": {"type": "string"}}),
        (change_items, {"type": "array", "items": {"type": "string"}, "minItems": 1}),
        (change_items, {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 1}),
        (change_items, {"type": "array", "items": False}),
        (change_items, {"type": "array", "items": [{"type": "string"}]}),
        (change_items, {"type": "array", "items": [{"type": "string"}], "minItems": 1}),
        (change_items, {"type": "array", "items": [{"type": "string"}], "minItems": 1, "maxItems": 1}),
        (change_properties, {"properties": {"foo": {"type": "integer"}}, "type": "object", "required": ["foo"]}),
        (change_properties, {"properties": {"foo": {"type": "integer"}}, "type": ["object"]}),
        (change_properties, {"properties": {"foo": {"type": "integer"}}, "type": "object"}),
        (change_properties, {"properties": {"foo": {"type": "integer"}}}),
        (
            change_properties,
            {
                "properties": {"foo": {"type": "string", "minLength": 5}, "bar": {"type": "string", "minLength": 5}},
                "type": "object",
                "required": ["foo", "bar"],
                "additionalProperties": False,
            },
        ),
    ),
)
@given(data=st.data())
def test_successful_mutations(data, mutation, schema):
    validate_schema(schema)
    validator = Draft7Validator(schema)
    schema = deepcopy(schema)
    # When mutation can be applied
    # Then it returns "success"
    assert mutation(data.draw, schema) == MutationResult.SUCCESS
    # And the mutated schema is a valid JSON Schema
    validate_schema(schema)
    # And instances valid for this schema are not valid for the original one
    new_instance = data.draw(from_schema(schema))
    assert not validator.is_valid(new_instance)
