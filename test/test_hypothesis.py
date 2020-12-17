from base64 import b64decode

import pytest
from hypothesis import HealthCheck, given, settings, strategies

import schemathesis
from schemathesis import Case, register_string_format
from schemathesis.exceptions import InvalidSchema
from schemathesis.models import Endpoint, EndpointDefinition
from schemathesis.specs.openapi._hypothesis import (
    PARAMETERS,
    STRING_FORMATS,
    filter_path_parameters,
    get_case_strategy,
    is_valid_query,
)
from schemathesis.specs.openapi.parameters import OpenAPI20Body, OpenAPI20CompositeBody, OpenAPI20Parameter


def make_endpoint(schema, **kwargs) -> Endpoint:
    return Endpoint("/users", "POST", definition=EndpointDefinition({}, {}, "foo", []), schema=schema, **kwargs)


@pytest.mark.parametrize("name", sorted(PARAMETERS))
@pytest.mark.filterwarnings("ignore:.*method is good for exploring strategies.*")
def test_get_examples(name, swagger_20):
    if name == "body":
        # TODO. explain why
        example = expected = {"name": "John"}
        media_type = "application/json"
    else:
        example = "John"
        expected = {"name": "John"}
        media_type = None
    endpoint = make_endpoint(
        swagger_20,
        **{
            name: [
                OpenAPI20Parameter(
                    {
                        "in": name,
                        "name": "name",
                        "required": True,
                        "type": "string",
                        "x-example": example,
                    }
                )
            ]
        },
    )
    strategies = endpoint.get_strategies_from_examples()
    assert len(strategies) == 1
    assert strategies[0].example() == Case(endpoint, media_type=media_type, **{name: expected})


@pytest.mark.filterwarnings("ignore:.*method is good for exploring strategies.*")
def test_no_body_in_get(swagger_20):
    endpoint = Endpoint(
        path="/api/success",
        method="GET",
        definition=EndpointDefinition({}, {}, "foo", []),
        schema=swagger_20,
        query=[
            OpenAPI20Parameter(
                {
                    "required": True,
                    "in": "query",
                    "type": "string",
                    "name": "key",
                    "x-example": "John",
                }
            )
        ],
    )
    strategies = endpoint.get_strategies_from_examples()
    assert len(strategies) == 1
    assert strategies[0].example().body is None


@pytest.mark.filterwarnings("ignore:.*method is good for exploring strategies.*")
def test_invalid_body_in_get(swagger_20):
    endpoint = Endpoint(
        path="/foo",
        method="GET",
        definition=EndpointDefinition({}, {}, "foo", []),
        schema=swagger_20,
        body=[
            OpenAPI20Body(
                {
                    "name": "attributes",
                    "in": "body",
                    "required": True,
                    "schema": {"required": ["foo"], "type": "object", "properties": {"foo": {"type": "string"}}},
                },
                media_type="application/json",
            )
        ],
    )
    with pytest.raises(InvalidSchema, match=r"^Body parameters are defined for GET request.$"):
        get_case_strategy(endpoint).example()


@pytest.mark.hypothesis_nested
def test_invalid_body_in_get_disable_validation(simple_schema):
    schema = schemathesis.from_dict(simple_schema, validate_schema=False)
    endpoint = Endpoint(
        path="/foo",
        method="GET",
        definition=EndpointDefinition({}, {}, "foo", []),
        schema=schema,
        body=[
            OpenAPI20Body(
                {
                    "name": "attributes",
                    "in": "body",
                    "required": True,
                    "schema": {"required": ["foo"], "type": "object", "properties": {"foo": {"type": "string"}}},
                },
                media_type="application/json",
            )
        ],
    )
    strategy = get_case_strategy(endpoint)

    @given(strategy)
    @settings(max_examples=1)
    def test(case):
        assert case.body is not None

    test()


@pytest.mark.filterwarnings("ignore:.*method is good for exploring strategies.*")
def test_custom_strategies(swagger_20):
    register_string_format("even_4_digits", strategies.from_regex(r"\A[0-9]{4}\Z").filter(lambda x: int(x) % 2 == 0))
    endpoint = make_endpoint(
        swagger_20,
        query=[
            OpenAPI20Parameter(
                {"name": "id", "in": "query", "required": True, "type": "string", "format": "even_4_digits"}
            )
        ],
    )
    result = get_case_strategy(endpoint).example()
    assert len(result.query["id"]) == 4
    assert int(result.query["id"]) % 2 == 0


def test_register_default_strategies():
    assert "binary" in STRING_FORMATS
    assert "byte" in STRING_FORMATS


@pytest.mark.filterwarnings("ignore:.*method is good for exploring strategies.*")
def test_default_strategies_binary(swagger_20):
    endpoint = make_endpoint(
        swagger_20,
        body=[
            OpenAPI20CompositeBody.from_parameters(
                {
                    "name": "upfile",
                    "in": "formData",
                    "type": "file",
                    "required": True,
                },
                media_type="multipart/form-data",
            )
        ],
    )
    result = get_case_strategy(endpoint).example()
    assert isinstance(result.body["upfile"], bytes)


