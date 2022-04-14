from test.utils import assert_requests_call
from typing import Any, Dict

import jsonschema
import pytest
import yaml
from _pytest.main import ExitCode
from hypothesis import find

import schemathesis
from schemathesis._hypothesis import get_single_example
from schemathesis.models import APIOperation
from schemathesis.specs.openapi import examples
from schemathesis.specs.openapi.examples import get_examples
from schemathesis.specs.openapi.parameters import parameters_to_json_schema
from schemathesis.specs.openapi.schemas import BaseOpenAPISchema


@pytest.fixture(scope="module")
def dict_with_examples() -> Dict[str, Any]:
    return {
        "openapi": "3.0.2",
        "info": {"title": "Test", "description": "Test", "version": "0.1.0"},
        "servers": [{"url": "http://127.0.0.1:8081/{basePath}", "variables": {"basePath": {"default": "api"}}}],
        "paths": {
            "/success": {
                "post": {
                    "parameters": [
                        {
                            "name": "anyKey",
                            "in": "header",
                            "required": True,
                            "schema": {"type": "string"},
                            "examples": {"header1": {"value": "header1"}, "header2": {"value": "header2"}},
                        },
                        {
                            "name": "id",
                            "in": "query",
                            "required": True,
                            "schema": {"type": "string"},
                            "examples": {"query1": {"value": "query1"}},
                        },
                        {"name": "genericObject", "in": "query", "schema": {"type": "string"}},
                    ],
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {"type": "object", "properties": {"foo": {"type": "string"}}},
                                "examples": {
                                    "body1": {"value": {"foo": "string1"}},
                                    "body2": {"value": {"foo": "string2"}},
                                    "body3": {"value": {"foo": "string3"}},
                                },
                            }
                        }
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
    }


@pytest.fixture(scope="module")
def dict_with_property_examples() -> Dict[str, Any]:
    return {
        "openapi": "3.0.2",
        "info": {"title": "Test", "description": "Test", "version": "0.1.0"},
        "servers": [{"url": "http://127.0.0.1:8081/{basePath}", "variables": {"basePath": {"default": "api"}}}],
        "paths": {
            "/success": {
                "post": {
                    "parameters": [
                        {
                            "name": "param1",
                            "in": "query",
                            "schema": {
                                "type": "object",
                                "properties": {
                                    "param1_prop1": {"type": "string", "example": "prop1 example string"},
                                    "param1_prop2": {"type": "string", "example": "prop2 example string"},
                                    "noExampleProp": {"type": "string"},
                                },
                            },
                        },
                        {
                            "name": "param2",
                            "in": "query",
                            "schema": {
                                "type": "object",
                                "properties": {
                                    "param2_prop1": {"type": "string", "example": "prop1 example string"},
                                    "param2_prop2": {"type": "string", "example": "prop2 example string"},
                                    "noExampleProp": {"type": "string"},
                                },
                            },
                        },
                    ],
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {"foo": {"type": "string", "example": "foo example string"}},
                                },
                            }
                        }
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
    }


@pytest.fixture(scope="module")
def schema_with_examples(dict_with_examples) -> BaseOpenAPISchema:
    return schemathesis.from_dict(dict_with_examples)


@pytest.fixture(scope="module")
def schema_with_property_examples(dict_with_property_examples) -> BaseOpenAPISchema:
    return schemathesis.from_dict(dict_with_property_examples)


@pytest.fixture()
def operation(schema_with_examples) -> APIOperation:
    """Returns first (and only) API operation from schema_with_examples."""
    return next(schema_with_examples.get_all_operations()).ok()


@pytest.fixture()
def operation_with_property_examples(schema_with_property_examples) -> APIOperation:
    """Returns first (and only) API operation from schema_with_examples."""
    return next(schema_with_property_examples.get_all_operations()).ok()


def test_get_parameter_examples(operation):
    param_examples = examples.get_parameter_examples(operation.definition.raw, "examples")

    # length equals the number of parameters with examples
    assert len(param_examples) == 2

    assert param_examples[0]["type"] == "headers"
    assert param_examples[0]["name"] == "anyKey"
    assert len(param_examples[0]["examples"]) == 2

    assert param_examples[1]["type"] == "query"
    assert param_examples[1]["name"] == "id"
    assert len(param_examples[1]["examples"]) == 1


def test_get_request_body_examples(operation):
    request_body_examples = examples.get_request_body_examples(operation.definition.raw, "examples")

    assert request_body_examples["type"] == "body"
    assert len(request_body_examples["examples"]) == 3


def test_get_static_parameters_from_examples(operation):
    static_parameters_list = examples.get_static_parameters_from_examples(operation, "examples")

    assert len(static_parameters_list) == 3

    # ensure that each request body example is included at least once
    assert all(
        [
            any(static_parameters["body"]["foo"] == "string1" for static_parameters in static_parameters_list),
            any(static_parameters["body"]["foo"] == "string2" for static_parameters in static_parameters_list),
            any(static_parameters["body"]["foo"] == "string3" for static_parameters in static_parameters_list),
        ]
    )
    # ensure that each header parameter example is included at least once
    assert all(
        [
            any("header1" in static_parameters["headers"]["anyKey"] for static_parameters in static_parameters_list),
            any("header2" in static_parameters["headers"]["anyKey"] for static_parameters in static_parameters_list),
        ]
    )
    # ensure that each query parameter example is included at least once
    assert any("query1" in static_parameters["query"]["id"] for static_parameters in static_parameters_list)


def test_get_strategies_from_examples(operation):
    strategies = examples.get_strategies_from_examples(operation, "examples")

    assert len(strategies) == 3
    assert all(strategy is not None for strategy in strategies)


def test_merge_examples_no_body_examples():
    parameter_examples = [
        {"type": "query", "name": "queryParam", "examples": ["example1", "example2", "example3"]},
        {"type": "headers", "name": "headerParam", "examples": ["example1"]},
        {"type": "path_parameters", "name": "pathParam", "examples": ["example1", "example2"]},
    ]
    request_body_examples = {}
    result = examples.merge_examples(parameter_examples, request_body_examples)

    assert len(result) == 3
    assert all(
        "query" in static_parameters and "queryParam" in static_parameters["query"] for static_parameters in result
    )
    assert all(
        "headers" in static_parameters and "headerParam" in static_parameters["headers"] for static_parameters in result
    )
    assert all(
        "path_parameters" in static_parameters and "pathParam" in static_parameters["path_parameters"]
        for static_parameters in result
    )


def test_merge_examples_with_body_examples():
    parameter_examples = []
    request_body_examples = {
        "type": "body",
        "examples": [{"foo": "example1"}, {"foo": "example2"}, {"foo": "example3"}],
    }
    result = examples.merge_examples(parameter_examples, request_body_examples)

    assert len(result) == 3
    assert all("body" in static_parameters and "foo" in static_parameters["body"] for static_parameters in result)


def test_merge_examples_with_empty_examples():
    parameter_examples = []
    request_body_examples = {
        "type": "body",
        "examples": [],
    }
    result = examples.merge_examples(parameter_examples, request_body_examples)

    assert len(result) == 0


def test_examples_from_cli(app, testdir, cli, base_url, schema_with_examples):
    schema = schema_with_examples.raw_schema
    app["config"].update({"schema_data": schema})
    schema_file = testdir.makefile(".yaml", schema=yaml.dump(schema))
    result = cli.run(
        str(schema_file),
        f"--base-url={base_url}",
        "--hypothesis-phases=explicit",
    )

    assert result.exit_code == ExitCode.OK, result.stdout
    # The request body has the 3 examples defined. Because 3 is the most examples defined
    # for any parameter, we expect to generate 3 requests.
    not_a_server_line = next(filter(lambda line: "not_a_server_error" in line, result.stdout.split("\n")))
    assert "3 / 3 passed" in not_a_server_line


def test_get_object_example_from_properties():
    mock_object_schema: Dict[str, Any] = {
        "type": "object",
        "properties": {
            "prop1": {"type": "string", "example": "prop1 example string"},
            "prop2": {
                "type": "object",
                "properties": {"sub_prop": {"type": "string"}},  # examples at sub_prop level not supported
                "example": {"sub_prop": "prop2 example string"},
            },
        },
    }
    example = examples.get_object_example_from_properties(mock_object_schema)
    assert "prop1" in example
    assert "prop2" in example
    assert example["prop1"] == "prop1 example string"
    assert example["prop2"]["sub_prop"] == "prop2 example string"


def test_get_parameter_example_from_properties():
    schema: Dict[str, Any] = {
        "parameters": [
            {
                "name": "param1",
                "in": "query",
                "schema": {
                    "type": "object",
                    "properties": {
                        "prop1": {"type": "string", "example": "prop1 example string"},
                        "prop2": {"type": "string", "example": "prop2 example string"},
                        "noExampleProp": {"type": "string"},
                    },
                },
            }
        ]
    }
    example = examples.get_parameter_example_from_properties(schema)
    assert "query" in example
    assert example["query"] == {"param1": {"prop1": "prop1 example string", "prop2": "prop2 example string"}}


def test_get_request_body_example_from_properties():
    schema: Dict[str, Any] = {
        "requestBody": {
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {"foo": {"type": "string", "example": "foo example string"}},
                    },
                }
            }
        }
    }
    example = examples.get_request_body_example_from_properties(schema)
    assert "body" in example
    assert example["body"] == {"foo": "foo example string"}


