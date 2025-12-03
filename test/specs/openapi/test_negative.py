import uuid
from urllib.parse import urlparse

import pytest
import requests
from flask import Flask, jsonify
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from hypothesis_jsonschema import from_schema
from hypothesis_jsonschema._canonicalise import FALSEY, canonicalish
from jsonschema import Draft4Validator, Draft202012Validator

import schemathesis
from schemathesis.config import GenerationConfig
from schemathesis.core.parameters import ParameterLocation
from schemathesis.core.transforms import deepclone
from schemathesis.generation import GenerationMode
from schemathesis.openapi.generation.filters import is_valid_header
from schemathesis.specs.openapi._hypothesis import get_default_format_strategies
from schemathesis.specs.openapi.negative import GeneratedValue, mutated, negative_schema
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
    ("location", "schema"),
    [(location, OBJECT_SCHEMA) for location in sorted(set(ParameterLocation) - {ParameterLocation.UNKNOWN})]
    + [
        # These schemas are only possible for "body"
        (ParameterLocation.BODY, EMPTY_OBJECT_SCHEMA),
        (ParameterLocation.BODY, ARRAY_SCHEMA),
        (ParameterLocation.BODY, INTEGER_SCHEMA),
    ],
)
@given(data=st.data())
@settings(deadline=None, suppress_health_check=SUPPRESSED_HEALTH_CHECKS, max_examples=MAX_EXAMPLES)
def test_top_level_strategy(data, location, schema):
    if location != ParameterLocation.BODY and schema.get("type") == "object":
        # It always comes this way from Schemathesis
        schema["additionalProperties"] = False
    validate_schema(schema)
    validator = Draft4Validator(schema)
    result = data.draw(
        negative_schema(
            schema,
            operation_name="GET /users/",
            location=location,
            media_type="application/json",
            custom_formats=get_default_format_strategies(),
            generation_config=GenerationConfig(),
            validator_cls=Draft4Validator,
        )
    )
    assert isinstance(result, GeneratedValue)
    instance = result.value
    assert not validator.is_valid(instance)
    if location.is_in_header:
        assert is_valid_header(instance)


@pytest.mark.parametrize(
    ("mutation", "schema", "location", "validate"),
    [
        # No constraints besides `type`
        (negate_constraints, {"type": "integer"}, ParameterLocation.BODY, True),
        # Missing type (i.e. all types are possible)
        (change_type, {}, ParameterLocation.BODY, True),
        # All types explicitly
        (
            change_type,
            {"type": ["string", "integer", "number", "object", "array", "boolean", "null"]},
            ParameterLocation.BODY,
            True,
        ),
        # No properties to remove
        (remove_required_property, {}, ParameterLocation.BODY, True),
        # Non-"object" type
        (remove_required_property, {"type": "array"}, ParameterLocation.BODY, True),
        # No properties at all
        (change_properties, {}, ParameterLocation.BODY, True),
        # No properties that can be mutated
        (change_properties, {"properties": {"foo": {}}}, ParameterLocation.BODY, True),
        # No items
        (change_items, {"type": "array"}, ParameterLocation.BODY, True),
        # `items` accept everything
        (change_items, {"type": "array", "items": {}}, ParameterLocation.BODY, True),
        (change_items, {"type": "array", "items": True}, ParameterLocation.BODY, False),
        # `items` is equivalent to accept-everything schema
        (change_items, {"type": "array", "items": {"uniqueItems": False}}, ParameterLocation.BODY, True),
        # The first element could be anything
        (change_items, {"type": "array", "items": [{}]}, ParameterLocation.BODY, True),
        # Query and path parameters are always strings
        (change_type, {"type": "string"}, ParameterLocation.PATH, True),
        (change_type, {"type": "string"}, ParameterLocation.QUERY, True),
    ],
)
@given(data=st.data())
@settings(deadline=None, suppress_health_check=SUPPRESSED_HEALTH_CHECKS, max_examples=MAX_EXAMPLES)
def test_failing_mutations(data, mutation, schema, location, validate):
    if validate:
        validate_schema(schema)
    original_schema = deepclone(schema)
    # When mutation can't be applied
    # Then it returns "failure"
    result, metadata = mutation(
        MutationContext(
            keywords=schema,
            non_keywords={},
            location=location,
            media_type="application/json",
            allow_extra_parameters=True,
        ),
        data.draw,
        schema,
    )
    assert result == MutationResult.FAILURE
    assert metadata is None
    # And doesn't mutate the input schema
    assert schema == original_schema


