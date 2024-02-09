from __future__ import annotations

from unittest.mock import ANY

from test.utils import assert_requests_call
from typing import Any

import jsonschema
import pytest
import yaml
from _pytest.main import ExitCode
from hypothesis import find

import schemathesis
from schemathesis._hypothesis import get_single_example
from schemathesis.models import APIOperation
from schemathesis.specs.openapi import examples
from schemathesis.specs.openapi.examples import (
    extract_inner_examples,
    ParameterExample,
)
from schemathesis.specs.openapi.parameters import parameters_to_json_schema
from schemathesis.specs.openapi.schemas import BaseOpenAPISchema


@pytest.fixture(scope="module")
def dict_with_examples() -> dict[str, Any]:
    return {
        "openapi": "3.0.2",
        "info": {"title": "Test", "description": "Test", "version": "0.1.0"},
        "servers": [{"url": "http://127.0.0.1:8081/{basePath}", "variables": {"basePath": {"default": "api"}}}],
        "paths": {
            "/success": {
                "parameters": [
                    {
                        "name": "SESSION",
                        "in": "cookie",
                        "required": True,
                        "schema": {"type": "string", "example": "cookie2", "examples": ["cookie3"]},
                        "example": "cookie0",
                        "examples": {
                            "cookie1": {"value": "cookie1"},
                            "cookie4": {"$ref": "#/components/examples/Referenced3"},
                        },
                    },
                ],
                "post": {
                    "parameters": [
                        {
                            "name": "anyKey",
                            "in": "header",
                            "required": True,
                            "schema": {"type": "string"},
                            "example": "header0",
                            "examples": {"header1": {"value": "header1"}, "header2": {"value": "header2"}},
                        },
                        {
                            "name": "id",
                            "in": "query",
                            "required": True,
                            "schema": {"type": "string"},
                            "example": "query0",
                            "examples": {
                                "query1": {"value": "query1"},
                                "query3": {"$ref": "#/components/examples/Referenced3"},
                            },
                        },
                        {"name": "genericObject", "in": "query", "schema": {"type": "string"}},
                        {"$ref": "#/components/parameters/Referenced"},
                    ],
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {"foo": {"type": "string"}},
                                    "example": {"foo": "string4"},
                                    "examples": [{"foo": "string5"}],
                                },
                                "example": {"foo": "string0"},
                                "examples": {
                                    "body1": {"value": {"foo": "string1"}},
                                    "body2": {"value": {"foo": "string2"}},
                                    "body3": {"value": {"foo": "string3"}},
                                },
                            },
                            "multipart/form-data": {
                                "schema": {"type": "object", "properties": {"bar": {"type": "string"}}},
                                "example": {"bar": "string0"},
                                "examples": {
                                    "body1": {"value": {"bar": "string1"}},
                                    "body2": {"$ref": "#/components/examples/Referenced2"},
                                },
                            },
                        }
                    },
                    "responses": {"200": {"description": "OK"}},
                },
            }
        },
        "components": {
            "parameters": {
                "Referenced": {
                    "name": "Referenced",
                    "in": "query",
                    "required": True,
                    "example": "Ref-1",
                    "schema": {
                        "type": "string",
                        "example": "Ref-2",
                    },
                    "examples": {"referenced-3": {"$ref": "#/components/examples/Referenced3"}},
                }
            },
            "examples": {
                "Referenced2": {"bar": "referenced-body2"},
                "Referenced3": "referenced-string3",
            },
        },
    }


@pytest.fixture(scope="module")
def dict_with_property_examples() -> dict[str, Any]:
    return {
        "openapi": "3.0.2",
        "info": {"title": "Test", "description": "Test", "version": "0.1.0"},
        "servers": [{"url": "http://127.0.0.1:8081/{basePath}", "variables": {"basePath": {"default": "api"}}}],
        "paths": {
            "/success": {
                "post": {
                    "parameters": [
                        {
                            "name": "q-1",
                            "in": "query",
                            "schema": {
                                "type": "object",
                                "properties": {
                                    "foo-1": {"type": "string", "example": "foo-11", "examples": ["foo-12"]},
                                    "bar-1": {"type": "string", "example": "bar-11"},
                                    "spam-1": {"type": "string"},
                                },
                            },
                        },
                        {
                            "name": "q-2",
                            "in": "query",
                            "schema": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "foo-2": {"type": "string", "example": "foo-21"},
                                        "bar-2": {"type": "string", "example": "bar-21", "examples": ["bar-22"]},
                                        "spam-2": {"type": "string"},
                                    },
                                    "required": ["spam-2"],
                                },
                            },
                        },
                    ],
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "key": {"type": "string", "example": "json-key-1", "examples": ["json-key-2"]}
                                    },
                                },
                            },
                            "multipart/form-data": {
                                "schema": {
                                    "type": "array",
                                    "items": {
                                        "type": "object",
                                        "properties": {
                                            "key": {
                                                "type": "string",
                                                "example": "form-key-1",
                                                "examples": ["form-key-2"],
                                            }
                                        },
                                    },
                                },
                            },
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


