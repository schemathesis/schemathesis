from pathlib import Path

import pytest

import schemathesis
from schemathesis.models import Endpoint, EndpointDefinition

from .utils import as_param, get_schema, integer


@pytest.fixture()
def petstore():
    return get_schema("petstore_v2.yaml")


@pytest.mark.parametrize(
    "ref, expected",
    (
        (
            {"$ref": "#/definitions/Category"},
            {
                "properties": {"id": {"format": "int64", "type": "integer"}, "name": {"type": "string"}},
                "type": "object",
                "xml": {"name": "Category"},
            },
        ),
        (
            {"$ref": "#/definitions/Pet"},
            {
                "properties": {
                    "category": {
                        "properties": {"id": {"format": "int64", "type": "integer"}, "name": {"type": "string"}},
                        "type": "object",
                        "xml": {"name": "Category"},
                    },
                    "id": {"format": "int64", "type": "integer"},
                    "name": {"example": "doggie", "type": "string"},
                    "photoUrls": {
                        "items": {"type": "string"},
                        "type": "array",
                        "xml": {"name": "photoUrl", "wrapped": True},
                    },
                    "status": {
                        "description": "pet status in the store",
                        "enum": ["available", "pending", "sold"],
                        "type": "string",
                    },
                    "tags": {
                        "items": {
                            "properties": {"id": {"format": "int64", "type": "integer"}, "name": {"type": "string"}},
                            "type": "object",
                            "xml": {"name": "Tag"},
                        },
                        "type": "array",
                        "xml": {"name": "tag", "wrapped": True},
                    },
                },
                "required": ["name", "photoUrls"],
                "type": "object",
                "xml": {"name": "Pet"},
            },
        ),
    ),
)
def test_resolve(petstore, ref, expected):
    assert petstore.resolver.resolve_all(ref) == expected


def test_recursive_reference(mocker):
    mocker.patch("schemathesis.specs.openapi.references.RECURSION_DEPTH_LIMIT", 1)
    reference = {"$ref": "#/components/schemas/Node"}
    raw_schema = {
        "info": {"description": "Test", "title": "Test", "version": "1.0.0"},
        "openapi": "3.0.2",
        "paths": {
            "/events": {
                "get": {
                    "description": "Test",
                    "responses": {
                        "200": {
                            "content": {"application/json": {"schema": {"$ref": "#/components/schemas/Response"}}},
                            "description": "Test",
                        },
                    },
                    "summary": "Test",
                }
            }
        },
        "components": {
            "schemas": {
                "Response": {
                    "description": "Test",
                    "properties": {"data": reference},
                    "required": ["data"],
                    "type": "object",
                },
                "Node": {
                    "description": "Test",
                    "properties": {"children": {"items": reference, "type": "array"}},
                    "type": "object",
                },
            }
        },
        "servers": [{"url": "/abc"}],
    }
    schema = schemathesis.from_dict(raw_schema)
    assert schema.resolver.resolve_all(reference) == {
        "description": "Test",
        "properties": {
            "children": {
                "items": {
                    "description": "Test",
                    "properties": {"children": {"items": reference, "type": "array"}},
                    "type": "object",
                },
                "type": "array",
            }
        },
        "type": "object",
    }


