from urllib.parse import urlparse

import pytest
import requests
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from hypothesis_jsonschema import from_schema
from hypothesis_jsonschema._canonicalise import FALSEY, canonicalish
from jsonschema import Draft4Validator

import schemathesis
from schemathesis.generation import DataGenerationMethod, GenerationConfig
from schemathesis.internal.copy import fast_deepcopy
from schemathesis.specs.openapi._hypothesis import get_default_format_strategies, is_valid_header
from schemathesis.specs.openapi.constants import LOCATION_TO_CONTAINER
from schemathesis.specs.openapi.negative import mutated, negative_schema
from schemathesis.specs.openapi.negative.mutations import (
    MutationContext,
    MutationResult,
    change_items,
    change_properties,
    change_type,
    negate_constraints,
    prevent_unsatisfiable_schema,
    remove_required_property,
)
from schemathesis.specs.openapi.utils import is_header_location
from test.utils import assert_requests_call

MAX_EXAMPLES = 15
SUPPRESSED_HEALTH_CHECKS = [HealthCheck.too_slow, HealthCheck.filter_too_much, HealthCheck.data_too_large]
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


validate_schema = Draft4Validator.check_schema


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
@settings(deadline=None, suppress_health_check=SUPPRESSED_HEALTH_CHECKS, max_examples=MAX_EXAMPLES)
def test_top_level_strategy(data, location, schema):
    if location != "body" and schema.get("type") == "object":
        # It always comes this way from Schemathesis
        schema["additionalProperties"] = False
    validate_schema(schema)
    validator = Draft4Validator(schema)
    schema = fast_deepcopy(schema)
    instance = data.draw(
        negative_schema(
            schema,
            operation_name="GET /users/",
            location=location,
            media_type="application/json",
            custom_formats=get_default_format_strategies(),
            generation_config=GenerationConfig(),
        )
    )
    assert not validator.is_valid(instance)
    if is_header_location(location):
        assert is_valid_header(instance)


@pytest.mark.parametrize(
    "mutation, schema, location, validate",
    (
        # No constraints besides `type`
        (negate_constraints, {"type": "integer"}, "body", True),
        # Missing type (i.e. all types are possible)
        (change_type, {}, "body", True),
        # All types explicitly
        (change_type, {"type": ["string", "integer", "number", "object", "array", "boolean", "null"]}, "body", True),
        # No properties to remove
        (remove_required_property, {}, "body", True),
        # Non-"object" type
        (remove_required_property, {"type": "array"}, "body", True),
        # No properties at all
        (change_properties, {}, "body", True),
        # No properties that can be mutated
        (change_properties, {"properties": {"foo": {}}}, "body", True),
        # No items
        (change_items, {"type": "array"}, "body", True),
        # `items` accept everything
        (change_items, {"type": "array", "items": {}}, "body", True),
        (change_items, {"type": "array", "items": True}, "body", False),
        # `items` is equivalent to accept-everything schema
        (change_items, {"type": "array", "items": {"uniqueItems": False}}, "body", True),
        # The first element could be anything
        (change_items, {"type": "array", "items": [{}]}, "body", True),
        # Query and path parameters are always strings
        (change_type, {"type": "string"}, "path", True),
        (change_type, {"type": "string"}, "query", True),
    ),
)
@given(data=st.data())
@settings(deadline=None, suppress_health_check=SUPPRESSED_HEALTH_CHECKS, max_examples=MAX_EXAMPLES)
def test_failing_mutations(data, mutation, schema, location, validate):
    if validate:
        validate_schema(schema)
    original_schema = fast_deepcopy(schema)
    # When mutation can't be applied
    # Then it returns "failure"
    assert (
        mutation(MutationContext(schema, {}, location, "application/json"), data.draw, schema) == MutationResult.FAILURE
    )
    # And doesn't mutate the input schema
    assert schema == original_schema


@given(data=st.data())
@settings(deadline=None, suppress_health_check=SUPPRESSED_HEALTH_CHECKS, max_examples=MAX_EXAMPLES)
def test_change_type_urlencoded(data):
    # When `application/x-www-form-urlencoded` media type is passed to `change_type`
    schema = {"type": "object"}
    original_schema = fast_deepcopy(schema)
    context = MutationContext(schema, {}, "body", "application/x-www-form-urlencoded")
    # Then it should not be mutated
    assert change_type(context, data.draw, schema) == MutationResult.FAILURE
    # And doesn't mutate the input schema
    assert schema == original_schema


