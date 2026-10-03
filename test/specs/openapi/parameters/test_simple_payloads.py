"""Tests for behavior not specific to forms."""

import pytest
from hypothesis import HealthCheck, given, settings

from schemathesis.core.errors import InvalidSchema
from schemathesis.schemas import PayloadAlternatives
from schemathesis.specs.openapi.adapter import v2, v3_0
from schemathesis.specs.openapi.adapter.parameters import OpenApiBody


@pytest.mark.parametrize(
    "consumes",
    [
        ["application/json"],
        # Multiple values in "consumes" implies multiple payload variants
        ["application/json", "application/xml"],
    ],
)
def test_payload_open_api_2(
    consumes,
    assert_parameters,
    make_openapi_2_schema,
    open_api_2_user_in_body,
    user_jsonschema,
):
    # A single "body" parameter is used for all payload variants
    schema = make_openapi_2_schema(consumes, [open_api_2_user_in_body])
    assert_parameters(
        schema,
        PayloadAlternatives(
            [
                OpenApiBody.from_definition(
                    definition=open_api_2_user_in_body,
                    is_required=False,
                    media_type=value,
                    resource_name=None,
                    name_to_uri={},
                    adapter=v2,
                )
                for value in consumes
            ]
        ),
        # For each one the schema is extracted from the parameter definition and transformed to the proper JSON Schema
        [user_jsonschema] * len(consumes),
    )


@pytest.mark.parametrize(
    "media_types",
    [
        ["application/json"],
        # Each media type corresponds to a payload variant
        ["application/json", "application/xml"],
        # Forms can be also combined
        ["application/x-www-form-urlencoded", "multipart/form-data"],
    ],
)
def test_payload_open_api_3(media_types, assert_parameters, make_openapi_3_schema, open_api_3_user, user_jsonschema):
    schema = make_openapi_3_schema(
        {
            "required": True,
            "content": {media_type: {"schema": open_api_3_user} for media_type in media_types},
        }
    )
    assert_parameters(
        schema,
        PayloadAlternatives(
            [
                OpenApiBody.from_definition(
                    definition={"schema": open_api_3_user},
                    media_type=media_type,
                    is_required=True,
                    resource_name=None,
                    name_to_uri={},
                    adapter=v3_0,
                )
                for media_type in media_types
            ]
        ),
        # The converted schema should correspond the schema in the relevant "requestBody" part
        # In this case they are the same
        [user_jsonschema] * len(media_types),
    )


def test_parameter_set_get(ctx, make_openapi_3_schema):
    header = {"in": "header", "name": "id", "required": True, "schema": {}}
    raw_schema = make_openapi_3_schema(parameters=[header])
    schema = ctx.openapi.load_schema(raw_schema["paths"])
    headers = schema["/users"]["POST"].headers
    assert "id" in headers
    assert "foo" not in headers


def generated(operation, attribute):
    values = []

    @given(case=operation.as_strategy())
    @settings(max_examples=10, suppress_health_check=list(HealthCheck), deadline=None, database=None)
    def inner(case):
        values.append(getattr(case, attribute))

    inner()
    return values


def test_query_after_querystring_is_rejected(ctx):
    schema = ctx.openapi.load_schema(
        {
            "/users": {
                "get": {
                    "parameters": [
                        {"name": "raw", "in": "querystring", "content": {"text/plain": {"schema": {"type": "string"}}}},
                        {"name": "a", "in": "query", "schema": {"type": "string"}},
                    ],
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
        version="3.2.0",
    )
    with pytest.raises(InvalidSchema, match="Invalid `parameters` definition"):
        schema["/users"]["GET"]


def test_swagger_parameter_with_non_string_location_is_skipped(ctx):
    operation = ctx.openapi.load_schema(
        {
            "/users": {
                "get": {
                    "parameters": [{"name": "q", "in": 5, "type": "string"}],
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
        version="2.0",
    )["/users"]["GET"]

    assert set(map(str, generated(operation, "query"))) == {"{}"}


def test_swagger_array_enum_intersects_item_enum(ctx):
    operation = ctx.openapi.load_schema(
        {
            "/users": {
                "get": {
                    "parameters": [
                        {
                            "name": "tags",
                            "in": "query",
                            "required": True,
                            "type": "array",
                            "maxItems": 1,
                            "enum": ["a", "b"],
                            "items": {"type": "string", "enum": ["a", ["x"], {"k": "v"}]},
                        }
                    ],
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
        version="2.0",
    )["/users"]["GET"]

    assert {query["tags"] for query in generated(operation, "query")} == {"a"}


@pytest.mark.parametrize(
    ("ref", "match"),
    [
        ("#/components/schemas/Null", "Invalid Schema Object definition for `Null`"),
        ("#/components/schemas/Missing", "Unresolvable reference in the schema"),
    ],
    ids=["ref-to-null", "missing-ref"],
)
def test_required_content_parameter_with_broken_schema_reference(ctx, ref, match):
    schema = ctx.openapi.load_schema(
        {
            "/users": {
                "get": {
                    "parameters": [
                        {
                            "name": "q",
                            "in": "query",
                            "required": True,
                            "content": {"application/json": {"schema": {"$ref": ref}}},
                        }
                    ],
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
        components={"schemas": {"Null": None}},
    )
    with pytest.raises(InvalidSchema, match=match):
        schema["/users"]["GET"]


# Examples replace a generated value at random one draw in five; 50 draws make missing it negligible.
def test_fuzzing_mixes_in_examples_of_boolean_schema_query_parameter(ctx):
    operation = ctx.openapi.load_schema(
        {
            "/users": {
                "get": {
                    "parameters": [{"name": "q", "in": "query", "required": True, "schema": True, "example": "picked"}],
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
        version="3.1.0",
    )["/users"]["GET"]
    values = []

    @given(case=operation.as_strategy())
    @settings(max_examples=50, suppress_health_check=list(HealthCheck), deadline=None, database=None)
    def inner(case):
        values.append(case.query["q"])

    inner()

    assert "picked" in values