def test_get_static_parameters_from_properties(operation_with_property_examples):
    example = examples.get_static_parameters_from_properties(operation_with_property_examples)
    assert "query" in example
    assert "param1" in example["query"]
    assert "param2" in example["query"]
    assert example["query"]["param1"] == {
        "param1_prop1": "prop1 example string",
        "param1_prop2": "prop2 example string",
    }
    assert example["query"]["param2"] == {
        "param2_prop1": "prop1 example string",
        "param2_prop2": "prop2 example string",
    }
    assert "body" in example
    assert example["body"] == {"foo": "foo example string"}


def test_static_parameters_union_1():
    sp1 = {"headers": {"header1": "example1 string"}, "body": {"foo1": "example1 string"}}
    sp2 = {"headers": {"header2": "example2 string"}, "body": {"foo2": "example2 string"}}

    full_sp1, full_sp2 = examples.static_parameters_union(sp1, sp2)
    assert "header1" in full_sp1["headers"] and full_sp1["headers"]["header1"] == "example1 string"
    assert "header2" in full_sp1["headers"] and full_sp1["headers"]["header2"] == "example2 string"
    assert "header1" in full_sp2["headers"] and full_sp2["headers"]["header1"] == "example1 string"
    assert "header2" in full_sp2["headers"] and full_sp2["headers"]["header2"] == "example2 string"

    assert full_sp1["body"] == {"foo1": "example1 string"}
    assert full_sp2["body"] == {"foo2": "example2 string"}