@given(data=st.data())
@settings(deadline=None, suppress_health_check=SUPPRESSED_HEALTH_CHECKS, max_examples=MAX_EXAMPLES)
def test_change_type_urlencoded(data):
    # When `application/x-www-form-urlencoded` media type is passed to `change_type`
    schema = {"type": "object"}
    original_schema = deepclone(schema)
    context = MutationContext(
        keywords=schema,
        non_keywords={},
        location=ParameterLocation.BODY,
        media_type="application/x-www-form-urlencoded",
        allow_extra_parameters=True,
    )
    # Then it should not be mutated
    result, metadata = change_type(context, data.draw, schema)
    assert result == MutationResult.FAILURE
    assert metadata is None
    # And doesn't mutate the input schema
    assert schema == original_schema


@pytest.mark.parametrize(
    ("mutation", "schema"),
    [
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
    ],
)
@given(data=st.data())
@settings(deadline=None, suppress_health_check=SUPPRESSED_HEALTH_CHECKS, max_examples=MAX_EXAMPLES)
def test_successful_mutations(data, mutation, schema):
    validate_schema(schema)
    validator = Draft4Validator(schema)
    schema = deepclone(schema)
    # When mutation can be applied
    # Then it returns "success"
    result, metadata = mutation(
        MutationContext(
            keywords=schema,
            non_keywords={},
            location=ParameterLocation.BODY,
            media_type="application/json",
            allow_extra_parameters=True,
        ),
        data.draw,
        schema,
    )
    assert result == MutationResult.SUCCESS
    assert metadata is not None
    # And the mutated schema is a valid JSON Schema
    validate_schema(schema)
    # And instances valid for this schema are not valid for the original one
    new_instance = data.draw(from_schema(schema))
    assert not validator.is_valid(new_instance)


@pytest.mark.parametrize(
    "schema",
    [
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
    ],
)
@given(data=st.data())
@settings(deadline=None, suppress_health_check=SUPPRESSED_HEALTH_CHECKS, max_examples=MAX_EXAMPLES)
def test_path_parameters_are_string(data, schema):
    validator = Draft4Validator(schema)
    new_schema = deepclone(schema)
    # When path parameters are mutated
    new_schema, _ = data.draw(
        mutated(
            keywords=new_schema,
            non_keywords={},
            location=ParameterLocation.PATH,
            media_type=None,
            allow_extra_parameters=True,
        )
    )
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


@pytest.mark.parametrize("key", ["components", "description"])
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
    new_schema, _ = data.draw(
        mutated(
            keywords=schema,
            non_keywords={key: {}},
            location=ParameterLocation.BODY,
            media_type="application/json",
            allow_extra_parameters=True,
        )
    )
    assert key in new_schema


@pytest.mark.parametrize(
    ("left", "right", "expected"),
    [
        (MutationResult.SUCCESS, MutationResult.SUCCESS, MutationResult.SUCCESS),
        (MutationResult.FAILURE, MutationResult.SUCCESS, MutationResult.SUCCESS),
        (MutationResult.SUCCESS, MutationResult.FAILURE, MutationResult.SUCCESS),
        (MutationResult.FAILURE, MutationResult.FAILURE, MutationResult.FAILURE),
    ],
)
def test_mutation_result_success(left, right, expected):
    assert left | right == expected
    left |= right
    assert left == expected