@pytest.mark.filterwarnings("ignore:.*method is good for exploring strategies.*")
def test_default_strategies_bytes(swagger_20):
    endpoint = make_endpoint(
        swagger_20,
        body=[
            OpenAPI20Body(
                {"in": "body", "name": "byte", "required": True, "schema": {"type": "string", "format": "byte"}},
                media_type="text/plain",
            )
        ],
    )
    result = get_case_strategy(endpoint).example()
    assert isinstance(result.body, str)
    b64decode(result.body)


@pytest.mark.parametrize(
    "values, error",
    (
        (("valid", "invalid"), f"strategy must be of type {strategies.SearchStrategy}, not {str}"),
        ((123, strategies.from_regex(r"\d")), f"name must be of type {str}, not {int}"),
    ),
)
def test_invalid_custom_strategy(values, error):
    with pytest.raises(TypeError) as exc:
        register_string_format(*values)
    assert error in str(exc.value)


@pytest.mark.hypothesis_nested
@pytest.mark.parametrize(
    "definition", ({"name": "api_key", "in": "header", "type": "string"}, {"name": "api_key", "in": "header"})
)
def test_valid_headers(openapi2_base_url, swagger_20, definition):
    endpoint = Endpoint(
        "/api/success",
        "GET",
        definition=EndpointDefinition({}, {}, "foo", []),
        schema=swagger_20,
        base_url=openapi2_base_url,
        headers=[OpenAPI20Parameter(definition)],
    )

    @given(case=get_case_strategy(endpoint))
    @settings(suppress_health_check=[HealthCheck.filter_too_much, HealthCheck.too_slow], deadline=None, max_examples=10)
    def inner(case):
        case.call()

    inner()


def make_swagger(*parameters):
    return {
        "swagger": "2.0",
        "info": {"title": "Sample API", "description": "API description in Markdown.", "version": "1.0.0"},
        "host": "api.example.com",
        "basePath": "/v1",
        "schemes": ["https"],
        "paths": {
            "/form": {
                "post": {
                    "parameters": list(parameters),
                    "summary": "Returns a list of users.",
                    "description": "Optional extended description in Markdown.",
                    "consumes": ["multipart/form-data"],
                    "produces": ["application/json"],
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
    }


@pytest.mark.parametrize(
    "raw_schema",
    (
        make_swagger(
            {"name": "a", "in": "formData", "required": True, "type": "number"},
            {"name": "b", "in": "formData", "required": True, "type": "boolean"},
            {"name": "c", "in": "formData", "required": True, "type": "array"},
        ),
        make_swagger({"name": "c", "in": "formData", "required": True, "type": "array"}),
        {
            "openapi": "3.0.2",
            "info": {"title": "Test", "description": "Test", "version": "0.1.0"},
            "servers": [{"url": "http://127.0.0.1:8081/{basePath}", "variables": {"basePath": {"default": "api"}}}],
            "paths": {
                "/form": {
                    "post": {
                        "requestBody": {
                            "content": {
                                "multipart/form-data": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {
                                            "a": {"type": "number"},
                                            "b": {"type": "boolean"},
                                            "c": {"type": "array"},
                                        },
                                        "required": ["a", "b", "c"],
                                    },
                                }
                            }
                        },
                        "responses": {"200": {"description": "OK"}},
                    }
                }
            },
        },
    ),
)
def test_valid_form_data(request, raw_schema):
    if "swagger" in raw_schema:
        base_url = request.getfixturevalue("openapi2_base_url")
    else:
        base_url = request.getfixturevalue("openapi3_base_url")
    # When the request definition contains a schema, matching values of which cannot be encoded to multipart
    # straightforwardly
    schema = schemathesis.from_dict(raw_schema, base_url=base_url)

    @given(case=schema["/form"]["POST"].as_strategy())
    @settings(deadline=None, suppress_health_check=[HealthCheck.too_slow], max_examples=100)
    def inner(case):
        case.call()

    # Then these values should be casted to bytes and handled successfully
    inner()


@pytest.mark.parametrize("value, expected", (({"key": "1"}, True), ({"key": 1}, True), ({"key": "\udcff"}, False)))
def test_is_valid_query(value, expected):
    assert is_valid_query(value) == expected


@pytest.mark.parametrize("value", ("/", "\udc9b"))
def test_filter_path_parameters(value):
    assert not filter_path_parameters({"foo": value})


@pytest.mark.hypothesis_nested
def test_is_valid_query_strategy():
    strategy = strategies.sampled_from([{"key": "1"}, {"key": "\udcff"}]).filter(is_valid_query)

    @given(strategy)
    @settings(max_examples=10)
    def test(value):
        assert value == {"key": "1"}

    test()