def test_static_parameters_union_0():
    sp1 = {"headers": {"header1": "example1 string"}, "body": {"foo1": "example1 string"}}
    sp2 = {}

    full_sp = examples.static_parameters_union(sp1, sp2)
    full_sp1 = full_sp[0]
    assert len(full_sp) == 1
    assert "header1" in full_sp1["headers"] and full_sp1["headers"]["header1"] == "example1 string"
    assert full_sp1["body"] == {"foo1": "example1 string"}


def test_multipart_examples():
    # Regression after parameters refactoring
    # When the schema contains examples for multipart forms
    raw_schema = {
        "openapi": "3.0.0",
        "info": {"title": "Sample API", "description": "API description in Markdown.", "version": "1.0.0"},
        "paths": {
            "/test": {
                "post": {
                    "description": "Test",
                    "requestBody": {
                        "content": {
                            "multipart/form-data": {
                                "schema": {
                                    "properties": {
                                        "key": {
                                            "example": "test",
                                            "type": "string",
                                        },
                                    },
                                    "type": "object",
                                }
                            }
                        }
                    },
                    "responses": {"default": {"description": "OK"}},
                },
            },
        },
    }
    schema = schemathesis.from_dict(raw_schema)
    # Then examples should be correctly generated
    strategies = schema["/test"]["POST"].get_strategies_from_examples()
    assert len(strategies) == 1
    assert find(strategies[0], lambda case: case.body == {"key": "test"})