def example_to_dict(example):
    if isinstance(example, ParameterExample):
        return {"container": example.container, "name": example.name, "value": example.value}
    return {"value": example.value, "media_type": example.media_type}


def test_extract_top_level(operation):
    top_level_examples = list(examples.extract_top_level(operation))
    extracted = [example_to_dict(example) for example in top_level_examples]
    assert extracted == [
        {"container": "headers", "name": "anyKey", "value": "header0"},
        {"container": "headers", "name": "anyKey", "value": "header1"},
        {"container": "headers", "name": "anyKey", "value": "header2"},
        {"container": "cookies", "name": "SESSION", "value": "cookie0"},
        {"container": "cookies", "name": "SESSION", "value": "cookie2"},
        {"container": "cookies", "name": "SESSION", "value": "cookie1"},
        {"container": "cookies", "name": "SESSION", "value": "referenced-string3"},
        {"container": "cookies", "name": "SESSION", "value": "cookie3"},
        {"container": "query", "name": "id", "value": "query0"},
        {"container": "query", "name": "id", "value": "query1"},
        {"container": "query", "name": "id", "value": "referenced-string3"},
        {"container": "query", "name": "Referenced", "value": "Ref-1"},
        {"container": "query", "name": "Referenced", "value": "Ref-2"},
        {"container": "query", "name": "Referenced", "value": "referenced-string3"},
        {"media_type": "application/json", "value": {"foo": "string0"}},
        {"media_type": "application/json", "value": {"foo": "string4"}},
        {"media_type": "application/json", "value": {"foo": "string1"}},
        {"media_type": "application/json", "value": {"foo": "string2"}},
        {"media_type": "application/json", "value": {"foo": "string3"}},
        {"media_type": "application/json", "value": {"foo": "string5"}},
        {"media_type": "multipart/form-data", "value": {"bar": "string0"}},
        {"media_type": "multipart/form-data", "value": {"bar": "string1"}},
        {"media_type": "multipart/form-data", "value": {"bar": "referenced-body2"}},
    ]
    assert list(examples.produce_combinations(top_level_examples)) == [
        {
            "body": {"foo": "string0"},
            "cookies": {"SESSION": "cookie0"},
            "headers": {"anyKey": "header0"},
            "media_type": "application/json",
            "query": {"Referenced": "Ref-1", "id": "query0"},
        },
        {
            "body": {"foo": "string4"},
            "cookies": {"SESSION": "cookie2"},
            "headers": {"anyKey": "header1"},
            "media_type": "application/json",
            "query": {"Referenced": "Ref-2", "id": "query1"},
        },
        {
            "body": {"foo": "string1"},
            "cookies": {"SESSION": "cookie1"},
            "headers": {"anyKey": "header2"},
            "media_type": "application/json",
            "query": {"Referenced": "referenced-string3", "id": "referenced-string3"},
        },
        {
            "body": {"foo": "string2"},
            "cookies": {"SESSION": "referenced-string3"},
            "headers": {"anyKey": "header0"},
            "media_type": "application/json",
            "query": {"Referenced": "Ref-1", "id": "query0"},
        },
        {
            "body": {"foo": "string3"},
            "cookies": {"SESSION": "cookie3"},
            "headers": {"anyKey": "header1"},
            "media_type": "application/json",
            "query": {"Referenced": "Ref-2", "id": "query1"},
        },
        {
            "body": {"foo": "string5"},
            "cookies": {"SESSION": "cookie0"},
            "headers": {"anyKey": "header0"},
            "media_type": "application/json",
            "query": {"Referenced": "Ref-1", "id": "query0"},
        },
        {
            "body": {"bar": "string0"},
            "cookies": {"SESSION": "cookie2"},
            "headers": {"anyKey": "header1"},
            "media_type": "multipart/form-data",
            "query": {"Referenced": "Ref-2", "id": "query1"},
        },
        {
            "body": {"bar": "string1"},
            "cookies": {"SESSION": "cookie1"},
            "headers": {"anyKey": "header2"},
            "media_type": "multipart/form-data",
            "query": {"Referenced": "referenced-string3", "id": "referenced-string3"},
        },
        {
            "body": {"bar": "referenced-body2"},
            "cookies": {"SESSION": "referenced-string3"},
            "headers": {"anyKey": "header0"},
            "media_type": "multipart/form-data",
            "query": {"Referenced": "Ref-1", "id": "query0"},
        },
    ]


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
    assert "9 / 9 passed" in not_a_server_line


