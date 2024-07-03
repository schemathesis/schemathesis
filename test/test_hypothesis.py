import datetime
from base64 import b64decode
from unittest.mock import ANY

import pytest
from hypothesis import HealthCheck, assume, find, given, settings
from hypothesis import strategies as st

import schemathesis
from schemathesis.constants import NOT_SET
from schemathesis.exceptions import OperationSchemaError
from schemathesis.generation import DataGenerationMethod, GenerationConfig
from schemathesis.models import APIOperation, Case, OperationDefinition
from schemathesis.parameters import ParameterSet, PayloadAlternatives
from schemathesis.serializers import Binary
from schemathesis.specs.openapi._hypothesis import (
    _get_body_strategy,
    get_case_strategy,
    is_valid_path,
    is_valid_query,
    jsonify_python_specific_types,
    make_positive_strategy,
    quote_all,
)
from schemathesis.specs.openapi.constants import LOCATION_TO_CONTAINER
from schemathesis.specs.openapi.parameters import OpenAPI20Body, OpenAPI20CompositeBody, OpenAPI20Parameter
from test.utils import assert_requests_call


def make_operation(schema, **kwargs) -> APIOperation:
    return APIOperation("/users", "POST", definition=OperationDefinition({}, {}, "foo"), schema=schema, **kwargs)


@pytest.mark.parametrize("location", sorted(LOCATION_TO_CONTAINER))
@pytest.mark.filterwarnings("ignore:.*method is good for exploring strategies.*")
def test_get_examples(location, swagger_20):
    if location == "body":
        # In Open API 2.0, the `body` parameter has a name, which is ignored
        # But we'd like to use this object as a payload; therefore, we put one extra level of nesting
        example = expected = {"name": "John"}
        media_type = "application/json"
        cls = PayloadAlternatives
        parameter_cls = OpenAPI20Body
        kwargs = {"media_type": media_type}
        definition = {
            "in": location,
            "name": "name",
            "required": True,
            "schema": {"type": "string"},
            "x-example": example,
        }
    else:
        example = "John"
        expected = {"name": example}
        media_type = None  # there is no payload
        cls = ParameterSet
        parameter_cls = OpenAPI20Parameter
        kwargs = {}
        definition = {
            "in": location,
            "name": "name",
            "required": True,
            "type": "string",
            "x-example": example,
        }
    container = LOCATION_TO_CONTAINER[location]
    operation = make_operation(
        swagger_20,
        **{container: cls([parameter_cls(definition, **kwargs)])},
    )
    strategies = operation.get_strategies_from_examples()
    assert len(strategies) == 1
    assert strategies[0].example() == Case(
        operation,
        generation_time=ANY,
        data_generation_method=DataGenerationMethod.positive,
        media_type=media_type,
        meta=ANY,
        **{container: expected},
    )


@pytest.mark.filterwarnings("ignore:.*method is good for exploring strategies.*")
def test_no_body_in_get(swagger_20):
    operation = APIOperation(
        path="/api/success",
        method="GET",
        definition=OperationDefinition({}, {}, "foo"),
        schema=swagger_20,
        query=ParameterSet(
            [
                OpenAPI20Parameter(
                    {
                        "required": True,
                        "in": "query",
                        "type": "string",
                        "name": "key",
                        "x-example": "John",
                    }
                )
            ]
        ),
    )
    strategies = operation.get_strategies_from_examples()
    assert len(strategies) == 1
    assert strategies[0].example().body is NOT_SET


@pytest.mark.filterwarnings("ignore:.*method is good for exploring strategies.*")
def test_invalid_body_in_get(swagger_20):
    swagger_20.validate_schema = True
    operation = APIOperation(
        path="/foo",
        method="GET",
        definition=OperationDefinition({}, {}, "foo"),
        schema=swagger_20,
        body=PayloadAlternatives(
            [
                OpenAPI20Body(
                    {
                        "name": "attributes",
                        "in": "body",
                        "required": True,
                        "schema": {"required": ["foo"], "type": "object", "properties": {"foo": {"type": "string"}}},
                    },
                    media_type="application/json",
                )
            ]
        ),
    )
    with pytest.raises(OperationSchemaError, match=r"^GET requests should not contain body parameters."):
        get_case_strategy(operation).example()


@pytest.mark.hypothesis_nested
def test_invalid_body_in_get_disable_validation(simple_schema):
    schema = schemathesis.from_dict(simple_schema, validate_schema=False)
    operation = APIOperation(
        path="/foo",
        method="GET",
        definition=OperationDefinition({}, {}, "foo"),
        schema=schema,
        body=PayloadAlternatives(
            [
                OpenAPI20Body(
                    {
                        "name": "attributes",
                        "in": "body",
                        "required": True,
                        "schema": {"required": ["foo"], "type": "object", "properties": {"foo": {"type": "string"}}},
                    },
                    media_type="application/json",
                )
            ]
        ),
    )
    strategy = get_case_strategy(operation)

    @given(strategy)
    @settings(max_examples=1)
    def test(case):
        assert case.body is not None

    test()


