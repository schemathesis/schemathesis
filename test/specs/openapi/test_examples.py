from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import ANY

import jsonschema
import pytest
from hypothesis import HealthCheck, Phase, find, given, settings
from hypothesis import strategies as st

import schemathesis
from schemathesis.core.parameters import ParameterLocation
from schemathesis.generation.hypothesis import examples
from schemathesis.specs.openapi.adapter.parameters import parameters_to_json_schema
from schemathesis.specs.openapi.examples import (
    BodyExample,
    ParameterExample,
    extract_from_schemas,
    extract_inner_examples,
    extract_top_level,
    find_matching_in_responses,
    produce_combinations,
)
from schemathesis.transport.wsgi import WSGI_TRANSPORT
from test.utils import assert_requests_call

if TYPE_CHECKING:
    from schemathesis.schemas import APIOperation
from schemathesis.specs.openapi.schemas import OpenApiSchema


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
def schema_with_examples(dict_with_examples) -> OpenApiSchema:
    return schemathesis.openapi.from_dict(dict_with_examples)


@pytest.fixture(scope="module")
def schema_with_property_examples(dict_with_property_examples) -> OpenApiSchema:
    return schemathesis.openapi.from_dict(dict_with_property_examples)


@pytest.fixture
def operation(schema_with_examples) -> APIOperation:
    """Returns first (and only) API operation from schema_with_examples."""
    return next(schema_with_examples.get_all_operations()).ok()


@pytest.fixture
def operation_with_property_examples(schema_with_property_examples) -> APIOperation:
    """Returns first (and only) API operation from schema_with_examples."""
    return next(schema_with_property_examples.get_all_operations()).ok()


def example_to_dict(example):
    if isinstance(example, ParameterExample):
        return {"container": example.container, "name": example.name, "value": example.value}
    return {"value": example.value, "media_type": example.media_type}