def test_network_error_with_flaky_generation(testdir, cli, snapshot_cli, schema_with_examples):
    # Assume that there is a user-defined hook that makes data generation flaky
    module = testdir.make_importable_pyfile(
        hook="""
import schemathesis


@schemathesis.hook
def before_generate_case(context, strategy):
    seen = set()

    def is_not_seen(case) -> bool:
        hashed = hash(case)
        if hashed not in seen:
            seen.add(hashed)
            return True
        return False

    return strategy.filter(is_not_seen)
"""
    )

    schema_file = testdir.makefile(".yaml", schema=yaml.dump(schema_with_examples.raw_schema))
    assert (
        cli.main(
            "run",
            str(schema_file),
            "--base-url=http://127.0.0.1:1",
            "--hypothesis-seed=23",
            "--hypothesis-phases=generate",
            hooks=module.purebasename,
        )
        == snapshot_cli
    )


def test_extract_from_schemas(operation_with_property_examples):
    extracted = [
        example_to_dict(example) for example in examples.extract_from_schemas(operation_with_property_examples)
    ]
    assert extracted == [
        {"container": "query", "name": "q-1", "value": {"bar-1": "bar-11", "foo-1": "foo-11"}},
        {"container": "query", "name": "q-1", "value": {"bar-1": "bar-11", "foo-1": "foo-12"}},
        {"container": "query", "name": "q-2", "value": [{"bar-2": "bar-21", "foo-2": "foo-21", "spam-2": ""}]},
        {"container": "query", "name": "q-2", "value": [{"bar-2": "bar-22", "foo-2": "foo-21", "spam-2": ""}]},
        {"media_type": "application/json", "value": {"key": "json-key-1"}},
        {"media_type": "application/json", "value": {"key": "json-key-2"}},
        {"media_type": "multipart/form-data", "value": [{"key": "form-key-1"}]},
        {"media_type": "multipart/form-data", "value": [{"key": "form-key-2"}]},
    ]


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


def test_shared_examples_openapi_2(empty_open_api_2_schema):
    empty_open_api_2_schema["paths"] = {
        "/test": {
            "parameters": [
                {
                    "name": "any",
                    "in": "body",
                    "required": True,
                    "schema": {},
                },
            ],
            "post": {
                "parameters": [{"name": "body", "in": "body", "schema": {}, "x-examples": {"foo": {"value": "value"}}}],
                "responses": {"default": {"description": "OK"}},
            },
        }
    }
    schema = schemathesis.from_dict(empty_open_api_2_schema)
    strategies = schema["/test"]["POST"].get_strategies_from_examples()
    assert len(strategies) == 1
    assert find(strategies[0], lambda case: case.body == "value")


def test_examples_ref_openapi_2(empty_open_api_2_schema):
    empty_open_api_2_schema["paths"] = {
        "/test": {
            "post": {
                "parameters": [{"$ref": "#/components/parameters/Referenced"}],
                "responses": {"default": {"description": "OK"}},
            },
        }
    }
    empty_open_api_2_schema["components"] = {
        "parameters": {
            "Referenced": {
                "name": "Referenced",
                "in": "body",
                "required": True,
                "schema": {},
                "x-examples": {"example1": {"value": "value"}},
            }
        }
    }
    schema = schemathesis.from_dict(empty_open_api_2_schema)
    strategies = schema["/test"]["POST"].get_strategies_from_examples()
    assert len(strategies) == 1
    assert find(strategies[0], lambda case: case.body == "value")