@pytest.mark.parametrize(
    "mutation, schema",
    (
        (negate_constraints, {"type": "integer", "minimum": 42}),
        (negate_constraints, {"minimum": 42}),
        (change_type, {"type": "object"}),
        (change_type, {"type": ["object", "array"]}),
        (change_type, {"type": ["string", "integer", "number", "object", "array", "boolean"]}),
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
@settings(deadline=None, suppress_health_check=SUPPRESSED_HEALTH_CHECKS, max_examples=MAX_EXAMPLES)
def test_successful_mutations(data, mutation, schema):
    validate_schema(schema)
    validator = Draft4Validator(schema)
    schema = fast_deepcopy(schema)
    # When mutation can be applied
    # Then it returns "success"
    assert (
        mutation(MutationContext(schema, {}, "body", "application/json"), data.draw, schema) == MutationResult.SUCCESS
    )
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
                "foo": {"type": "integer"},
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
@settings(deadline=None, suppress_health_check=SUPPRESSED_HEALTH_CHECKS, max_examples=MAX_EXAMPLES)
def test_path_parameters_are_string(data, schema):
    validator = Draft4Validator(schema)
    new_schema = fast_deepcopy(schema)
    # When path parameters are mutated
    new_schema = data.draw(mutated(new_schema, {}, "path", None))
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
@settings(deadline=None, suppress_health_check=SUPPRESSED_HEALTH_CHECKS, max_examples=MAX_EXAMPLES)
def test_custom_fields_are_intact(data, key):
    # When the schema contains some non-JSON Schema keywords (e.g. components from Open API)
    schema = {
        "type": "object",
        "properties": {"X-Foo": {"type": "string", "maxLength": 5}},
        "additionalProperties": False,
    }
    # Then they should not be negated
    new_schema = data.draw(mutated(schema, {key: {}}, "body", "application/json"))
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
@settings(deadline=None, suppress_health_check=SUPPRESSED_HEALTH_CHECKS, max_examples=MAX_EXAMPLES)
def test_negate_constraints_keep_dependencies(data, schema):
    # When `negate_constraints` is used
    schema = fast_deepcopy(schema)
    negate_constraints(MutationContext(schema, {}, "body", "application/json"), data.draw, schema)
    # Then it should always produce valid schemas
    validate_schema(schema)
    # E.g. `exclusiveMaximum` / `exclusiveMinimum` only work when `maximum` / `minimum` are present in the same schema


@given(data=st.data())
@settings(deadline=None, suppress_health_check=SUPPRESSED_HEALTH_CHECKS, max_examples=MAX_EXAMPLES)
def test_no_unsatisfiable_schemas(data):
    schema = {"type": "object", "required": ["foo"]}
    mutated_schema = data.draw(mutated(schema, {}, location="body", media_type="application/json"))
    assert canonicalish(mutated_schema) != FALSEY


@pytest.mark.hypothesis_nested
def test_optional_query_param_negation(empty_open_api_3_schema):
    # When all query parameters are optional
    empty_open_api_3_schema["paths"]["/bug"] = {
        "get": {
            "parameters": [
                {"name": "key1", "in": "query", "required": False, "schema": {"type": "string"}},
            ],
            "responses": {"200": {"description": "OK"}},
        }
    }

    schema = schemathesis.from_dict(empty_open_api_3_schema)

    @given(case=schema["/bug"]["get"].as_strategy(data_generation_method=DataGenerationMethod.negative))
    @settings(deadline=None, max_examples=10, suppress_health_check=SUPPRESSED_HEALTH_CHECKS)
    def test(case):
        request = requests.PreparedRequest()
        request.prepare(**case.as_transport_kwargs(base_url="http://127.0.0.1"))
        # Then negative schema should not generate empty queries
        assert urlparse(request.url).query != ""

    test()


@pytest.mark.parametrize(
    "schema, new_type",
    (
        ({"type": "object", "required": ["a"]}, "string"),
        ({"required": ["a"], "not": {"maxLength": 5}}, "string"),
    ),
)
def test_prevent_unsatisfiable_schema(schema, new_type):
    prevent_unsatisfiable_schema(schema, new_type)
    assert canonicalish(schema) != FALSEY


ARRAY_PARAMETER = {"type": "array", "minItems": 1, "items": {"type": "string", "format": "ipv4"}}
OBJECT_PARAMETER = {
    "type": "object",
    "minProperties": 1,
    "properties": {"foo": {"type": "string", "format": "ipv4"}, "bar": {"type": "string", "format": "ipv4"}},
    "additionalProperties": False,
}
DYNAMIC_OBJECT_PARAMETER = {"type": "object", "additionalProperties": {"type": "string"}}


@pytest.mark.parametrize("explode", (True, False))
@pytest.mark.parametrize(
    "location, schema, style",
    [("query", ARRAY_PARAMETER, style) for style in ("pipeDelimited", "spaceDelimited")]
    + [("query", OBJECT_PARAMETER, "deepObject")]
    + [("query", DYNAMIC_OBJECT_PARAMETER, "form")]
    + [
        ("path", parameter, style)
        for parameter in [OBJECT_PARAMETER, ARRAY_PARAMETER]
        for style in ("simple", "label", "matrix")
    ],
)
@pytest.mark.hypothesis_nested
def test_non_default_styles(empty_open_api_3_schema, location, schema, style, explode):
    # See GH-1208
    # When the schema contains a parameter with a not-default "style"
    empty_open_api_3_schema["paths"]["/bug"] = {
        "get": {
            "parameters": [
                {"name": "key", "in": location, "required": True, "style": style, "explode": explode, "schema": schema},
            ],
            "responses": {"200": {"description": "OK"}},
        }
    }

    schema = schemathesis.from_dict(empty_open_api_3_schema)

    @given(case=schema["/bug"]["get"].as_strategy(data_generation_method=DataGenerationMethod.negative))
    @settings(deadline=None, max_examples=10, suppress_health_check=SUPPRESSED_HEALTH_CHECKS)
    def test(case):
        assert_requests_call(case)

    test()