def test_extract_top_level(operation):
    top_level_examples = list(extract_top_level(operation))
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
    assert list(produce_combinations(top_level_examples)) == [
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


def test_examples_from_cli(ctx, app, cli, base_url, schema_with_examples):
    schema = schema_with_examples.raw_schema
    app["config"].update({"schema_data": schema})
    schema_path = ctx.makefile(schema)
    result = cli.run_and_assert(
        str(schema_path),
        f"--url={base_url}",
        "--phases=examples",
        "--checks=not_a_server_error",
    )
    # The request body has the 3 examples defined. Because 3 is the most examples defined
    # for any parameter, we expect to generate 3 requests.
    assert "9 generated" in result.stdout


def test_network_error_with_flaky_generation(ctx, cli, snapshot_cli, schema_with_examples):
    # Assume that there is a user-defined hook that makes data generation flaky
    module = ctx.write_pymodule(
        """
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

    schema_file = ctx.makefile(schema_with_examples.raw_schema)
    assert (
        cli.main(
            "run",
            str(schema_file),
            "--url=http://127.0.0.1:1",
            "--seed=23",
            "--phases=fuzzing",
            hooks=module,
        )
        == snapshot_cli
    )


@pytest.fixture
def explicit_header(ctx):
    with ctx.check("""
@schemathesis.check
def explicit_header(ctx, response, case):
    assert case.headers["anyKey"] == "OVERRIDE"
    assert case.query["id"] == "OVERRIDE"
""") as module:
        yield module


def test_parameter_override(ctx, cli, openapi3_base_url, snapshot_cli, explicit_header):
    schema_file = ctx.openapi.write_schema(
        {
            "/success": {
                "post": {
                    "parameters": [
                        {
                            "name": "anyKey",
                            "in": "header",
                            "required": True,
                            "schema": {"type": "string"},
                            "example": "header0",
                        },
                        {
                            "name": "id",
                            "in": "query",
                            "required": True,
                            "schema": {"type": "string"},
                            "example": "query0",
                        },
                    ],
                    "responses": {"200": {"description": "OK"}},
                },
            }
        }
    )
    assert (
        cli.main(
            "run",
            str(schema_file),
            "--seed=23",
            "--phases=examples",
            f"--url={openapi3_base_url}",
            "--checks=explicit_header",
            hooks=explicit_header,
            config={
                "parameters": {
                    "anyKey": "OVERRIDE",
                    "id": "OVERRIDE",
                }
            },
        )
        == snapshot_cli
    )


def test_extract_from_schemas(operation_with_property_examples):
    extracted = [example_to_dict(example) for example in extract_from_schemas(operation_with_property_examples)]
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
    schema = schemathesis.openapi.from_dict(raw_schema)
    # Then examples should be correctly generated
    strategies = schema["/test"]["POST"].get_strategies_from_examples()
    assert len(strategies) == 1
    assert find(strategies[0], lambda case: case.body == {"key": "test"})


def test_invalid_x_examples(ctx):
    # See GH-982
    # When an Open API 2.0 schema contains a non-object type in `x-examples`
    schema = ctx.openapi.build_schema(
        {
            "/test": {
                "post": {
                    "parameters": [
                        {"name": "body", "in": "body", "schema": {"type": "integer"}, "x-examples": {"foo": "value"}}
                    ],
                    "responses": {"default": {"description": "OK"}},
                }
            }
        },
        version="2.0",
    )
    schema = schemathesis.openapi.from_dict(schema)
    # Then such examples should be skipped as invalid (there should be an object)
    assert schema["/test"]["POST"].get_strategies_from_examples() == []


def test_shared_examples_openapi_2(ctx):
    schema = ctx.openapi.build_schema(
        {
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
                    "parameters": [
                        {"name": "body", "in": "body", "schema": {}, "x-examples": {"foo": {"value": "value"}}}
                    ],
                    "responses": {"default": {"description": "OK"}},
                },
            }
        },
        version="2.0",
    )
    schema = schemathesis.openapi.from_dict(schema)
    strategies = schema["/test"]["POST"].get_strategies_from_examples()
    assert len(strategies) == 1
    assert find(strategies[0], lambda case: case.body == "value")


@pytest.mark.parametrize("examples", [{"example1": {"value": "value"}}, ["value"]])
def test_examples_ref_openapi_2(ctx, examples):
    schema = ctx.openapi.build_schema(
        {
            "/test": {
                "post": {
                    "parameters": [{"$ref": "#/components/parameters/Referenced"}],
                    "responses": {"default": {"description": "OK"}},
                },
            }
        },
        components={
            "parameters": {
                "Referenced": {
                    "name": "Referenced",
                    "in": "body",
                    "required": True,
                    "schema": {},
                    "x-examples": examples,
                }
            }
        },
        version="2.0",
    )
    schema = schemathesis.openapi.from_dict(schema)
    strategies = schema["/test"]["POST"].get_strategies_from_examples()
    assert len(strategies) == 1
    assert find(strategies[0], lambda case: case.body == "value")


@pytest.mark.parametrize("body", ["BodyDirect", "BodyRef"])
def test_examples_ref_openapi_3(ctx, body):
    schema = ctx.openapi.build_schema(
        {
            "/test": {
                "post": {
                    "requestBody": {"$ref": f"#/components/requestBodies/{body}"},
                    "responses": {"default": {"description": "OK"}},
                },
            }
        },
        components={
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
        },
    )
    schema = schemathesis.openapi.from_dict(schema)
    strategies = schema["/test"]["POST"].get_strategies_from_examples()
    assert len(strategies) == 1
    assert find(strategies[0], lambda case: case.body == "value")


def test_boolean_subschema(ctx):
    schema = ctx.openapi.build_schema(
        {
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
    )
    schema = schemathesis.openapi.from_dict(schema)
    strategy = schema["/test"]["POST"].get_strategies_from_examples()[0]
    example = examples.generate_one(strategy)
    assert example.body == {"bar": ANY, "foo": "foo-value"}


def test_examples_ref_missing_components(ctx):
    schema = ctx.openapi.build_schema(
        {
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
        },
        components={
            "schemas": {
                "Referenced": {
                    "type": "object",
                    "properties": {"inner": {"$ref": "#/components/schemas/Key0"}},
                    "required": ["inner"],
                },
                **{f"Key{idx}": {"$ref": f"#/components/schemas/Key{idx + 1}"} for idx in range(8)},
                "Key8": {"enum": ["example"]},
            }
        },
    )
    schema = schemathesis.openapi.from_dict(schema)
    strategy = schema["/test"]["POST"].get_strategies_from_examples()[0]
    example = examples.generate_one(strategy)
    assert example.query == {"q": {"foo-1": "foo-11", "spam-1": {"inner": "example"}}}


@pytest.mark.parametrize("key", ["anyOf", "oneOf"])
def test_examples_in_any_of_top_level(ctx, key):
    schema = ctx.openapi.build_schema(
        {
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
    )
    schema = schemathesis.openapi.from_dict(schema)
    extracted = [example_to_dict(example) for example in extract_top_level(schema["/test"]["POST"])]
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


def test_examples_in_all_of_top_level(ctx):
    schema = ctx.openapi.build_schema(
        {
            "/test": {
                "post": {
                    "parameters": [
                        {
                            "name": "q",
                            "in": "query",
                            "schema": {
                                "allOf": [
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
                                    "allOf": [
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
    )
    schema = schemathesis.openapi.from_dict(schema)
    extracted = [example_to_dict(example) for example in extract_top_level(schema["/test"]["POST"])]
    assert extracted == [
        {"container": "query", "name": "q", "value": "foo-1-1"},
        {"container": "query", "name": "q", "value": "foo-1-2"},
        {"container": "query", "name": "q", "value": "foo-2-1"},
        {"container": "query", "name": "q", "value": "foo-2-2"},
        {"media_type": "application/json", "value": "body-1-1"},
        {"media_type": "application/json", "value": "body-1-2"},
        {"media_type": "application/json", "value": "body-2-1"},
        {"media_type": "application/json", "value": "body-2-2"},
    ]


@pytest.mark.parametrize("key", ["anyOf", "oneOf"])
def test_examples_in_any_of_in_schemas(ctx, key):
    schema = ctx.openapi.build_schema(
        {
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
    )
    schema = schemathesis.openapi.from_dict(schema)
    extracted = [example_to_dict(example) for example in extract_from_schemas(schema["/test"]["POST"])]
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


def test_partial_examples(ctx):
    # When the API schema contains multiple parameters in the same location
    # And some of them don't have explicit examples and others do
    schema = ctx.openapi.build_schema(
        {
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
    )
    schema = schemathesis.openapi.from_dict(schema)
    operation = schema["/test/{foo}/{bar}/"]["POST"]
    strategy = operation.get_strategies_from_examples()[0]
    # Then all generated examples should have those missing parts generated according to the API schema
    example = examples.generate_one(strategy)
    parameters_schema = parameters_to_json_schema(operation.path_parameters, ParameterLocation.PATH)
    jsonschema.validate(example.path_parameters, parameters_schema)


def test_partial_examples_without_null_bytes_and_formats(ctx):
    schemathesis.openapi.format("even_4_digits", st.from_regex(r"\A[0-9]{4}\Z").filter(lambda x: int(x) % 2 == 0))
    schema = ctx.openapi.build_schema(
        {
            "/test/": {
                "post": {
                    "parameters": [
                        {
                            "name": "q1",
                            "in": "query",
                            "required": True,
                            "schema": {
                                "type": "object",
                                "properties": {"foo": {"type": "string"}},
                                "required": ["foo"],
                                "additionalProperties": False,
                            },
                        },
                        {
                            "name": "q2",
                            "in": "query",
                            "required": True,
                            "schema": {
                                "type": "object",
                                "properties": {"foo": {"type": "string", "format": "even_4_digits"}},
                                "required": ["foo"],
                                "additionalProperties": False,
                            },
                        },
                        {
                            "name": "q3",
                            "in": "query",
                            "required": True,
                            "schema": {"type": "string"},
                            "example": "test",
                        },
                    ],
                    "responses": {"default": {"description": "OK"}},
                }
            }
        }
    )
    schema = schemathesis.openapi.from_dict(schema)
    schema.config.generation.update(allow_x00=False)
    operation = schema["/test/"]["POST"]
    strategy = operation.get_strategies_from_examples()[0]

    @given(case=strategy)
    @settings(deadline=None, suppress_health_check=list(HealthCheck), phases=[Phase.generate])
    def test(case):
        assert "\x00" not in case.query["q1"]["foo"]
        assert len(case.query["q2"]["foo"]) == 4
        assert int(case.query["q2"]["foo"]) % 2 == 0

    test()


def test_external_value(ctx, server):
    # When the API schema contains examples via `externalValue` keyword
    schema = ctx.openapi.build_schema(
        {
            "/test/": {
                "post": {
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {"type": "integer"},
                                "examples": {
                                    "answer": {"externalValue": f"http://127.0.0.1:{server['port']}/answer.json"}
                                },
                            }
                        }
                    },
                    "responses": {"default": {"description": "OK"}},
                }
            }
        }
    )
    schema = schemathesis.openapi.from_dict(schema)
    operation = schema["/test/"]["POST"]
    strategy = operation.get_strategies_from_examples()[0]
    # Then this example should be used
    example = examples.generate_one(strategy)
    assert example.body == b"42"
    # And this data should be OK to send
    assert_requests_call(example)
    assert WSGI_TRANSPORT.serialize_case(example)["data"] == b"42"


def test_external_value_network_error(ctx):
    # When the external value is not available
    schema = ctx.openapi.build_schema(
        {
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
    )
    schema = schemathesis.openapi.from_dict(schema)
    operation = schema["/test/"]["POST"]
    # Then this example should not be used
    assert not operation.get_strategies_from_examples()


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ({"foo": {"value": 42}}, [42]),
        ({"foo": {}}, []),
    ],
)
def test_empty_example(value, expected):
    assert list(extract_inner_examples(value, None)) == expected


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
    schema = schemathesis.openapi.from_dict(raw_schema)
    operation = schema["/success"]["GET"]
    extracted = [example_to_dict(example) for example in extract_top_level(operation)]
    assert extracted == [{"container": "query", "name": "key", "value": "query1"}]


def test_no_wrapped_examples():
    # See GH-2238
    raw_schema = {
        "openapi": "3.0.3",
        "info": {"title": "Test API", "version": "1.0.0"},
        "paths": {
            "/register": {
                "post": {
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/Register"},
                                "examples": {"objectExample": {"$ref": "#/components/examples/objectExample"}},
                            }
                        }
                    },
                    "responses": {"200": {"description": "Successful operation"}},
                }
            }
        },
        "components": {
            "schemas": {
                "Register": {
                    "type": "object",
                    "properties": {
                        "username": {"type": "string", "example": "username"},
                        "email": {"type": "string", "example": "john.doe@email.com"},
                        "password": {"type": "string", "example": "password"},
                    },
                },
            },
            "examples": {
                "objectExample": {
                    "summary": "summary",
                    "value": {"username": "username1", "email": "john.doe@email.com", "password": "password1"},
                },
            },
        },
    }
    schema = schemathesis.openapi.from_dict(raw_schema)
    operation = schema["/register"]["POST"]
    extracted = [example_to_dict(example) for example in extract_from_schemas(operation)]
    assert extracted == [
        {
            "media_type": "application/json",
            "value": {"username": "username", "email": "john.doe@email.com", "password": "password"},
        }
    ]
    extracted = [example_to_dict(example) for example in extract_top_level(operation)]
    assert extracted == [
        {
            "media_type": "application/json",
            "value": {"email": "john.doe@email.com", "password": "password1", "username": "username1"},
        },
    ]


def test_openapi_2_example():
    raw_schema = {
        "swagger": "2.0",
        "info": {"version": "0.1.0", "title": "Item List API", "license": {"name": "Test"}},
        "schemes": ["http"],
        "host": "localhost:8083",
        "securityDefinitions": {"ApiKeyAuth": {"in": "header", "name": "Authorization", "type": "apiKey"}},
        "paths": {
            "/items": {
                "post": {
                    "summary": "Add a new item to the list",
                    "operationId": "addItem",
                    "tags": ["items"],
                    "parameters": [
                        {
                            "in": "body",
                            "name": "Item",
                            "required": True,
                            "description": "item object for POST body",
                            "schema": {"$ref": "#/definitions/Item"},
                        }
                    ],
                    "responses": {
                        "201": {"description": "Item added successfully", "schema": {"$ref": "#/definitions/Item"}},
                        "400": {"description": "Bad Request", "schema": {"$ref": "#/definitions/Error"}},
                        "500": {"description": "Internal server error", "schema": {"$ref": "#/definitions/Error"}},
                        "401": {"description": "Access token is missing or invalid"},
                    },
                    "consumes": ["application/json"],
                    "produces": ["application/json"],
                    "security": [{"ApiKeyAuth": []}],
                }
            }
        },
        "definitions": {
            "Items": {"items": {"$ref": "#/definitions/Item"}, "type": "array"},
            "Error": {
                "type": "object",
                "required": ["message"],
                "properties": {"message": {"type": "string"}, "data": {"type": "object"}},
            },
            "Item": {
                "type": "object",
                "required": ["title", "description"],
                "properties": {
                    "id": {
                        "type": "string",
                        "description": "ID of the list item.",
                        "format": "uuid",
                        "example": "415feabd-9114-44af-bc78-479299dadc1e",
                    },
                    "title": {"type": "string", "description": "Title of the item.", "example": "Learn Music"},
                    "description": {
                        "type": "string",
                        "description": "More detailed description of the item.",
                        "example": "learn to play drums",
                    },
                    "year": {"type": "string", "description": "Target year", "pattern": "^\\d{4}", "example": "1987"},
                },
                "example": {"title": "Reading", "description": "Read a comic"},
            },
        },
    }
    schema = schemathesis.openapi.from_dict(raw_schema)
    operation = schema["/items"]["POST"]
    extracted = [example_to_dict(example) for example in extract_from_schemas(operation)]
    assert extracted == [
        {
            "value": {
                "id": "415feabd-9114-44af-bc78-479299dadc1e",
                "title": "Learn Music",
                "description": "learn to play drums",
                "year": "1987",
            },
            "media_type": "application/json",
        }
    ]
    extracted = [example_to_dict(example) for example in extract_top_level(operation)]
    assert extracted == [
        {
            "value": {"title": "Reading", "description": "Read a comic"},
            "media_type": "application/json",
        }
    ]


def test_property_examples_behind_ref():
    raw_schema = {
        "swagger": "2.0",
        "info": {"version": "0.1.0", "title": "Item List API"},
        "schemes": ["http"],
        "host": "localhost:8083",
        "securityDefinitions": {"ApiKeyAuth": {"in": "header", "name": "Authorization", "type": "apiKey"}},
        "paths": {
            "/trees": {
                "post": {
                    "responses": {"200": {"description": "Ok"}},
                    "parameters": [
                        {
                            "in": "body",
                            "name": "Tree",
                            "schema": {"$ref": "#/definitions/Tree"},
                            "description": "tree to create",
                            "required": True,
                        }
                    ],
                }
            },
        },
        "definitions": {
            "Tree": {
                "properties": {
                    "id": {
                        "format": "uuid",
                        "type": "string",
                        "example": "415feabd-9114-44af-bc78-479299dadc1e",
                    },
                    "year": {"pattern": "^\\d{4}", "type": "string", "example": "2020"},
                    "branches": {"items": {"$ref": "#/definitions/Branch"}, "type": "array"},
                    "description": {"type": "string", "example": "white birch tree"},
                    "name": {"type": "string", "example": "Birch"},
                    "bird": {"$ref": "#/definitions/Bird", "type": "object"},
                },
                "required": ["name", "description"],
                "type": "object",
                "example": {"description": "Pine tree", "name": "Pine"},
            },
            "Nest": {
                "properties": {
                    "eggs": {"items": {"$ref": "#/definitions/Egg"}, "type": "array"},
                    "description": {"type": "string", "example": "first nest"},
                    "name": {"type": "string", "example": "nest1"},
                },
                "required": ["name"],
                "type": "object",
            },
            "Bark": {
                "properties": {
                    "description": {"type": "string", "example": "brown bark"},
                    "name": {"type": "string", "example": "bark1"},
                },
                "required": ["name", "description"],
                "type": "object",
            },
            "Leaf": {
                "properties": {
                    "description": {"type": "string", "example": "main leaf"},
                    "name": {"type": "string", "example": "leaf1"},
                },
                "required": ["name", "description"],
                "type": "object",
            },
            "Branch": {
                "properties": {
                    "leaves": {"items": {"$ref": "#/definitions/Leaf"}, "type": "array"},
                    "bark": {"$ref": "#/definitions/Bark", "type": "object"},
                    "description": {"type": "string", "example": "main branch"},
                    "name": {"type": "string", "example": "branch1"},
                },
                "required": ["name", "description"],
                "type": "object",
            },
            "Bird": {
                "properties": {
                    "nest": {"$ref": "#/definitions/Nest", "type": "object"},
                    "description": {"type": "string", "example": "brown sparrow"},
                    "name": {"type": "string", "example": "sparrow"},
                },
                "required": ["name", "description"],
                "type": "object",
            },
            "Trees": {"items": {"$ref": "#/definitions/Tree"}, "type": "array"},
            "Egg": {
                "properties": {
                    "description": {"type": "string", "example": "first egg"},
                    "name": {"type": "string", "example": "egg1"},
                },
                "required": ["name", "description"],
                "type": "object",
            },
        },
    }
    schema = schemathesis.openapi.from_dict(raw_schema)
    operation = schema["/trees"]["POST"]
    extracted = [example_to_dict(example) for example in extract_from_schemas(operation)]
    assert extracted == [
        {
            "value": {
                "id": "415feabd-9114-44af-bc78-479299dadc1e",
                "year": "2020",
                "branches": [
                    {
                        "leaves": [{"description": "main leaf", "name": "leaf1"}],
                        "bark": {"description": "brown bark", "name": "bark1"},
                        "description": "main branch",
                        "name": "branch1",
                    }
                ],
                "description": "white birch tree",
                "name": "Birch",
                "bird": {
                    "nest": {
                        "eggs": [{"description": "first egg", "name": "egg1"}],
                        "description": "first nest",
                        "name": "nest1",
                    },
                    "description": "brown sparrow",
                    "name": "sparrow",
                },
            },
            "media_type": "application/json",
        }
    ]


def test_property_examples_with_all_of():
    # See GH-2375
    raw_schema = {
        "openapi": "3.0.3",
        "info": {"title": "Test API", "version": "1.0.0"},
        "paths": {
            "/peers": {
                "post": {
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/peer"},
                            }
                        }
                    },
                    "responses": {"200": {"description": "Successful operation"}},
                }
            }
        },
        "components": {
            "schemas": {
                "peer": {
                    "type": "object",
                    "properties": {"outbound_proxy": {"$ref": "#/components/schemas/outbound_proxy_with_port"}},
                    "required": ["outbound_proxy"],
                },
                "outbound_proxy_common": {
                    "type": "object",
                    "properties": {"host": {"type": "string", "format": "ipv4", "example": "10.22.22.191"}},
                    "required": ["host"],
                },
                "outbound_proxy_with_port": {
                    "allOf": [
                        {"$ref": "#/components/schemas/outbound_proxy_common"},
                        {
                            "type": "object",
                            "properties": {"port": {"type": "integer", "example": 8080}},
                            "required": ["port"],
                        },
                    ]
                },
            }
        },
    }
    schema = schemathesis.openapi.from_dict(raw_schema)
    operation = schema["/peers"]["POST"]
    extracted = [example_to_dict(example) for example in extract_from_schemas(operation)]
    assert extracted == [
        {
            "value": {"outbound_proxy": {"host": "10.22.22.191", "port": 8080}},
            "media_type": "application/json",
        }
    ]


@pytest.mark.parametrize(
    ("parent_example_field", "parent_example_value", "base_example", "expected_count", "expected_values"),
    [
        # Parent has 'example' field - should take precedence
        (
            "example",
            {"name": "example-name", "numeric_field": 42},
            {"name": "example-name"},
            1,
            [{"name": "example-name", "numeric_field": 42}],
        ),
        # Parent has 'examples' array - should take precedence
        (
            "examples",
            [
                {"name": "example-1", "numeric_field": 10},
                {"name": "example-2", "numeric_field": 20},
            ],
            {"name": "base-example"},
            2,
            [
                {"name": "example-1", "numeric_field": 10},
                {"name": "example-2", "numeric_field": 20},
            ],
        ),
    ],
)
def test_parent_example_takes_precedence_over_allof(
    ctx, parent_example_field, parent_example_value, base_example, expected_count, expected_values
):
    # See GH-3268
    # When a parent schema has allOf with a base schema that has an example,
    # and the parent has its own example, only the parent's example should be used.
    raw_schema = ctx.openapi.build_schema(
        {
            "/resource": {
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {"application/json": {"schema": {"$ref": "#/components/schemas/resource"}}},
                    },
                    "responses": {"204": {"description": "Done"}},
                }
            }
        },
        version="3.0.3",
        components={
            "schemas": {
                "resource": {
                    "type": "object",
                    "allOf": [{"$ref": "#/components/schemas/base_resource"}],
                    "required": ["numeric_field"],
                    "properties": {
                        "numeric_field": {
                            "type": "integer",
                            "format": "int32",
                            "minimum": 0,
                            "maximum": 100,
                        }
                    },
                    parent_example_field: parent_example_value,
                },
                "base_resource": {
                    "type": "object",
                    "properties": {"name": {"type": "string", "minLength": 1}},
                    "example": base_example,
                },
            }
        },
    )
    schema = schemathesis.openapi.from_dict(raw_schema)
    operation = schema["/resource"]["POST"]

    extracted = [example_to_dict(example) for example in extract_top_level(operation)]

    # Should only get parent's examples, not base's incomplete example
    assert len(extracted) == expected_count
    for expected_value in expected_values:
        assert any(e["value"] == expected_value for e in extracted), f"Expected {expected_value} in extracted examples"
    # Ensure base example is NOT included
    assert not any(e["value"] == base_example for e in extracted), "Base example should not be extracted"


def test_multiple_allof_items_with_parent_example(ctx):
    # See GH-3268
    # When allOf contains multiple schemas with their own examples,
    # parent's example should still take precedence over all of them.
    raw_schema = ctx.openapi.build_schema(
        {
            "/resource": {
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {"application/json": {"schema": {"$ref": "#/components/schemas/resource"}}},
                    },
                    "responses": {"204": {"description": "Done"}},
                }
            }
        },
        version="3.0.3",
        components={
            "schemas": {
                "resource": {
                    "type": "object",
                    "allOf": [
                        {"$ref": "#/components/schemas/base_resource"},
                        {
                            "type": "object",
                            "properties": {"age": {"type": "integer"}},
                            "example": {"age": 25},  # Partial example in allOf item
                        },
                    ],
                    "required": ["id"],
                    "properties": {"id": {"type": "integer"}},
                    # Complete example for the merged schema
                    "example": {"name": "John", "age": 30, "id": 123},
                },
                "base_resource": {
                    "type": "object",
                    "properties": {"name": {"type": "string"}},
                    "example": {"name": "Base"},
                },
            }
        },
    )
    schema = schemathesis.openapi.from_dict(raw_schema)
    operation = schema["/resource"]["POST"]

    extracted = [example_to_dict(example) for example in extract_top_level(operation)]

    # Should only get parent's complete example, not the partial ones from allOf items
    assert len(extracted) == 1
    assert extracted[0]["value"] == {"name": "John", "age": 30, "id": 123}


def test_allof_without_parent_example_preserves_existing_behavior(ctx):
    # See GH-3268
    # When parent has NO example, allOf examples should still be used.
    raw_schema = ctx.openapi.build_schema(
        {
            "/resource": {
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {"application/json": {"schema": {"$ref": "#/components/schemas/resource"}}},
                    },
                    "responses": {"204": {"description": "Done"}},
                }
            }
        },
        components={
            "schemas": {
                "resource": {
                    "type": "object",
                    "allOf": [{"$ref": "#/components/schemas/base_resource"}],
                    "required": ["numeric_field"],
                    "properties": {"numeric_field": {"type": "integer"}},
                    # NO example in parent schema
                },
                "base_resource": {
                    "type": "object",
                    "properties": {"name": {"type": "string"}},
                    "example": {"name": "example-name"},
                },
            }
        },
    )
    schema = schemathesis.openapi.from_dict(raw_schema)
    operation = schema["/resource"]["POST"]

    extracted = [example_to_dict(example) for example in extract_top_level(operation)]

    # When parent has no example, should use example from allOf item (existing behavior)
    assert len(extracted) == 1
    assert extracted[0]["value"] == {"name": "example-name"}


@pytest.mark.parametrize(
    ("components", "expected_value"),
    [
        # Single allOf item with property examples in both parent and base
        (
            {
                "schemas": {
                    "resource": {
                        "type": "object",
                        "allOf": [{"$ref": "#/components/schemas/base_resource"}],
                        "required": ["id", "status"],
                        "properties": {
                            "id": {"type": "integer", "example": 42},
                            "status": {"type": "string", "example": "active"},
                        },
                    },
                    "base_resource": {
                        "type": "object",
                        "required": ["name"],
                        "properties": {
                            "name": {"type": "string", "example": "John Doe"},
                            "email": {"type": "string", "format": "email", "example": "john@example.com"},
                        },
                    },
                }
            },
            {
                "name": "John Doe",
                "email": "john@example.com",
                "id": 42,
                "status": "active",
            },
        ),
        # Multiple allOf items with property examples
        (
            {
                "schemas": {
                    "resource": {
                        "type": "object",
                        "allOf": [
                            {"$ref": "#/components/schemas/base_entity"},
                            {"$ref": "#/components/schemas/timestamped"},
                        ],
                        "required": ["username"],
                        "properties": {
                            "username": {"type": "string", "example": "jdoe"},
                            "role": {"type": "string", "example": "admin"},
                        },
                    },
                    "base_entity": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "integer", "example": 100},
                        },
                    },
                    "timestamped": {
                        "type": "object",
                        "properties": {
                            "created_at": {"type": "string", "format": "date-time", "example": "2025-01-01T00:00:00Z"},
                            "updated_at": {"type": "string", "format": "date-time", "example": "2025-01-02T00:00:00Z"},
                        },
                    },
                }
            },
            {
                "id": 100,
                "created_at": "2025-01-01T00:00:00Z",
                "updated_at": "2025-01-02T00:00:00Z",
                "username": "jdoe",
                "role": "admin",
            },
        ),
        # Parent has property examples, but allOf base has no examples
        (
            {
                "schemas": {
                    "resource": {
                        "type": "object",
                        "allOf": [{"$ref": "#/components/schemas/base"}],
                        "properties": {
                            "name": {"type": "string", "example": "Widget"},
                            "price": {"type": "number", "example": 19.99},
                        },
                    },
                    "base": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "integer"},
                            "created": {"type": "string", "format": "date-time"},
                        },
                    },
                }
            },
            {
                "name": "Widget",
                "price": 19.99,
            },
        ),
    ],
)
def test_property_level_examples_with_allof_and_parent_properties(ctx, components, expected_value):
    # Tests the code block that handles property-level example extraction
    # when a schema has both 'allOf' and its own 'properties'.
    # This ensures we extract property examples from ALL schemas (parent + allOf items).
    raw_schema = ctx.openapi.build_schema(
        {
            "/resource": {
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {"application/json": {"schema": {"$ref": "#/components/schemas/resource"}}},
                    },
                    "responses": {"204": {"description": "Done"}},
                }
            }
        },
        components=components,
    )
    schema = schemathesis.openapi.from_dict(raw_schema)
    operation = schema["/resource"]["POST"]

    extracted = [example_to_dict(example) for example in extract_from_schemas(operation)]

    # Should extract property-level examples from BOTH parent and allOf schemas
    assert len(extracted) == 1
    assert extracted[0] == {
        "media_type": "application/json",
        "value": expected_value,
    }


def content(schema, **kwargs):
    return {
        "description": "",
        "content": {
            "application/json": {"schema": schema, **kwargs},
        },
    }


@pytest.mark.parametrize(
    ("response", "expected"),
    [
        ({"description": "Ok"}, []),
        ({"$ref": "#/components/responses/NoExamples"}, []),
        (
            {"$ref": "#/components/responses/SingleExample"},
            [
                ("Item", {"id": "123456"}),
            ],
        ),
        (
            {"$ref": "#/components/responses/OneExample"},
            [
                ("Item", {"id": "123456"}),
            ],
        ),
        (
            {"$ref": "#/components/responses/TwoExamples"},
            [
                ("Item", {"id": "123456"}),
                ("Item", {"id": "456789"}),
            ],
        ),
        (
            content({"$ref": "#/components/schemas/Item"}, examples={"Example1": {"value": {"id": "123456"}}}),
            [
                ("Item", {"id": "123456"}),
            ],
        ),
        (
            # No `value` inside
            content({"$ref": "#/components/schemas/Item"}, examples={"Example1": {"externalValue": ""}}),
            [],
        ),
        (
            content({"$ref": "#/components/schemas/Item"}, **{"x-examples": {"Example1": {"value": {"id": "123456"}}}}),
            [
                ("Item", {"id": "123456"}),
            ],
        ),
        (
            content({"$ref": "#/components/schemas/Item"}, **{"x-examples": [{"id": "123456"}]}),
            [
                ("Item", {"id": "123456"}),
            ],
        ),
        (
            content({"$ref": "#/components/schemas/Item"}, **{"x-example": {"id": "123456"}}),
            [
                ("Item", {"id": "123456"}),
            ],
        ),
        (
            content(
                {"properties": {"id": {"type": "string"}}},
                examples={"Example1": {"value": {"id": "123456"}}},
            ),
            [
                ("200/application/json", {"id": "123456"}),
            ],
        ),
    ],
)
def test_find_in_responses(ctx, response, expected):
    schema = ctx.openapi.build_schema(
        {
            "/items/{itemId}/": {
                "get": {
                    "parameters": [{"name": "itemId", "in": "path", "schema": {"type": "string"}, "required": True}],
                    "responses": {"200": response},
                }
            }
        },
        components={
            "schemas": {
                "Item": {
                    "properties": {
                        "id": {
                            "type": "string",
                        }
                    }
                }
            },
            "responses": {
                "NoExamples": content({"$ref": "#/components/schemas/Item"}),
                "OneExample": content(
                    {"$ref": "#/components/schemas/Item"}, examples={"Example1": {"value": {"id": "123456"}}}
                ),
                "TwoExamples": content(
                    {"$ref": "#/components/schemas/Item"},
                    examples={
                        "Example1": {"value": {"id": "123456"}},
                        "Example2": {"value": {"id": "456789"}},
                    },
                ),
                "SingleExample": content({"$ref": "#/components/schemas/Item"}, example={"id": "123456"}),
            },
        },
    )
    schema = schemathesis.openapi.from_dict(schema)
    operation = schema["/items/{itemId}/"]["get"]
    assert list(operation.responses.iter_examples()) == expected

    if expected:
        strategy = st.one_of(operation.get_strategies_from_examples())
        collected = []

        @given(strategy)
        def test(case):
            collected.append(case.path_parameters)

        test()

        assert collected == [{"itemId": value["id"]} for _, value in expected]


def test_find_in_responses_only_in_2xx(ctx):
    schema = ctx.openapi.build_schema(
        {
            "/items/{id}/": {
                "get": {
                    "parameters": [{"name": "id", "in": "path", "schema": {"type": "string"}, "required": True}],
                    "responses": {
                        "400": content(
                            {
                                "properties": {
                                    "id": {"type": "string"},
                                }
                            },
                            examples={
                                "Example1": {"value": {"id": "123456"}},
                            },
                        )
                    },
                }
            }
        }
    )
    schema = schemathesis.openapi.from_dict(schema)
    operation = schema["/items/{id}/"]["get"]
    assert list(operation.responses.iter_examples()) == []


@pytest.mark.parametrize(
    ("examples", "name", "expected"),
    [
        (
            [
                ("Item", {"id": "123"}),
                ("Item", {"id": "456"}),
            ],
            "id",
            ["123", "456"],
        ),
        (
            [
                ("Item", {"itemId": "123"}),
                ("Item", {"itemId": "456"}),
            ],
            "itemId",
            ["123", "456"],
        ),
        (
            [
                ("Item", {"item": [{"itemId": "123"}, {"itemId": "789"}, {"unknown": 0}]}),
                ("Item", {"itemId": "456"}),
                ("Item", {"itemId": 789}),
                ("Item", {"item": {"itemId": "42"}}),
                ("Item", {"item": 55}),
                ("Item", {"item": [55]}),
                ("Item", {"item": [{"id": "143"}], "paginationInfo": {}}),
            ],
            "itemId",
            ["123", "789", "456", 789, "42", 55, "143"],
        ),
        (
            [
                ("ItemResult", {"item": [{"id": "143"}, {"id": 55}, [], {}], "paginationInfo": {}}),
            ],
            "itemId",
            ["143", 55],
        ),
        (
            [
                ("Item", {"id": "123"}),
                ("Item", {"id": "456"}),
            ],
            "itemId",
            ["123", "456"],
        ),
        (
            [
                ("Item", {"ItemId": "123"}),
                ("Item", {"ITEMID": "456"}),
            ],
            "itemId",
            ["123", "456"],
        ),
        (
            [
                ("Product", {"productId": "123"}),
                ("Product", {"product_id": "456"}),
            ],
            "id",
            ["123", "456"],
        ),
        (
            [
                ("User", {"userId": "123"}),
                ("User", {"user_id": "456"}),
                ("Item", {"itemId": "789"}),
            ],
            "userId",
            ["123", "456"],
        ),
        (
            [
                ("User", {"name": "John"}),
                ("User", {"age": 30}),
            ],
            "id",
            [],
        ),
        (
            [
                ("User", {"name": "John"}),
                ("User", {"age": 30}),
            ],
            "name",
            ["John"],
        ),
        (
            [
                ("User", {"name": "John"}),
            ],
            "unknown",
            [],
        ),
        (
            [
                ("User", None),
            ],
            "unknown",
            [],
        ),
    ],
)
def test_find_matching_in_responses(examples, name, expected):
    assert list(find_matching_in_responses(examples, name)) == expected


def test_find_matching_in_responses_yields_all():
    examples = [
        ("Item", {"id": "123"}),
        ("Item", {"id": "456"}),
        ("Product", {"id": "789"}),
        ("Product", {"productId": "101112"}),
    ]
    result = list(find_matching_in_responses(examples, "id"))
    assert result == ["123", "456", "789", "101112"]


def test_find_matching_in_responses_empty():
    assert list(find_matching_in_responses({}, "id")) == []


def test_config_override_with_examples(ctx, cli, snapshot_cli, openapi3_base_url):
    # See GH-3000
    schema_file = ctx.openapi.write_schema(
        {
            "/{primary}/subs/{secondary}": {
                "put": {
                    "parameters": [
                        {"name": "primary", "in": "path", "schema": {"type": "string"}, "required": True},
                        {
                            "name": "secondary",
                            "in": "path",
                            "schema": {"type": "string"},
                            "example": "whatever",
                            "required": True,
                        },
                    ],
                    "requestBody": {
                        "required": True,
                        "content": {"application/json": {"schema": {"$ref": "#/components/schemas/Schema"}}},
                    },
                    "responses": {"201": {"description": "OK"}},
                }
            },
        },
        components={
            "schemas": {
                "Schema": {
                    "type": "object",
                    "properties": {"property": {"schema": {"type": "string"}, "example": "whatever"}},
                }
            }
        },
    )
    assert (
        cli.main(
            "run",
            str(schema_file),
            "--phases=examples",
            f"--url={openapi3_base_url}",
            config={"parameters": {"path.primary": "primary"}},
        )
        == snapshot_cli
    )


def test_path_parameters_example_escaping(ctx, cli, snapshot_cli, openapi3_base_url):
    # See GH-3003
    schema_file = ctx.openapi.write_schema(
        {
            "/networks/{network}": {
                "get": {
                    "parameters": [
                        {
                            "name": "network",
                            "in": "path",
                            "required": True,
                            "schema": {
                                "type": "string",
                                "format": "ipv6-network",
                                "example": "fd00::/64",
                            },
                        },
                    ],
                    # Set `202` to trigger a failure
                    "responses": {"202": {"description": "Ok"}},
                }
            }
        }
    )

    result = cli.main(
        "run",
        str(schema_file),
        "--phases=examples",
        f"--url={openapi3_base_url}",
    )

    assert result == snapshot_cli


def test_non_recursive_duplicate_refs_unit(ctx):
    raw_schema = ctx.openapi.build_schema(
        {
            "/test": {
                "put": {
                    "requestBody": {
                        "content": {"application/json": {"schema": {"$ref": "#/components/schemas/Container"}}}
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
        components={
            "schemas": {
                "Item": {"type": "object", "properties": {"value": {"type": "string", "example": "test-value"}}},
                "Container": {
                    "type": "object",
                    "properties": {
                        "first": {"$ref": "#/components/schemas/Item"},
                        "second": {"$ref": "#/components/schemas/Item"},
                        "third": {"$ref": "#/components/schemas/Item"},
                    },
                },
            }
        },
    )

    schema = schemathesis.openapi.from_dict(raw_schema)
    operation = schema["/test"]["PUT"]

    extracted = list(extract_from_schemas(operation))

    assert len(extracted) == 1
    example = extracted[0]
    assert example.value == {
        "first": {"value": "test-value"},
        "second": {"value": "test-value"},
        "third": {"value": "test-value"},
    }


@pytest.mark.filterwarnings("error")
def test_empty_ref_in_allof(ctx, cli, snapshot_cli, openapi3_base_url):
    # When the schema contains an empty $ref within allOf
    schema_file = ctx.openapi.write_schema(
        {
            "/items": {
                "post": {
                    "requestBody": {
                        "content": {"application/json": {"schema": {"$ref": "#/components/schemas/issue"}}}
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
        components={
            "schemas": {
                "issue": {
                    "properties": {
                        "key": {
                            "$ref": "#/components/schemas/repository",
                        }
                    }
                },
                "object": {},
                "repository": {
                    "allOf": [
                        {"$ref": "#/components/schemas/object"},
                        {
                            "properties": {
                                "key": {"$ref": ""},
                            }
                        },
                    ]
                },
            }
        },
    )

    assert (
        cli.main(
            "run",
            str(schema_file),
            "--phases=examples",
            f"--url={openapi3_base_url}",
        )
        == snapshot_cli
    )


@pytest.mark.filterwarnings("error")
def test_empty_all_of(ctx, cli, snapshot_cli, openapi2_base_url):
    schema_file = ctx.openapi.write_schema(
        {
            "/items": {
                "put": {
                    "parameters": [
                        {
                            "in": "body",
                            "schema": {
                                "allOf": [],
                            },
                        }
                    ]
                }
            }
        },
        version="2.0",
    )

    assert (
        cli.main(
            "run",
            str(schema_file),
            "--phases=examples",
            f"--url={openapi2_base_url}",
        )
        == snapshot_cli
    )


@pytest.mark.filterwarnings("error")
def test_multiple_hops_in_examples(ctx, cli, openapi3_base_url, snapshot_cli):
    schema_file = ctx.openapi.write_schema(
        {
            "/test": {
                "post": {
                    "parameters": [{"$ref": "#/components/parameters/TraceSpan"}],
                    "requestBody": {
                        "content": {"application/json": {"schema": {"$ref": "#/components/schemas/Query"}}}
                    },
                }
            }
        },
        components={
            "parameters": {"TraceSpan": {"example": {}, "in": "header", "name": "Zan", "schema": {}}},
            "schemas": {
                "ArrayExpression": {},
                "Expression": {
                    "oneOf": [
                        {"$ref": "#/components/schemas/ArrayExpression"},
                        {"$ref": "#/components/schemas/MemberExpression"},
                    ]
                },
                "MemberExpression": {
                    "properties": {
                        "key": {"$ref": "#/components/schemas/Expression"},
                    }
                },
                "Query": {"$ref": "#/components/schemas/Expression"},
            },
        },
    )

    assert (
        cli.main(
            "run",
            str(schema_file),
            "--phases=examples",
            "--checks=not_a_server_error",
            f"--url={openapi3_base_url}",
        )
        == snapshot_cli
    )


def test_nested_allof_with_property_refs():
    raw_schema = {
        "swagger": "2.0",
        "info": {"title": "Test", "version": "1.0.0"},
        "paths": {
            "/items": {
                "put": {
                    "parameters": [
                        {
                            "in": "body",
                            "schema": {"$ref": "#/definitions/HostingEnvironment"},
                        }
                    ],
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
        "definitions": {
            "HostingEnvironment": {
                "allOf": [{"$ref": "#/definitions/Resource"}],
                "properties": {"key": {"properties": {"key": {"$ref": "#/definitions/WorkerPool"}}}},
            },
            "Resource": {},
            "WorkerPool": {
                "allOf": [{"$ref": "#/definitions/Resource"}],
                "properties": {"sku": {}},
            },
        },
    }
    schema = schemathesis.openapi.from_dict(raw_schema)
    operation = schema["/items"]["PUT"]
    assert operation.get_strategies_from_examples() == []


def test_allof_with_required_field_should_not_use_incomplete_property_examples(ctx):
    # GH-3333
    raw_schema = ctx.openapi.build_schema(
        {
            "/resource": {
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {"application/json": {"schema": {"$ref": "#/components/schemas/resource"}}},
                    },
                    "responses": {"204": {"description": "Done"}},
                }
            }
        },
        version="3.0.3",
        components={
            "schemas": {
                "resource": {
                    "type": "object",
                    "allOf": [{"$ref": "#/components/schemas/base_resource"}],
                    "properties": {"choice": {"$ref": "#/components/schemas/choice"}},
                    "example": {
                        "name": "example-name",
                        "choice": "option2",
                    },
                },
                "base_resource": {
                    "type": "object",
                    "required": ["name"],
                    "properties": {
                        "name": {
                            "type": "string",
                            "minLength": 1,
                        }
                    },
                    "example": {"name": "example-name"},
                },
                "choice": {
                    "type": "string",
                    "enum": ["option1", "option2", "option3"],
                    "example": "option2",
                },
            }
        },
    )

    schema = schemathesis.openapi.from_dict(raw_schema)
    operation = schema["/resource"]["POST"]

    extracted = list(extract_from_schemas(operation))

    assert len(extracted) == 1
    example = extracted[0]
    assert isinstance(example, BodyExample)

    body_alternative = list(operation.body)[0]
    body_schema = body_alternative.optimized_schema

    validation_error = None
    try:
        jsonschema.validate(example.value, body_schema)
    except jsonschema.ValidationError as e:
        validation_error = e

    assert validation_error is None, (
        f"Example {example.value} is invalid (missing required 'name' from allOf). "
        f"Property-level examples should not be extracted when they violate schema constraints."
    )

    strategies = operation.get_strategies_from_examples()
    for strategy in strategies:
        case = examples.generate_one(strategy)
        try:
            jsonschema.validate(case.body, body_schema)
        except jsonschema.ValidationError as e:
            pytest.fail(f"Generated invalid case {case.body}: {e}")


def test_anyof_with_required_constraints(ctx):
    # See GH-3404
    # When a schema uses `anyOf` with `required` constraints (but no properties inside anyOf branches)
    # to express "either field A or field B must be present", generated examples must satisfy the
    # anyOf constraint by including fields from the first branch
    raw_schema = ctx.openapi.build_schema(
        {
            "/test": {
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {"application/json": {"schema": {"$ref": "#/components/schemas/Item"}}},
                    },
                    "responses": {"200": {"description": "OK"}},
                },
            }
        },
        components={
            "schemas": {
                "Item": {
                    "type": "object",
                    "anyOf": [
                        {"required": ["name"]},
                        {"required": ["id"]},
                    ],
                    "properties": {
                        "type": {"type": "string", "example": "item"},
                        "id": {"type": "string", "format": "uuid"},
                        "name": {"type": "string"},
                    },
                }
            }
        },
    )
    schema = schemathesis.openapi.from_dict(raw_schema)
    operation = schema["/test"]["POST"]

    extracted = [example_to_dict(example) for example in extract_from_schemas(operation)]
    assert extracted == [
        {"media_type": "application/json", "value": {"type": "item", "name": ""}},
    ]

    body_schema = list(operation.body)[0].optimized_schema
    for example in extracted:
        jsonschema.validate(example["value"], body_schema)


def test_non_string_pattern_in_schema(ctx):
    # When a schema contains an invalid non-string `pattern` value (e.g., integer),
    # examples extraction should proceed gracefully without crashing
    raw_schema = ctx.openapi.build_schema(
        {
            "/test": {
                "patch": {
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "key": {
                                            "type": "string",
                                            "pattern": 0,  # Invalid: should be string
                                        }
                                    },
                                }
                            }
                        }
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            }
        }
    )
    schema = schemathesis.openapi.from_dict(raw_schema)
    operation = schema["/test"]["PATCH"]
    # Should not raise TypeError
    assert list(extract_from_schemas(operation)) == []


def test_allof_not_referencing_root_schema(ctx):
    # It used to lead to infinite recursion
    raw_schema = ctx.openapi.build_schema(
        {
            "/first": {
                "put": {
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "$ref": "#/components/schemas/Base",
                                }
                            }
                        }
                    }
                }
            },
            "/second": {
                "get": {
                    "parameters": [
                        {
                            "schema": {
                                "allOf": [
                                    {
                                        "$ref": "#/components/schemas/Bar",
                                    }
                                ]
                            },
                            "name": "key",
                            "in": "query",
                        }
                    ]
                }
            },
        },
        components={
            "schemas": {
                "Base": {
                    "foo": {"$ref": "#/components/schemas/Foo"},
                    "bar": {
                        "$ref": "#/components/schemas/Bar",
                    },
                },
                "Foo": {},
                "Bar": {},
            }
        },
    )
    schema = schemathesis.openapi.from_dict(raw_schema)
    operation = schema["/second"]["GET"]

    assert list(extract_top_level(operation)) == []