@pytest.mark.parametrize(
    "schema, validator_cls",
    [
        ({"minimum": 5, "exclusiveMinimum": True}, Draft4Validator),
        ({"maximum": 5, "exclusiveMaximum": True}, Draft4Validator),
        ({"maximum": 5, "exclusiveMaximum": True, "minimum": 1, "exclusiveMinimum": True}, Draft4Validator),
        ({"type": "integer", "maximum": 365.0, "exclusiveMinimum": 0.0, "title": "Nights"}, Draft202012Validator),
    ],
)
@given(data=st.data())
@settings(deadline=None, suppress_health_check=SUPPRESSED_HEALTH_CHECKS, max_examples=MAX_EXAMPLES)
def test_negate_constraints_keep_dependencies(data, schema, validator_cls):
    # When `negate_constraints` is used
    schema = deepclone(schema)
    negate_constraints(
        MutationContext(
            keywords=schema,
            non_keywords={},
            location=ParameterLocation.BODY,
            media_type="application/json",
            allow_extra_parameters=True,
        ),
        data.draw,
        schema,
    )
    # Then it should always produce valid schemas
    validator_cls.check_schema(schema)
    # E.g. `exclusiveMaximum` / `exclusiveMinimum` only work when `maximum` / `minimum` are present in the same schema


@given(data=st.data())
@settings(deadline=None, suppress_health_check=SUPPRESSED_HEALTH_CHECKS, max_examples=MAX_EXAMPLES)
def test_no_unsatisfiable_schemas(data):
    schema = {"type": "object", "required": ["foo"]}
    mutated_schema, _ = data.draw(
        mutated(
            keywords=schema,
            non_keywords={},
            location=ParameterLocation.BODY,
            media_type="application/json",
            allow_extra_parameters=True,
        )
    )
    assert canonicalish(mutated_schema) != FALSEY


@pytest.mark.hypothesis_nested
def test_optional_query_param_negation(ctx):
    # When all query parameters are optional
    schema = ctx.openapi.build_schema(
        {
            "/bug": {
                "get": {
                    "parameters": [
                        {"name": "key1", "in": "query", "required": False, "schema": {"type": "string"}},
                    ],
                    "responses": {"200": {"description": "OK"}},
                }
            }
        }
    )

    schema = schemathesis.openapi.from_dict(schema)

    @given(case=schema["/bug"]["get"].as_strategy(generation_mode=GenerationMode.NEGATIVE))
    @settings(deadline=None, max_examples=10, suppress_health_check=SUPPRESSED_HEALTH_CHECKS)
    def test(case):
        request = requests.PreparedRequest()
        request.prepare(**case.as_transport_kwargs(base_url="http://127.0.0.1"))
        # Then negative schema should not generate empty queries
        assert urlparse(request.url).query != ""

    test()


@pytest.mark.hypothesis_nested
def test_negating_multiple_query_params(ctx):
    # When all query parameters are optional
    schema = ctx.openapi.build_schema(
        {
            "/bug": {
                "get": {
                    "parameters": [
                        {"name": "key1", "in": "query", "required": False, "schema": {"type": "integer"}},
                        {"name": "key2", "in": "query", "required": False, "schema": {"type": "integer"}},
                    ],
                    "responses": {"200": {"description": "OK"}},
                }
            }
        }
    )

    schema = schemathesis.openapi.from_dict(schema)

    @given(case=schema["/bug"]["get"].as_strategy(generation_mode=GenerationMode.NEGATIVE))
    @settings(deadline=None, suppress_health_check=SUPPRESSED_HEALTH_CHECKS)
    def test(case):
        request = requests.PreparedRequest()
        request.prepare(**case.as_transport_kwargs(base_url="http://127.0.0.1"))
        # Then negated parameter should always be serialized
        query = urlparse(request.url).query
        if "key1" in case.query:
            assert "key1" in query, case.query
        if "key2" in case.query:
            assert "key2" in query, case.query

    test()


@given(data=st.data())
@settings(deadline=None, suppress_health_check=SUPPRESSED_HEALTH_CHECKS, max_examples=MAX_EXAMPLES)
def test_negative_query_respects_allow_extra_parameter_toggle(data):
    schema = {
        "type": "object",
        "properties": {"token": {"type": "string", "minLength": 5}},
        "required": ["token"],
        "additionalProperties": False,
    }
    result = data.draw(
        negative_schema(
            schema,
            operation_name="GET /token",
            location=ParameterLocation.QUERY,
            media_type=None,
            custom_formats=get_default_format_strategies(),
            validator_cls=Draft4Validator,
            generation_config=GenerationConfig(allow_extra_parameters=False),
        )
    )
    assert isinstance(result, GeneratedValue)
    value = result.value
    if isinstance(value, dict):
        assert "x-schemathesis-unknown-property" not in value


