"""Tests for behavior not specific to forms."""

import pytest

from schemathesis.core.errors import InvalidSchema
from schemathesis.generation.hypothesis.examples import generate_many
from schemathesis.generation.modes import GenerationMode
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
    values = [getattr(case, attribute) for case in generate_many(operation.as_strategy(), 10)]
    return values


QUERYSTRING = {"name": "raw", "in": "querystring", "content": {"text/plain": {"schema": {"type": "string"}}}}
QUERY = {"name": "a", "in": "query", "schema": {"type": "string"}}


@pytest.mark.parametrize(
    "parameters",
    [[QUERYSTRING, QUERY], [QUERY, QUERYSTRING], [QUERYSTRING, QUERYSTRING]],
    ids=["query-after-querystring", "querystring-after-query", "two-querystrings"],
)
def test_query_and_querystring_are_mutually_exclusive(ctx, parameters):
    schema = ctx.openapi.load_schema(
        {"/users": {"get": {"parameters": parameters, "responses": {"200": {"description": "OK"}}}}},
        version="3.2.0",
    )
    with pytest.raises(InvalidSchema):
        schema["/users"]["GET"]


def test_single_querystring_parameter_is_accepted(ctx):
    operation = ctx.openapi.load_operation(
        "GET /users", {"parameters": [QUERYSTRING], "responses": {"200": {"description": "OK"}}}, version="3.2.0"
    )
    assert [parameter.name for parameter in operation.query] == ["raw"]


@pytest.mark.parametrize(
    ("version", "schema"),
    [("2.0", {"type": "string"}), ("3.0.2", {"schema": {"type": "string"}})],
    ids=["swagger", "openapi"],
)
def test_parameter_with_non_string_location_is_skipped(ctx, version, schema):
    operation = ctx.openapi.load_operation(
        "GET /users",
        {
            "parameters": [{"name": "q", "in": 5, **schema}, {"name": "id", "in": "query", **schema}],
            "responses": {"200": {"description": "OK"}},
        },
        version=version,
    )

    assert [parameter.name for parameter in operation.query] == ["id"]


BODY_PARAMETER = {"name": "payload", "in": "body", "schema": {"type": "object"}}
OK = {"200": {"description": "OK"}}


@pytest.mark.parametrize(
    ("operation_definition", "global_consumes", "expected"),
    [
        (
            {"consumes": ["*/*", "application/xml"], "parameters": [BODY_PARAMETER], "responses": OK},
            {},
            ["application/xml"],
        ),
        ({"consumes": ["*/*"], "parameters": [BODY_PARAMETER], "responses": OK}, {}, ["application/json"]),
        ({"parameters": [BODY_PARAMETER], "responses": OK}, {"consumes": ["application/xml"]}, ["application/xml"]),
    ],
    ids=["wildcard-dropped-beside-concrete", "only-wildcard-falls-back-to-json", "global-consumes"],
)
def test_swagger_body_media_types(ctx, operation_definition, global_consumes, expected):
    operation = ctx.openapi.load_operation("POST /users", operation_definition, version="2.0", **global_consumes)
    assert [body.media_type for body in operation.body] == expected


REFERENCED_BODY = {"name": "payload", "in": "body", "schema": {"$ref": "#/definitions/User"}}
QUERY_PARAMETER = {"name": "q", "in": "query", "type": "string"}


@pytest.mark.parametrize(
    "path_item",
    [
        {"parameters": [QUERY_PARAMETER], "post": {"parameters": [REFERENCED_BODY], "responses": OK}},
        {"parameters": [REFERENCED_BODY], "post": {"parameters": [QUERY_PARAMETER], "responses": OK}},
    ],
    ids=["operation", "path-item"],
)
def test_swagger_body_resource_name_comes_from_its_reference(ctx, path_item):
    schema = ctx.openapi.load_schema({"/users": path_item}, version="2.0", definitions={"User": {"type": "object"}})
    assert [body.resource_name for body in schema["/users"]["POST"].body] == ["User"]


@pytest.mark.parametrize(
    ("schema", "expected"),
    [
        ({"$ref": "#/components/schemas/User"}, "User"),
        ({"type": "array", "items": {"$ref": "#/components/schemas/User"}}, "User"),
        ({"type": "object"}, None),
    ],
    ids=["reference", "array-of-references", "inline"],
)
def test_request_body_resource_name_comes_from_its_reference(ctx, schema, expected):
    operation = ctx.openapi.load_operation(
        "POST /users",
        {
            "requestBody": {"content": {"application/json": {"schema": schema}}},
            "responses": {"200": {"description": "OK"}},
        },
        components={"schemas": {"User": {"type": "object"}}},
    )
    assert [body.resource_name for body in operation.body] == [expected]