@pytest.mark.parametrize("body", ("BodyDirect", "BodyRef"))
def test_examples_ref_openapi_3(empty_open_api_3_schema, body):
    empty_open_api_3_schema["paths"] = {
        "/test": {
            "post": {
                "requestBody": {"$ref": f"#/components/requestBodies/{body}"},
                "responses": {"default": {"description": "OK"}},
            },
        }
    }
    empty_open_api_3_schema["components"] = {
        "requestBodies": {
            "BodyDirect": {
                "content": {
                    "application/json": {
                        "schema": {},
                        "examples": {"example1": {"value": "value"}},
                    }
                }
            },
            "BodyRef": {"$ref": "#/components/requestBodies/BodyDirect"},
        }
    }
    schema = schemathesis.from_dict(empty_open_api_3_schema)
    strategies = schema["/test"]["POST"].get_strategies_from_examples()
    assert len(strategies) == 1
    assert find(strategies[0], lambda case: case.body == "value")


def test_boolean_subschema(empty_open_api_3_schema):
    empty_open_api_3_schema["paths"] = {
        "/test": {
            "post": {
                "requestBody": {
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "properties": {"foo": {"type": "string", "example": "foo-value"}, "bar": True},
                                "required": ["foo", "bar"],
                            },
                        }
                    }
                },
                "responses": {"default": {"description": "OK"}},
            },
        }
    }
    schema = schemathesis.from_dict(empty_open_api_3_schema)
    strategy = schema["/test"]["POST"].get_strategies_from_examples()[0]
    example = get_single_example(strategy)
    assert example.body == {"bar": ANY, "foo": "foo-value"}


def test_examples_ref_missing_components(empty_open_api_3_schema):
    empty_open_api_3_schema["paths"] = {
        "/test": {
            "post": {
                "parameters": [
                    {
                        "name": "q",
                        "in": "query",
                        "schema": {
                            "type": "object",
                            "properties": {
                                "foo-1": {"type": "string", "example": "foo-11"},
                                "spam-1": {"$ref": "#/components/schemas/Referenced"},
                            },
                            "required": ["foo-1", "spam-1"],
                        },
                    }
                ],
                "responses": {"default": {"description": "OK"}},
            },
        }
    }
    empty_open_api_3_schema["components"] = {
        "schemas": {
            "Referenced": {
                "type": "object",
                "properties": {"inner": {"$ref": "#/components/schemas/Key0"}},
                "required": ["inner"],
            },
            **{f"Key{idx}": {"$ref": f"#/components/schemas/Key{idx + 1}"} for idx in range(8)},
            "Key8": {"enum": ["example"]},
        }
    }
    schema = schemathesis.from_dict(empty_open_api_3_schema)
    strategy = schema["/test"]["POST"].get_strategies_from_examples()[0]
    example = get_single_example(strategy)
    assert example.query == {"q": {"foo-1": "foo-11", "spam-1": {"inner": "example"}}}


@pytest.mark.parametrize("key", ("anyOf", "oneOf", "allOf"))
def test_examples_in_any_of_top_level(empty_open_api_3_schema, key):
    empty_open_api_3_schema["paths"] = {
        "/test": {
            "post": {
                "parameters": [
                    {
                        "name": "q",
                        "in": "query",
                        "schema": {
                            key: [
                                {
                                    "example": "foo-1-1",
                                    "examples": ["foo-1-2"],
                                    "type": "string",
                                },
                                {
                                    "example": "foo-2-1",
                                    "examples": ["foo-2-2"],
                                    "type": "string",
                                },
                                True,
                            ]
                        },
                    }
                ],
                "requestBody": {
                    "content": {
                        "application/json": {
                            "schema": {
                                key: [
                                    {
                                        "example": "body-1-1",
                                        "examples": ["body-1-2"],
                                        "type": "string",
                                    },
                                    {
                                        "example": "body-2-1",
                                        "examples": ["body-2-2"],
                                        "type": "string",
                                    },
                                    True,
                                ]
                            }
                        },
                    }
                },
                "responses": {"default": {"description": "OK"}},
            },
        }
    }
    schema = schemathesis.from_dict(empty_open_api_3_schema)
    extracted = [example_to_dict(example) for example in examples.extract_top_level(schema["/test"]["POST"])]
    assert extracted == [
        {"container": "query", "name": "q", "value": "foo-1-1"},
        {"container": "query", "name": "q", "value": "foo-2-1"},
        {"container": "query", "name": "q", "value": "foo-1-2"},
        {"container": "query", "name": "q", "value": "foo-2-2"},
        {"media_type": "application/json", "value": "body-1-1"},
        {"media_type": "application/json", "value": "body-2-1"},
        {"media_type": "application/json", "value": "body-1-2"},
        {"media_type": "application/json", "value": "body-2-2"},
    ]