@pytest.mark.parametrize(
    ("schema", "new_type"),
    [
        ({"type": "object", "required": ["a"]}, "string"),
        ({"required": ["a"], "not": {"maxLength": 5}}, "string"),
    ],
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


@pytest.mark.parametrize("explode", [True, False])
@pytest.mark.parametrize(
    ("location", "schema", "style"),
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
def test_non_default_styles(ctx, location, schema, style, explode):
    # See GH-1208
    # When the schema contains a parameter with a not-default "style"
    schema = ctx.openapi.build_schema(
        {
            "/bug": {
                "get": {
                    "parameters": [
                        {
                            "name": "key",
                            "in": location,
                            "required": True,
                            "style": style,
                            "explode": explode,
                            "schema": schema,
                        },
                    ],
                    "responses": {"200": {"description": "OK"}},
                }
            }
        }
    )

    schema = schemathesis.openapi.from_dict(schema)

    @given(case=schema["/bug"]["get"].as_strategy(generation_mode=GenerationMode.NEGATIVE))
    @settings(deadline=None, max_examples=10, suppress_health_check=SUPPRESSED_HEALTH_CHECKS)
    def test(case):
        assert_requests_call(case)

    test()


@pytest.mark.snapshot()
def test_bundled_references(ctx, app_runner, cli, snapshot_cli):
    raw_schema = ctx.openapi.build_schema(
        {
            "/api/groups/migrations": {
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {"multipart/form-data": {"schema": {"$ref": "#/components/schemas/Object"}}},
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
        components={
            "schemas": {
                "Object": {
                    "properties": {
                        "migration_type": {"$ref": "#/components/schemas/SupportedMigrations"},
                        "archive": {},
                    },
                    "type": "object",
                    "required": ["migration_type", "archive"],
                },
                "SupportedMigrations": {},
            }
        },
    )

    app = Flask(__name__)

    @app.route("/openapi.json")
    def schema():
        return jsonify(raw_schema)

    @app.route("/api/groups/migrations", methods=["POST"])
    def create_migration():
        return jsonify({"result": "error"}), 400

    port = app_runner.run_flask_app(app)

    assert (
        cli.run(
            f"http://127.0.0.1:{port}/openapi.json",
            "--checks=not_a_server_error",
            "--phases=fuzzing",
            "--mode=negative",
            "--suppress-health-check=filter_too_much",
        )
        == snapshot_cli
    )


def is_valid_uuid(value: str) -> bool:
    try:
        uuid.UUID(value)
        return True
    except ValueError:
        return False


@pytest.mark.hypothesis_nested
def test_negative_format_generates_invalid_values(ctx):
    # When a path parameter has `format: uuid`
    schema = ctx.openapi.build_schema(
        {
            "/items/{id}": {
                "get": {
                    "parameters": [
                        {"name": "id", "in": "path", "required": True, "schema": {"type": "string", "format": "uuid"}}
                    ],
                    "responses": {"200": {"description": "OK"}},
                }
            }
        }
    )
    schema = schemathesis.openapi.from_dict(schema)
    invalid_uuid_found = False

    @given(case=schema["/items/{id}"]["GET"].as_strategy(generation_mode=GenerationMode.NEGATIVE))
    @settings(deadline=None, max_examples=10, suppress_health_check=SUPPRESSED_HEALTH_CHECKS)
    def test(case):
        nonlocal invalid_uuid_found
        id_value = case.path_parameters["id"]
        # Negative mode should generate values that DON'T match UUID format
        if not is_valid_uuid(id_value):
            invalid_uuid_found = True

    test()
    # Then at least some generated values should be invalid UUIDs
    assert invalid_uuid_found, "Negative mode should generate invalid UUID values"


def is_valid_uuid4(value: str) -> bool:
    try:
        parsed = uuid.UUID(value)
        return parsed.version == 4
    except ValueError:
        return False


@pytest.mark.hypothesis_nested
def test_negative_custom_format_generates_invalid_values(ctx):
    # When a user registers a custom format (uuid4)
    schemathesis.openapi.format("uuid4", st.uuids(version=4).map(str))
    # And a path parameter uses that custom format
    schema = ctx.openapi.build_schema(
        {
            "/items/{id}": {
                "get": {
                    "parameters": [
                        {"name": "id", "in": "path", "required": True, "schema": {"type": "string", "format": "uuid4"}}
                    ],
                    "responses": {"200": {"description": "OK"}},
                }
            }
        }
    )
    schema = schemathesis.openapi.from_dict(schema)
    invalid_uuid4_found = False

    @given(case=schema["/items/{id}"]["GET"].as_strategy(generation_mode=GenerationMode.NEGATIVE))
    @settings(deadline=None, max_examples=10, suppress_health_check=SUPPRESSED_HEALTH_CHECKS)
    def test(case):
        nonlocal invalid_uuid4_found
        id_value = case.path_parameters["id"]
        # Negative mode should generate values that DON'T match the custom uuid4 format
        if not is_valid_uuid4(id_value):
            invalid_uuid4_found = True

    test()
    # Then at least some generated values should be invalid UUID4s
    assert invalid_uuid4_found, "Negative mode should generate invalid UUID4 values for custom formats"


@pytest.mark.hypothesis_nested
def test_multiple_mutations_clear_description():
    # GH-3367: When multiple mutations are applied to a schema, they can conflict
    # (e.g., one mutation changes a property's type, another removes that property).
    # In such cases, keeping the first mutation's description is misleading.
    # This test verifies that when multiple mutations succeed, description is cleared.
    schema = {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "value": {"type": "integer"},
        },
        "required": ["name", "value"],
    }
    ctx = MutationContext(
        keywords=schema,
        non_keywords={},
        location=ParameterLocation.HEADER,
        media_type=None,
        allow_extra_parameters=False,
    )

    @given(data=st.data())
    @settings(deadline=None, max_examples=50, suppress_health_check=SUPPRESSED_HEALTH_CHECKS)
    def test(data):
        draw = data.draw
        _, metadata = ctx.mutate(draw)
        if metadata is not None:
            assert metadata.description != "Schema mutated"

    test()


@pytest.mark.hypothesis_nested
def test_path_parameters_never_contain_slash():
    # When fuzzing path parameters, mutated values should never contain `/`
    # because this would change the URL structure and potentially route to different endpoints.
    # For example, `/api/groups/{id}` with id="foo/bar" becomes `/api/groups/foo/bar`
    # which could match `/api/groups/{id}/{user_id}` instead.
    #
    # This can happen when the generated value is a dict/object that gets stringified with `/` in keys
    schema = schemathesis.openapi.from_dict(
        {
            "openapi": "3.0.0",
            "info": {"title": "Test", "version": "0.1.0"},
            "paths": {
                "/api/groups/{id}": {
                    "get": {
                        "parameters": [
                            {
                                "name": "id",
                                "in": "path",
                                "required": True,
                                "schema": {
                                    "anyOf": [
                                        {
                                            "type": "string",
                                            "examples": ["bqf7a2d9gbgud9a0jgfgt1ie"],
                                            "pattern": "^[a-zA-Z0-9\\-]+$",
                                        },
                                        {"enum": ["valid-id-1", "valid-id-2"]},
                                    ]
                                },
                            }
                        ],
                        "responses": {"200": {"description": "OK"}},
                    }
                },
            },
        }
    )
    operation = schema["/api/groups/{id}"]["GET"]

    @given(case=operation.as_strategy(generation_mode=GenerationMode.NEGATIVE))
    @settings(deadline=None, max_examples=250, suppress_health_check=SUPPRESSED_HEALTH_CHECKS)
    def test(case):
        for value in case.path_parameters.values():
            assert "/" not in str(value)

    test()