@pytest.mark.filterwarnings("ignore:.*method is good for exploring strategies.*")
def test_custom_strategies(swagger_20):
    schemathesis.openapi.format("even_4_digits", st.from_regex(r"\A[0-9]{4}\Z").filter(lambda x: int(x) % 2 == 0))
    operation = make_operation(
        swagger_20,
        query=ParameterSet(
            [
                OpenAPI20Parameter(
                    {"name": "id", "in": "query", "required": True, "type": "string", "format": "even_4_digits"}
                )
            ]
        ),
    )
    result = get_case_strategy(operation).example()
    assert len(result.query["id"]) == 4
    assert int(result.query["id"]) % 2 == 0


@pytest.mark.filterwarnings("ignore:.*method is good for exploring strategies.*")
def test_default_strategies_binary(swagger_20):
    body = OpenAPI20CompositeBody.from_parameters(
        {
            "name": "upfile",
            "in": "formData",
            "type": "file",
            "required": True,
        },
        media_type="multipart/form-data",
    )
    operation = make_operation(swagger_20, body=PayloadAlternatives([body]))
    swagger_20.raw_schema["consumes"] = ["multipart/form-data"]
    case = get_case_strategy(operation).example()
    assert isinstance(case.body["upfile"], Binary)
    kwargs = case.as_transport_kwargs(base_url="http://127.0.0.1")
    assert kwargs["files"] == [("upfile", case.body["upfile"].data)]


@pytest.mark.parametrize("media_type", ("application/json", "text/yaml"))
def test_binary_is_serializable(empty_open_api_3_schema, media_type):
    empty_open_api_3_schema["paths"] = {
        "/data": {
            "post": {
                "requestBody": {
                    "required": True,
                    "content": {media_type: {"schema": {"type": "string", "format": "binary"}}},
                },
                "responses": {"200": {"description": "OK"}},
            },
        },
    }

    schema = schemathesis.from_dict(empty_open_api_3_schema)
    operation = schema["/data"]["POST"]

    @given(operation.as_strategy())
    @settings(max_examples=1)
    def test(case):
        assert_requests_call(case)
        assert case.as_transport_kwargs()["data"] == case.body.data

    test()


@pytest.mark.filterwarnings("ignore:.*method is good for exploring strategies.*")
def test_default_strategies_bytes(swagger_20):
    operation = make_operation(
        swagger_20,
        body=PayloadAlternatives(
            [
                OpenAPI20Body(
                    {"in": "body", "name": "byte", "required": True, "schema": {"type": "string", "format": "byte"}},
                    media_type="text/plain",
                )
            ]
        ),
    )
    result = get_case_strategy(operation).example()
    assert isinstance(result.body, str)
    b64decode(result.body)


@pytest.mark.parametrize(
    "values, error",
    (
        (("valid", "invalid"), f"strategy must be of type {st.SearchStrategy}, not {str}"),
        ((123, st.from_regex(r"\d")), f"name must be of type {str}, not {int}"),
    ),
)
def test_invalid_custom_strategy(values, error):
    with pytest.raises(TypeError) as exc:
        schemathesis.openapi.format(*values)
    assert error in str(exc.value)


@pytest.mark.hypothesis_nested
@pytest.mark.parametrize(
    "definition", ({"name": "api_key", "in": "header", "type": "string"}, {"name": "api_key", "in": "header"})
)
def test_valid_headers(openapi2_base_url, swagger_20, definition):
    operation = APIOperation(
        "/api/success",
        "GET",
        definition=OperationDefinition({}, {}, "foo"),
        schema=swagger_20,
        base_url=openapi2_base_url,
        headers=ParameterSet([OpenAPI20Parameter(definition)]),
    )

    @given(case=get_case_strategy(operation))
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
@pytest.mark.hypothesis_nested
def test_valid_form_data(request, raw_schema):
    if "swagger" in raw_schema:
        base_url = request.getfixturevalue("openapi2_base_url")
    else:
        base_url = request.getfixturevalue("openapi3_base_url")
    # When the request definition contains a schema, matching values of which cannot be encoded to multipart
    # straightforwardly
    schema = schemathesis.from_dict(raw_schema, base_url=base_url)

    @given(case=schema["/form"]["POST"].as_strategy())
    @settings(deadline=None, suppress_health_check=[HealthCheck.too_slow], max_examples=10)
    def inner(case):
        case.call()

    # Then these values should be cast to bytes and handled successfully
    inner()


@pytest.mark.hypothesis_nested
def test_optional_form_data(openapi3_base_url, empty_open_api_3_schema):
    empty_open_api_3_schema["paths"]["/form"] = {
        "post": {
            "requestBody": {
                "content": {
                    "multipart/form-data": {
                        "schema": {
                            "type": "string",
                        },
                    }
                }
            },
            "responses": {"200": {"description": "OK"}},
        }
    }
    # When the multipart form is optional
    # Note, this test is similar to the one above, but has a simplified schema & conditions
    # It is done mostly due to performance reasons
    schema = schemathesis.from_dict(empty_open_api_3_schema, base_url=openapi3_base_url)

    @given(case=schema["/form"]["POST"].as_strategy())
    @settings(deadline=None, suppress_health_check=[HealthCheck.too_slow, HealthCheck.filter_too_much], max_examples=1)
    def inner(case):
        assume(case.body is NOT_SET)
        case.call()

    # Then payload can be absent
    inner()