@pytest.mark.parametrize(
    ("enum", "items", "expected"),
    [
        (["a", "b"], {"type": "string", "enum": ["a", ["x"], {"k": "v"}]}, {"a"}),
        (["a", "b"], {"type": "string"}, {"a", "b"}),
        ([{"k": [1]}, {"k": [2]}], {"type": "object", "enum": [{"k": [1]}, {"k": [3]}]}, {'{"k": [1]}'}),
    ],
    ids=["intersects-item-enum", "moves-onto-items", "compares-objects-by-value"],
)
def test_swagger_array_enum_constrains_items(ctx, enum, items, expected):
    operation = ctx.openapi.load_operation(
        "GET /users",
        {
            "parameters": [
                {
                    "name": "tags",
                    "in": "query",
                    "required": True,
                    "type": "array",
                    "maxItems": 1,
                    "enum": enum,
                    "items": items,
                }
            ],
            "responses": {"200": {"description": "OK"}},
        },
        version="2.0",
    )

    assert {str(query["tags"]) for query in generated(operation, "query")} == expected


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
    operation = ctx.openapi.load_operation(
        "GET /users",
        {
            "parameters": [
                {"name": "page", "in": "query", "schema": {"type": "integer"}},
                {"name": "q", "in": "query", "required": True, "schema": True, "example": "picked"},
            ],
            "responses": {"200": {"description": "OK"}},
        },
        version="3.1.0",
    )
    values = [case.query["q"] for case in generate_many(operation.as_strategy(), 50)]

    assert "picked" in values


@pytest.mark.parametrize(
    ("version", "parameter"),
    [
        ("2.0", {"type": "string", "x-example": "a", "x-examples": {"other": {"value": "b"}}}),
        ("3.0.2", {"schema": {"type": "string"}, "example": "a", "examples": {"other": {"value": "b"}}}),
    ],
    ids=["swagger-2.0", "openapi-3.0"],
)
def test_parameter_lists_each_declared_example_once(ctx, version, parameter):
    operation = ctx.openapi.load_operation(
        "GET /users",
        {
            "parameters": [{"name": "q", "in": "query", "required": True, **parameter}],
            "responses": {"200": {"description": "OK"}},
        },
        version=version,
    )

    assert operation.query.get("q").examples == ["b", "a"]


# An empty array leaves a required query parameter off the wire, so such an example is never used.
def test_fuzzing_skips_examples_that_drop_a_required_parameter_from_the_wire(ctx):
    operation = ctx.openapi.load_operation(
        "GET /users",
        {
            "parameters": [
                {
                    "name": "tags",
                    "in": "query",
                    "required": True,
                    "schema": {"type": "array", "items": {"type": "string", "enum": ["a"]}},
                    "examples": {"empty": {"value": []}},
                }
            ],
            "responses": {"200": {"description": "OK"}},
        },
    )
    assert [case.query for case in generate_many(operation.as_strategy(), 50) if not case.query.get("tags")] == []


@pytest.mark.parametrize(
    ("parameter", "expected"),
    [
        ({"schema": {"type": "string"}}, []),
        (
            {"example": "top", "schema": {"type": "string", "example": "nested", "examples": ["listed"]}},
            ["top", "nested", "listed"],
        ),
    ],
    ids=["none", "definition-and-schema"],
)
def test_parameter_examples_come_from_definition_and_schema(ctx, parameter, expected):
    operation = ctx.openapi.load_operation(
        "GET /users",
        {"parameters": [{"name": "q", "in": "query", **parameter}], "responses": {"200": {"description": "OK"}}},
    )
    assert [parameter.examples for parameter in operation.query] == [expected]


def test_request_body_without_schema_has_an_empty_schema(ctx):
    operation = ctx.openapi.load_operation(
        "POST /users",
        {
            "requestBody": {"required": True, "content": {"application/json": {}}},
            "responses": {"200": {"description": "OK"}},
        },
    )
    assert [body.raw_schema for body in operation.body] == [{}]


def test_swagger_examples_list_holds_example_objects_and_raw_values(ctx):
    operation = ctx.openapi.load_operation(
        "GET /users",
        {
            "parameters": [
                {"name": "q", "in": "query", "type": "string", "x-examples": [{"value": "wrapped"}, "raw value"]}
            ],
            "responses": {"200": {"description": "OK"}},
        },
        version="2.0",
    )
    assert [parameter.examples for parameter in operation.query] == [["wrapped", "raw value"]]


# Examples replace a generated body at random one draw in five; 50 draws make missing it negligible.
@pytest.mark.parametrize(
    "example", [{"value": {"name": "picked"}}, {"$ref": "#/components/examples/Picked"}], ids=["inline", "reference"]
)
def test_fuzzing_mixes_in_named_body_examples(ctx, example):
    operation = ctx.openapi.load_operation(
        "POST /users",
        {
            "requestBody": {
                "required": True,
                "content": {
                    "application/json": {
                        "schema": {"type": "object", "properties": {"name": {"type": "string"}}},
                        "examples": {"picked": example},
                    }
                },
            },
            "responses": {"200": {"description": "OK"}},
        },
        components={"examples": {"Picked": {"value": {"name": "picked"}}}},
    )
    assert {"name": "picked"} in [case.body for case in generate_many(operation.as_strategy(), 50)]


# A schema example is valid by definition, so mixing it into negative bodies would mislabel it.
def test_negative_bodies_never_reuse_schema_examples(ctx):
    operation = ctx.openapi.load_operation(
        "POST /users",
        {
            "requestBody": {
                "required": True,
                "content": {
                    "application/json": {
                        "schema": {"type": "object", "properties": {"name": {"type": "string"}}},
                        "example": {"name": "picked"},
                    }
                },
            },
            "responses": {"200": {"description": "OK"}},
        },
    )
    bodies = [case.body for case in generate_many(operation.as_strategy(generation_mode=GenerationMode.NEGATIVE), 50)]
    assert {"name": "picked"} not in bodies