@pytest.mark.parametrize("key", ("anyOf", "oneOf", "allOf"))
def test_examples_in_any_of_in_schemas(empty_open_api_3_schema, key):
    empty_open_api_3_schema["paths"] = {
        "/test": {
            "post": {
                "parameters": [
                    {
                        "name": "q-1",
                        "in": "query",
                        "schema": {
                            "type": "object",
                            "properties": {
                                "foo-1": {
                                    key: [
                                        {
                                            "example": "foo-1-1-1",
                                            "examples": ["foo-1-1-2"],
                                            "type": "string",
                                        },
                                        {
                                            "example": "foo-1-2-1",
                                            "examples": ["foo-1-2-2"],
                                            "type": "string",
                                        },
                                        True,
                                    ]
                                },
                                "bar-1": {
                                    key: [
                                        {
                                            "example": "bar-1-1-1",
                                            "type": "string",
                                        },
                                        {
                                            "example": "bar-1-2-1",
                                            "type": "string",
                                        },
                                        True,
                                    ]
                                },
                                "spam-1": {"type": "string"},
                            },
                            "required": ["foo-1", "bar-1"],
                        },
                    }
                ],
                "requestBody": {
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "properties": {
                                    "key": {
                                        key: [
                                            {
                                                "example": "json-key-1-1",
                                                "examples": ["json-key-1-2"],
                                                "type": "string",
                                            },
                                            {
                                                "example": "json-key-2-1",
                                                "examples": ["json-key-2-2"],
                                                "type": "string",
                                            },
                                            True,
                                        ]
                                    }
                                },
                            }
                        },
                    }
                },
                "responses": {"default": {"description": "OK"}},
            },
        }
    }
    schema = schemathesis.from_dict(empty_open_api_3_schema)
    extracted = [example_to_dict(example) for example in examples.extract_from_schemas(schema["/test"]["POST"])]
    assert extracted == [
        {"container": "query", "name": "q-1", "value": {"bar-1": "bar-1-1-1", "foo-1": "foo-1-1-1"}},
        {"container": "query", "name": "q-1", "value": {"bar-1": "bar-1-2-1", "foo-1": "foo-1-1-2"}},
        {"container": "query", "name": "q-1", "value": {"bar-1": "bar-1-1-1", "foo-1": "foo-1-2-1"}},
        {"container": "query", "name": "q-1", "value": {"bar-1": "bar-1-2-1", "foo-1": "foo-1-2-2"}},
        {"media_type": "application/json", "value": {"key": "json-key-1-1"}},
        {"media_type": "application/json", "value": {"key": "json-key-1-2"}},
        {"media_type": "application/json", "value": {"key": "json-key-2-1"}},
        {"media_type": "application/json", "value": {"key": "json-key-2-2"}},
    ]


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
def test_empty_example(value, expected):
    assert list(extract_inner_examples(value, value)) == expected


def test_example_override():
    raw_schema = {
        "openapi": "3.0.2",
        "info": {"title": "Test", "description": "Test", "version": "0.1.0"},
        "servers": [{"url": "http://127.0.0.1:8081/{basePath}", "variables": {"basePath": {"default": "api"}}}],
        "paths": {
            "/success": {
                "parameters": [
                    {
                        "name": "key",
                        "in": "query",
                        "required": True,
                        "schema": {"type": "string"},
                    },
                ],
                "get": {
                    "parameters": [
                        {
                            "name": "key",
                            "in": "query",
                            "required": True,
                            "schema": {"type": "string"},
                            "examples": {"query1": {"value": "query1"}},
                        }
                    ],
                    "responses": {"200": {"description": "OK"}},
                },
            }
        },
    }
    schema = schemathesis.from_dict(raw_schema)
    operation = schema["/success"]["GET"]
    extracted = [example_to_dict(example) for example in examples.extract_top_level(operation)]
    assert extracted == [{"container": "query", "name": "key", "value": "query1"}]