def test_invalid_x_examples(empty_open_api_2_schema):
    # See GH-982
    # When an Open API 2.0 schema contains a non-object type in `x-examples`
    empty_open_api_2_schema["paths"] = {
        "/test": {
            "post": {
                "parameters": [
                    {"name": "body", "in": "body", "schema": {"type": "integer"}, "x-examples": {"foo": "value"}}
                ],
                "responses": {"default": {"description": "OK"}},
            }
        }
    }
    schema = schemathesis.from_dict(empty_open_api_2_schema)
    # Then such examples should be skipped as invalid (there should be an object)
    assert schema["/test"]["POST"].get_strategies_from_examples() == []


def test_partial_examples(empty_open_api_3_schema):
    # When the API schema contains multiple parameters in the same location
    # And some of them don't have explicit examples and others do
    empty_open_api_3_schema["paths"] = {
        "/test/{foo}/{bar}/": {
            "post": {
                "parameters": [
                    {"name": "foo", "in": "path", "required": True, "schema": {"type": "string", "enum": ["A"]}},
                    {
                        "name": "bar",
                        "in": "path",
                        "required": True,
                        "schema": {"type": "string", "example": "bar-example"},
                    },
                ],
                "responses": {"default": {"description": "OK"}},
            }
        }
    }
    schema = schemathesis.from_dict(empty_open_api_3_schema)
    operation = schema["/test/{foo}/{bar}/"]["POST"]
    strategy = operation.get_strategies_from_examples()[0]
    # Then all generated examples should have those missing parts generated according to the API schema
    example = get_single_example(strategy)
    parameters_schema = parameters_to_json_schema(operation, operation.path_parameters)
    jsonschema.validate(example.path_parameters, parameters_schema)


def test_external_value(empty_open_api_3_schema, server):
    # When the API schema contains examples via `externalValue` keyword
    empty_open_api_3_schema["paths"] = {
        "/test/": {
            "post": {
                "requestBody": {
                    "content": {
                        "application/json": {
                            "schema": {"type": "integer"},
                            "examples": {"answer": {"externalValue": f"http://127.0.0.1:{server['port']}/answer.json"}},
                        }
                    }
                },
                "responses": {"default": {"description": "OK"}},
            }
        }
    }
    schema = schemathesis.from_dict(empty_open_api_3_schema)
    operation = schema["/test/"]["POST"]
    strategy = operation.get_strategies_from_examples()[0]
    # Then this example should be used
    example = get_single_example(strategy)
    assert example.body == b"42"
    # And this data should be OK to send
    assert_requests_call(example)
    assert example.as_werkzeug_kwargs()["data"] == b"42"


def test_external_value_network_error(empty_open_api_3_schema):
    # When the external value is not available
    empty_open_api_3_schema["paths"] = {
        "/test/": {
            "post": {
                "requestBody": {
                    "content": {
                        "application/json": {
                            "schema": {"type": "integer"},
                            "examples": {
                                "answer": {
                                    # Not available
                                    "externalValue": "http://127.0.0.1:1/answer.json"
                                }
                            },
                        }
                    }
                },
                "responses": {"default": {"description": "OK"}},
            }
        }
    }
    schema = schemathesis.from_dict(empty_open_api_3_schema)
    operation = schema["/test/"]["POST"]
    # Then this example should not be used
    assert not operation.get_strategies_from_examples()


@pytest.mark.parametrize(
    "value, expected",
    (
        ({"foo": {"value": 42}}, [42]),
        ({"foo": {}}, []),
    ),
)
def test_empty_example(value, expected, server):
    assert list(get_examples(value)) == expected