@pytest.mark.parametrize(
    "value, expected",
    (({"key": "1"}, True), ({"key": 1}, True), ({"key": "\udcff"}, False), ({"key": ["1", "abc", "\udcff"]}, False)),
)
def test_is_valid_query(value, expected):
    assert is_valid_query(value) == expected


@pytest.mark.parametrize("value", ("/", "\udc9b"))
def test_filter_path_parameters(value):
    assert not is_valid_path({"foo": value})


@pytest.mark.parametrize("value, expected", ((".", "%2E"), ("..", "%2E%2E"), (".foo", ".foo")))
def test_path_parameters_quotation(value, expected):
    # See GH-1036
    assert quote_all({"foo": value})["foo"] == expected


@pytest.mark.parametrize("expected", ("null", "true", "false"))
def test_parameters_jsonified(empty_open_api_3_schema, expected):
    # See GH-1166
    # When `None` or `True` / `False` are generated in path or query
    empty_open_api_3_schema["paths"] = {
        "/foo/{param_path}": {
            "get": {
                "parameters": [
                    {
                        "name": f"param_{location}",
                        "in": location,
                        "required": True,
                        "schema": {"type": "boolean", "nullable": True},
                    }
                    for location in ("path", "query")
                ],
                "responses": {"200": {"description": "OK"}},
            }
        }
    }

    schema = schemathesis.from_dict(empty_open_api_3_schema)

    strategy = schema["/foo/{param_path}"]["GET"].as_strategy()

    @given(case=strategy)
    @settings(deadline=None, max_examples=1)
    def test(case):
        # Then they should be converted to their JSON equivalents
        assume(case.path_parameters["param_path"] == expected)
        assume(case.query["param_query"] == expected)

    test()


@pytest.mark.hypothesis_nested
def test_is_valid_query_strategy():
    strategy = st.sampled_from([{"key": "1"}, {"key": "\udcff"}]).filter(is_valid_query)

    @given(strategy)
    @settings(max_examples=10)
    def test(value):
        assert value == {"key": "1"}

    test()


@pytest.mark.parametrize("spec_version", ("open_api_2", "open_api_3"))
def test_optional_payload(request, spec_version):
    # When body are not required
    raw_schema = request.getfixturevalue(f"empty_{spec_version}_schema")
    raw_schema["paths"] = {
        "/users": {
            "post": {
                "responses": {"200": {"description": "OK"}},
            }
        }
    }
    if spec_version == "open_api_2":
        raw_schema["paths"]["/users"]["post"]["parameters"] = [
            {"in": "body", "name": "body", "schema": {"type": "string"}}
        ]
    else:
        raw_schema["paths"]["/users"]["post"]["requestBody"] = {
            "content": {"application/json": {"schema": {"type": "string"}}}
        }
    schema = schemathesis.from_dict(raw_schema)
    operation = schema["/users"]["post"]
    strategy = _get_body_strategy(operation.body[0], make_positive_strategy, operation, GenerationConfig())
    # Then `None` could be generated by Schemathesis
    assert find(strategy, lambda x: x is NOT_SET) is NOT_SET


@given(data=st.data())
@settings(deadline=None)
def test_date_format(data):
    raw_schema = {
        "openapi": "3.0.2",
        "info": {"title": "Test", "description": "Test", "version": "0.1.0"},
        "paths": {
            "/data": {
                "post": {
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "format": "date",
                                    "type": "string",
                                },
                            }
                        },
                        "required": True,
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
    }
    schema = schemathesis.from_dict(raw_schema)
    strategy = schema["/data"]["POST"].as_strategy()
    case = data.draw(strategy)
    datetime.datetime.strptime(case.body, "%Y-%m-%d")


@pytest.mark.parametrize(
    "value, expected",
    (
        ({"foo": True}, {"foo": "true"}),
        ({"foo": False}, {"foo": "false"}),
        ({"foo": None}, {"foo": "null"}),
        ([{"foo": None}], [{"foo": "null"}]),
        ([{"foo": {"bar": True}}], [{"foo": {"bar": "true"}}]),
    ),
)
def test_jsonify_python_specific_types(value, expected):
    assert jsonify_python_specific_types(value) == expected


def test_health_check_failed_large_base_example(testdir, empty_open_api_3_schema, cli, snapshot_cli):
    empty_open_api_3_schema["paths"] = {
        "/data": {
            "post": {
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {"type": "array", "items": {"type": "integer"}, "minItems": 10000}
                        }
                    },
                },
                "responses": {"200": {"description": "OK"}},
            },
        },
    }
    schema_file = testdir.make_openapi_schema_file(empty_open_api_3_schema)
    # Then it should be able to generate requests
    assert cli.run(str(schema_file), "--dry-run", "--hypothesis-max-examples=1") == snapshot_cli
