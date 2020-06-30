import pytest
import yaml
from _pytest.main import ExitCode

import schemathesis
from schemathesis.models import Endpoint
from schemathesis.specs.openapi import examples
from schemathesis.specs.openapi.schemas import BaseOpenAPISchema


@pytest.fixture(scope="module")
def schema_with_examples() -> BaseOpenAPISchema:
    return schemathesis.from_dict(
        {
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
                            {"name": "idWithoutExamples", "in": "query", "schema": {"type": "string"}},
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
    )


@pytest.fixture()
def endpoint(schema_with_examples) -> Endpoint:
    """Returns first (and only) endpoint from schema_with_examples."""
    return next(schema_with_examples.get_all_endpoints())


def test_get_param_examples(endpoint):
    param_examples = examples.get_param_examples(endpoint.definition.raw, "examples")

    # length equals the number of parameters with examples
    assert len(param_examples) == 2

    assert param_examples[0]["type"] == "headers"
    assert param_examples[0]["name"] == "anyKey"
    assert len(param_examples[0]["examples"]) == 2

    assert param_examples[1]["type"] == "query"
    assert param_examples[1]["name"] == "id"
    assert len(param_examples[1]["examples"]) == 1


def test_get_request_body_examples(endpoint):
    request_body_examples = examples.get_request_body_examples(endpoint.definition.raw, "examples")

    assert request_body_examples["type"] == "body"
    assert len(request_body_examples["examples"]) == 3


def test_get_static_parameters_from_examples(endpoint):
    static_parameters_list = examples.get_static_parameters_from_examples(endpoint, "examples")

    assert len(static_parameters_list) == 3

    # ensure that each request body example is included at least once
    assert all(
        [
            any("string1" == static_parameters["body"]["foo"] for static_parameters in static_parameters_list),
            any("string2" == static_parameters["body"]["foo"] for static_parameters in static_parameters_list),
            any("string3" == static_parameters["body"]["foo"] for static_parameters in static_parameters_list),
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


def test_get_strategies_from_examples(endpoint):
    strategies = examples.get_strategies_from_examples(endpoint, "examples")

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


def test_examples_from_cli(app, testdir, cli, base_url, schema_with_examples):
    schema = schema_with_examples.raw_schema
    app["config"].update({"schema_data": schema})
    schema_file = testdir.makefile(".yaml", schema=yaml.dump(schema))
    result = cli.run(str(schema_file), f"--base-url={base_url}", "--hypothesis-phases=explicit",)

    assert result.exit_code == ExitCode.OK, result.stdout
    # The request body has the 3 examples defined. Because 3 is the most examples defined
    # for any parameter, we expect to generate 3 requests.
    not_a_server_line = next(filter(lambda line: "not_a_server_error" in line, result.stdout.split("\n")))
    assert "3 / 3 passed" in not_a_server_line
