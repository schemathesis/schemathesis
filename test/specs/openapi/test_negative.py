from copy import deepcopy

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from hypothesis_jsonschema import from_schema
from jsonschema import Draft4Validator

from schemathesis.specs.openapi.constants import LOCATION_TO_CONTAINER
from schemathesis.specs.openapi.negative import negative_schema
from schemathesis.specs.openapi.negative.mutations import (
    MutationResult,
    change_properties,
    change_schema_type,
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
def test_negative(schema, location):
    validator = Draft4Validator(schema)

    @given(negative_schema(schema, location=location, custom_formats={}))
    @settings(max_examples=10)
    def test(instance):
        assert not validator.is_valid(instance)

    test()


@pytest.mark.parametrize(
    "schema, expected",
    (
        ({"properties": {"foo": {}}, "required": ["foo"]}, {"properties": {}}),
        ({"properties": {"foo": {}, "bar": {}}, "required": ["foo"]}, {"properties": {"bar": {}}}),
    ),
)
@given(data=st.data())
def test_remove_required_property_success(data, schema, expected):
    schema = deepcopy(schema)
    assert remove_required_property(data.draw, schema) == MutationResult.SUCCESS
    assert schema == expected


@given(data=st.data())
def test_remove_required_property_failure(data):
    # Can't apply this mutation to an empty schema
    assert remove_required_property(data.draw, {}) == MutationResult.FAILURE


@pytest.mark.parametrize(
    "schema",
    ({"type": "object"}, {"type": ["object", "array"]}),
)
@given(data=st.data())
def test_change_schema_type_success(data, schema):
    types = schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
    schema = deepcopy(schema)
    assert change_schema_type(data.draw, schema) == MutationResult.SUCCESS
    assert all(schema["type"] != type_ for type_ in types)


@given(data=st.data())
def test_change_schema_type_failure(data):
    assert change_schema_type(data.draw, {}) == MutationResult.FAILURE


@given(data=st.data())
def test_negate_schema_success(data):
    schema = {"type": "object"}
    assert negate_schema(data.draw, schema) == MutationResult.SUCCESS
    assert schema == {"not": {"type": "object"}}


@pytest.mark.parametrize("schema", ({}, {"uniqueItems": False}))
@given(data=st.data())
def test_negate_schema_failure(data, schema):
    assert negate_schema(data.draw, schema) == MutationResult.FAILURE


@pytest.mark.parametrize(
    "schema",
    (
        {"properties": {"foo": {"type": "integer"}}, "type": "object", "required": ["foo"]},
        # TODO. check it without `required` / `type`
    ),
)
@given(data=st.data())
def test_change_properties_success(data, schema):
    schema = {"properties": {"foo": {"type": "integer"}}, "type": "object", "required": ["foo"]}
    validator = Draft4Validator(schema)
    schema = deepcopy(schema)
    # When the schema is mutated
    assert change_properties(data.draw, schema) == MutationResult.SUCCESS
    # Then its instances should not be valid against the original schema
    new_instance = data.draw(from_schema(schema))
    assert not validator.is_valid(new_instance)
