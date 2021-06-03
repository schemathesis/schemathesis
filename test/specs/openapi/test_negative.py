from copy import deepcopy

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from hypothesis_jsonschema import from_schema
from hypothesis_jsonschema._canonicalise import FALSEY, canonicalish
from jsonschema import Draft4Validator

from schemathesis.specs.openapi._hypothesis import STRING_FORMATS, is_valid_header
from schemathesis.specs.openapi.constants import LOCATION_TO_CONTAINER
from schemathesis.specs.openapi.negative import mutated, negative_schema
from schemathesis.specs.openapi.negative.mutations import (
    MutationContext,
    MutationResult,
    change_items,
    change_properties,
    change_type,
    negate_constraints,
    remove_required_property,
)
from schemathesis.specs.openapi.utils import is_header_location

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
    Draft4Validator.check_schema(schema)


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
@settings(suppress_health_check=[HealthCheck.too_slow, HealthCheck.filter_too_much])
def test_top_level_strategy(data, location, schema):
    if location != "body" and schema.get("type") == "object":
        # It always comes this way from Schemathesis
        schema["additionalProperties"] = False
    validate_schema(schema)
    validator = Draft4Validator(schema)
    schema = deepcopy(schema)
    instance = data.draw(
        negative_schema(
            schema,
            operation_name="GET /users/",
            location=location,
            media_type="application/json",
            custom_formats=STRING_FORMATS,
        )
    )
    assert not validator.is_valid(instance)
    if is_header_location(location):
        assert is_valid_header(instance)


@pytest.mark.parametrize(
    "mutation, schema",
    (
        # No constraints besides `type`
        (negate_constraints, {"type": "integer"}),
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
    assert mutation(MutationContext(schema, "body", "application/json"), data.draw, schema) == MutationResult.FAILURE
    # And doesn't mutate the input schema
    assert schema == original_schema


@pytest.mark.parametrize(
    "mutation, schema",
    (
        (negate_constraints, {"type": "integer", "minimum": 42}),
        (negate_constraints, {"minimum": 42}),
        (change_type, {"type": "object"}),
        (change_type, {"type": ["object", "array"]}),
        (remove_required_property, {"properties": {"foo": {}}, "required": ["foo"]}),
        (remove_required_property, {"properties": {"foo": {}, "bar": {}}, "required": ["foo"]}),
        (remove_required_property, {"required": ["foo"]}),
        (change_items, {"type": "array", "items": {"type": "string"}}),
        (change_items, {"type": "array", "items": {"type": "string"}, "minItems": 1}),
        (change_items, {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 1}),
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
@settings(deadline=None, suppress_health_check=[HealthCheck.too_slow, HealthCheck.filter_too_much])
def test_successful_mutations(data, mutation, schema):
    validate_schema(schema)
    validator = Draft4Validator(schema)
    schema = deepcopy(schema)
    # When mutation can be applied
    # Then it returns "success"
    assert mutation(MutationContext(schema, "body", "application/json"), data.draw, schema) == MutationResult.SUCCESS
    # And the mutated schema is a valid JSON Schema
    validate_schema(schema)
    # And instances valid for this schema are not valid for the original one
    new_instance = data.draw(from_schema(schema))
    assert not validator.is_valid(new_instance)


@pytest.mark.parametrize(
    "schema",
    (
        {
            "type": "object",
            "properties": {
                "foo": {"type": "string"},
            },
            "required": [
                "foo",
            ],
            "additionalProperties": False,
        },
        {
            "type": "object",
            "properties": {
                "foo": {"type": "string", "minLength": 5},
            },
            "required": [
                "foo",
            ],
            "additionalProperties": False,
        },
    ),
)
@given(data=st.data())
@settings(deadline=None, suppress_health_check=[HealthCheck.too_slow, HealthCheck.filter_too_much])
def test_path_parameters_are_string(data, schema):
    validator = Draft4Validator(schema)
    new_schema = deepcopy(schema)
    # When path parameters are mutated
    new_schema = data.draw(mutated(new_schema, "path", None))
    assert new_schema["type"] == "object"
    # Then mutated schema is a valid JSON Schema
    validate_schema(new_schema)
    # And parameters remain primitive types
    new_instance = data.draw(from_schema(new_schema))
    assert not isinstance(new_instance["foo"], (list, dict))
    # And there should be no additional parameters
    assert len(new_instance) == 1
    # And instances valid for this schema are not valid for the original one
    assert not validator.is_valid(new_instance)


@pytest.mark.parametrize("key", ("components", "description"))
@given(data=st.data())
def test_custom_fields_are_intact(data, key):
    # When the schema contains some non-JSON Schema keywords (e.g. components from Open API)
    schema = {
        "type": "object",
        "properties": {"X-Foo": {"type": "string", "maxLength": 5}},
        "additionalProperties": False,
        key: {},
    }
    # Then they should not be negated
    new_schema = data.draw(mutated(schema, "body", "application/json"))
    assert key in new_schema


@pytest.mark.parametrize(
    "left, right, expected",
    (
        (MutationResult.SUCCESS, MutationResult.SUCCESS, MutationResult.SUCCESS),
        (MutationResult.FAILURE, MutationResult.SUCCESS, MutationResult.SUCCESS),
        (MutationResult.SUCCESS, MutationResult.FAILURE, MutationResult.SUCCESS),
        (MutationResult.FAILURE, MutationResult.FAILURE, MutationResult.FAILURE),
    ),
)
def test_mutation_result_success(left, right, expected):
    assert left | right == expected
    left |= right
    assert left == expected


@pytest.mark.parametrize(
    "schema",
    (
        {"minimum": 5, "exclusiveMinimum": True},
        {"maximum": 5, "exclusiveMaximum": True},
        {"maximum": 5, "exclusiveMaximum": True, "minimum": 1, "exclusiveMinimum": True},
    ),
)
@given(data=st.data())
def test_negate_constraints_keep_dependencies(data, schema):
    # When `negate_constraints` is used
    schema = deepcopy(schema)
    negate_constraints(MutationContext(schema, "body", "application/json"), data.draw, schema)
    # Then it should always produce valid schemas
    validate_schema(schema)
    # E.g. `exclusiveMaximum` / `exclusiveMinimum` only work when `maximum` / `minimum` are present in the same schema


@given(data=st.data())
def test_no_unsatisfiable_schemas(data):
    schema = {"type": "object", "required": ["foo"]}
    mutated_schema = data.draw(mutated(schema, location="body", media_type="application/json"))
    assert canonicalish(mutated_schema) != FALSEY