def test_simple_dereference(testdir):
    # When a given parameter contains a JSON reference
    testdir.make_test(
        """
@schema.parametrize(method="POST")
@settings(max_examples=1)
def test_(request, case):
    request.config.HYPOTHESIS_CASES += 1
    assert case.path == "/v1/users"
    assert case.method == "POST"
    assert_int(case.body)
""",
        paths={
            "/users": {
                "post": {
                    "parameters": [
                        {
                            "schema": {"$ref": "#/definitions/SimpleIntRef"},
                            "in": "body",
                            "name": "object",
                            "required": True,
                        }
                    ],
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
        definitions={"SimpleIntRef": {"type": "integer"}},
    )
    # Then it should be correctly resolved and used in the generated case
    result = testdir.runpytest("-v", "-s")
    result.assert_outcomes(passed=1)
    result.stdout.re_match_lines([r"Hypothesis calls: 1$"])


def test_recursive_dereference(testdir):
    # When a given parameter contains a JSON reference, that reference an object with another reference
    testdir.make_test(
        """
@schema.parametrize(method="POST")
@settings(max_examples=1)
def test_(request, case):
    request.config.HYPOTHESIS_CASES += 1
    assert case.path == "/v1/users"
    assert case.method == "POST"
    assert_int(case.body["id"])
""",
        paths={
            "/users": {
                "post": {
                    "parameters": [
                        {
                            "schema": {"$ref": "#/definitions/ObjectRef"},
                            "in": "body",
                            "name": "object",
                            "required": True,
                        }
                    ],
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
        definitions={
            "ObjectRef": {
                "required": ["id"],
                "type": "object",
                "additionalProperties": False,
                "properties": {"id": {"$ref": "#/definitions/SimpleIntRef"}},
            },
            "SimpleIntRef": {"type": "integer"},
        },
    )
    # Then it should be correctly resolved and used in the generated case
    result = testdir.runpytest("-v", "-s")
    result.assert_outcomes(passed=1)
    result.stdout.re_match_lines([r"Hypothesis calls: 1$"])


def test_inner_dereference(testdir):
    # When a given parameter contains a JSON reference inside a property of an object
    testdir.make_test(
        """
@schema.parametrize(method="POST")
@settings(max_examples=1)
def test_(request, case):
    request.config.HYPOTHESIS_CASES += 1
    assert case.path == "/v1/users"
    assert case.method == "POST"
    assert_int(case.body["id"])
""",
        paths={
            "/users": {
                "post": {
                    "parameters": [
                        {
                            "schema": {
                                "type": "object",
                                "required": ["id"],
                                "properties": {"id": {"$ref": "#/definitions/SimpleIntRef"}},
                            },
                            "in": "body",
                            "name": "object",
                            "required": True,
                        }
                    ],
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
        definitions={"SimpleIntRef": {"type": "integer"}},
    )
    # Then it should be correctly resolved and used in the generated case
    result = testdir.runpytest("-v", "-s")
    result.assert_outcomes(passed=1)
    result.stdout.re_match_lines([r"Hypothesis calls: 1$"])


def test_inner_dereference_with_lists(testdir):
    # When a given parameter contains a JSON reference inside a list in `allOf`
    testdir.make_test(
        """
@schema.parametrize(method="POST")
@settings(max_examples=1)
def test_(request, case):
    request.config.HYPOTHESIS_CASES += 1
    assert case.path == "/v1/users"
    assert case.method == "POST"
    assert_int(case.body["id"]["a"])
    assert_str(case.body["id"]["b"])
""",
        paths={
            "/users": {
                "post": {
                    "parameters": [
                        {
                            "schema": {
                                "type": "object",
                                "required": ["id"],
                                "properties": {
                                    "id": {"allOf": [{"$ref": "#/definitions/A"}, {"$ref": "#/definitions/B"}]}
                                },
                            },
                            "in": "body",
                            "name": "object",
                            "required": True,
                        }
                    ],
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
        definitions={
            "A": {"type": "object", "required": ["a"], "properties": {"a": {"type": "integer"}}},
            "B": {"type": "object", "required": ["b"], "properties": {"b": {"type": "string"}}},
        },
    )
    # Then it should be correctly resolved and used in the generated case
    result = testdir.runpytest("-v", "-s")
    result.assert_outcomes(passed=1)
    result.stdout.re_match_lines([r"Hypothesis calls: 1$"])


def make_nullable_test_data(spec_version):
    field_name = {"openapi": "nullable", "swagger": "x-nullable"}[spec_version]
    return (
        (
            {
                "properties": {
                    "id": {"format": "int64", "type": "integer", field_name: True},
                    "name": {"type": "string"},
                },
                "type": "object",
            },
            {
                "properties": {
                    "id": {"anyOf": [{"format": "int64", "type": "integer"}, {"type": "null"}]},
                    "name": {"type": "string"},
                },
                "type": "object",
            },
        ),
        (
            {
                "parameters": [
                    {"name": "id", "in": "query", "type": "integer", "format": "int64", field_name: True},
                    {"name": "name", "type": "string"},
                ]
            },
            {
                "parameters": [
                    {"name": "id", "in": "query", "format": "int64", "anyOf": [{"type": "integer"}, {"type": "null"}]},
                    {"name": "name", "type": "string"},
                ]
            },
        ),
        (
            {
                "properties": {
                    "id": {"type": "string", "enum": ["a", "b"], field_name: True},
                    "name": {"type": "string"},
                },
                "type": "object",
            },
            {
                "properties": {
                    "id": {"anyOf": [{"type": "string", "enum": ["a", "b"]}, {"type": "null"}]},
                    "name": {"type": "string"},
                },
                "type": "object",
            },
        ),
    )


@pytest.mark.parametrize("nullable, expected", make_nullable_test_data("swagger"))
def test_x_nullable(petstore, nullable, expected):
    assert petstore.resolver.resolve_all(nullable) == expected


@pytest.mark.parametrize("nullable, expected", make_nullable_test_data("openapi"))
def test_nullable(openapi_30, nullable, expected):
    assert openapi_30.resolver.resolve_all(nullable) == expected


def test_nullable_parameters(testdir):
    testdir.make_test(
        """
@schema.parametrize()
@settings(max_examples=1)
def test_(request, case):
    request.config.HYPOTHESIS_CASES += 1
    assert case.path == "/v1/users"
    assert case.method == "GET"
    assert case.query["id"] is None
""",
        **as_param(integer(name="id", required=True, **{"x-nullable": True})),
    )
    # Then it should be correctly resolved and used in the generated case
    result = testdir.runpytest("-v", "-s")
    result.assert_outcomes(passed=1)
    result.stdout.re_match_lines([r"Hypothesis calls: 1$"])


def test_nullable_properties(testdir):
    testdir.make_test(
        """
@schema.parametrize(method="POST")
@settings(max_examples=10)
def test_(request, case):
    assert case.path == "/v1/users"
    assert case.method == "POST"
    if case.body["id"] is None:
        request.config.HYPOTHESIS_CASES += 1
""",
        paths={
            "/users": {
                "post": {
                    "parameters": [
                        {
                            "in": "body",
                            "name": "attributes",
                            "schema": {
                                "type": "object",
                                "properties": {"id": {"type": "integer", "format": "int64", "x-nullable": True}},
                                "required": ["id"],
                            },
                            "required": True,
                        }
                    ],
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
    )
    # Then it should be correctly resolved and used in the generated case
    result = testdir.runpytest("-vv", "-s")
    result.assert_outcomes(passed=1)
    # At least one `None` value should be generated
    hypothesis_calls = int(result.stdout.lines[-1].split(":")[-1].strip())
    assert hypothesis_calls > 0


def test_nullable_ref(testdir):
    testdir.make_test(
        """
@schema.parametrize(method="POST")
@settings(max_examples=1)
def test_(request, case):
    request.config.HYPOTHESIS_CASES += 1
    assert case.path == "/v1/users"
    assert case.method == "POST"
    assert case.body is None
""",
        paths={
            "/users": {
                "post": {
                    "parameters": [
                        {
                            "in": "body",
                            "name": "attributes",
                            "schema": {"$ref": "#/definitions/NullableIntRef"},
                            "required": True,
                        }
                    ],
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
        definitions={"NullableIntRef": {"type": "integer", "x-nullable": True}},
    )
    # Then it should be correctly resolved and used in the generated case
    result = testdir.runpytest("-v", "-s")
    result.assert_outcomes(passed=1)
    result.stdout.re_match_lines([r"Hypothesis calls: 1$"])


def test_path_ref(testdir):
    # When path is specified via `$ref`
    testdir.make_test(
        """
@schema.parametrize()
@settings(max_examples=1)
def test_(request, case):
    request.config.HYPOTHESIS_CASES += 1
    assert case.path == "/v1/users"
    assert isinstance(case.body, str)
""",
        paths={"/users": {"$ref": "#/x-paths/UsersPath"}},
        **{
            # custom extension `x-paths` to be compliant with the spec, otherwise there is no handy place
            # to put the referenced object
            "x-paths": {
                "UsersPath": {
                    "post": {
                        "parameters": [{"schema": {"type": "string"}, "in": "body", "name": "object", "required": True}]
                    }
                }
            }
        },
    )
    # Then it should be correctly resolved and used in the generated case
    result = testdir.runpytest("-v", "-s")
    result.assert_outcomes(passed=1)
    result.stdout.re_match_lines([r"Hypothesis calls: 1$"])


def test_nullable_enum(testdir):
    testdir.make_test(
        """
@schema.parametrize()
@settings(max_examples=1)
def test_(request, case):
    request.config.HYPOTHESIS_CASES += 1
    assert case.path == "/v1/users"
    assert case.method == "GET"
    assert case.query["id"] is None
""",
        **as_param(integer(name="id", required=True, enum=[1, 2], **{"x-nullable": True})),
    )
    # Then it should be correctly resolved and used in the generated case
    result = testdir.runpytest("-v", "-s")
    result.assert_outcomes(passed=1)
    result.stdout.re_match_lines([r"Hypothesis calls: 1$"])


def test_complex_dereference(testdir, complex_schema):
    schema = schemathesis.from_path(complex_schema)
    path = Path(str(testdir))
    assert schema.endpoints["/teapot"]["POST"] == Endpoint(
        path="/teapot",
        method="POST",
        definition=EndpointDefinition(
            {
                "requestBody": {
                    "content": {
                        "application/json": {"schema": {"$ref": "../schemas/teapot/create.yaml#/TeapotCreateRequest"}}
                    },
                    "description": "Test.",
                    "required": True,
                },
                "responses": {"default": {"$ref": "../../common/responses.yaml#/DefaultError"}},
                "summary": "Test",
                "tags": ["ancillaries"],
            },
            {
                "requestBody": {
                    "content": {
                        "application/json": {
                            "schema": {
                                "additionalProperties": False,
                                "description": "Test",
                                "properties": {
                                    "profile": {
                                        "additionalProperties": False,
                                        "description": "Test",
                                        "properties": {"id": {"type": "integer"}},
                                        "required": ["id"],
                                        "type": "object",
                                    },
                                    "username": {"type": "string"},
                                },
                                "required": ["username", "profile"],
                                "type": "object",
                            }
                        }
                    },
                    "description": "Test.",
                    "required": True,
                },
                "responses": {
                    "default": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "additionalProperties": False,
                                    "properties": {
                                        "key": {"anyOf": [{"type": "string"}, {"type": "null"}]},
                                        "referenced": {"anyOf": [{"type": "string"}, {"type": "null"}]},
                                    },
                                    "required": ["key", "referenced"],
                                    "type": "object",
                                }
                            }
                        },
                        "description": "Probably an error",
                    }
                },
                "summary": "Test",
                "tags": ["ancillaries"],
            },
            scope=f"{path.as_uri()}/root/paths/teapot.yaml#/TeapotCreatePath",
        ),
        body={
            "additionalProperties": False,
            "description": "Test",
            "properties": {
                "profile": {
                    "additionalProperties": False,
                    "description": "Test",
                    "properties": {"id": {"type": "integer"}},
                    "required": ["id"],
                    "type": "object",
                },
                "username": {"type": "string"},
            },
            "required": ["username", "profile"],
            "type": "object",
        },
        schema=schema,
    )


def test_remote_reference_to_yaml(swagger_20, schema_url):
    scope, resolved = swagger_20.resolver.resolve(f"{schema_url}#/info/title")
    assert scope.endswith("#/info/title")
    assert resolved == "Example API"
