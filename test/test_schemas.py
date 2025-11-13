import platform

import pytest
import requests

import schemathesis
from schemathesis.core.errors import InvalidSchema, LoaderError, OperationNotFound
from schemathesis.core.failures import Failure
from schemathesis.core.result import Err, Ok
from schemathesis.core.transport import Response as HTTPResponse
from schemathesis.openapi.checks import JsonSchemaError
from schemathesis.specs.openapi._operation_lookup import OperationLookup


@pytest.mark.parametrize("base_path", ["/v1", "/v1/"])
def test_base_path_suffix(swagger_20, base_path):
    # When suffix is present or not present in the raw schema's "basePath"
    swagger_20.raw_schema["basePath"] = base_path
    # Then base path ends with "/" anyway in the swagger instance
    assert swagger_20.base_path == "/v1/"
    assert swagger_20.specification.name == "Open API 2.0"
    assert swagger_20.specification.version == "2.0"


@pytest.mark.parametrize(
    ("server", "base_path"),
    [
        (
            [
                {
                    "url": "https://api.example.com/{basePath}/foo/{bar}",
                    "variables": {"basePath": {"default": "v1"}, "bar": {"default": "bar"}},
                }
            ],
            "/v1/foo/bar/",
        ),
        ([], "/"),
    ],
)
def test_open_api_base_path(openapi_30, server, base_path):
    openapi_30.raw_schema["servers"] = server
    assert openapi_30.base_path == base_path


def test_open_api_specification(openapi_30):
    assert openapi_30.specification.name == "Open API 3.0.0"
    assert openapi_30.specification.version == "3.0.0"


def test_resolving_multiple_files():
    raw_schema = {
        "swagger": "2.0",
        "info": {"title": "Example API", "description": "An API to test Schemathesis", "version": "1.0.0"},
        "host": "127.0.0.1:8888",
        "basePath": "/api",
        "schemes": ["http"],
        "produces": ["application/json"],
        "paths": {
            "/teapot": {
                "post": {
                    "parameters": [
                        {
                            "schema": {"$ref": "test/data/petstore_v2.yaml#/definitions/User"},
                            "in": "body",
                            "name": "user",
                            "required": True,
                        }
                    ],
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
    }
    schema = schemathesis.openapi.from_dict(raw_schema)
    assert len(schema["/teapot"]["post"].body) == 1
    body = schema["/teapot"]["post"].body[0]
    assert body.media_type == "application/json"
    assert body.definition == {
        "in": "body",
        "name": "user",
        "required": True,
        "schema": {
            "type": "object",
            "properties": {
                "id": {"type": "integer", "format": "int64"},
                "username": {"type": "string"},
                "firstName": {"type": "string"},
                "lastName": {"type": "string"},
                "email": {"type": "string"},
                "password": {"type": "string"},
                "phone": {"type": "string"},
                "userStatus": {"type": "integer", "format": "int32", "description": "User Status"},
            },
            "xml": {"name": "User"},
        },
    }


def test_resolving_relative_files():
    schema = schemathesis.openapi.from_path("test/data/relative_files/main.yaml")
    operations = list(schema.get_all_operations())
    errors = [op.err() for op in operations if isinstance(op, Err)]
    assert not errors


def test_schema_parsing_error(simple_schema):
    # When API operation contains unresolvable reference on its parameter level
    simple_schema["paths"]["/users"]["get"]["parameters"] = [{"$ref": "#/definitions/SimpleIntRef"}]
    simple_schema["paths"]["/foo"] = {"post": RESPONSES}
    # Then it is not detectable during the schema validation
    schema = schemathesis.openapi.from_dict(simple_schema)
    # And is represented as an `Err` instance during operations parsing
    operations = list(schema.get_all_operations())
    assert len(operations) == 2
    errors = [op.err() for op in operations if isinstance(op, Err)]
    assert len(errors) == 1
    # And `path` and `method` are known for this error
    assert errors[0].path == "/users"
    assert errors[0].method == "get"
    # And all valid operations should be parsed as `Ok`
    oks = [op.ok() for op in operations if isinstance(op, Ok)]
    assert len(oks) == 1
    assert oks[0].path == "/foo"
    assert oks[0].method == "post"


def test_not_recoverable_schema_error(simple_schema):
    # When there is an error in the API schema that leads to inability to generate any tests
    del simple_schema["paths"]
    # Then it is an explicit exception during processing API operations
    with pytest.raises(InvalidSchema):
        schema = schemathesis.openapi.from_dict(simple_schema)
        list(schema.get_all_operations())


def test_invalid_parameter_schema_type(ctx):
    # When a parameter's schema is not a dict or bool (e.g., a list)
    raw_schema = ctx.openapi.build_schema(
        {
            "/users": {
                "get": {
                    "parameters": [{"in": "query", "name": "filter", "schema": ["invalid", "list"]}],
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
    )
    schema = schemathesis.openapi.from_dict(raw_schema)
    operation = schema["/users"]["get"]
    # Then getting this parameter's schema should raise InvalidSchema
    with pytest.raises(InvalidSchema, match="Can not generate data for query parameter"):
        _ = operation.query[0].optimized_schema


def test_no_paths_on_openapi_3_1():
    raw_schema = {
        "openapi": "3.1.0",
        "info": {"title": "Test", "version": "0.1.0"},
    }
    schema = schemathesis.openapi.from_dict(raw_schema)
    assert list(schema.get_all_operations()) == []


def test_operation_lookup_without_paths_on_openapi_3_1():
    raw_schema = {
        "openapi": "3.1.0",
        "info": {"title": "Test", "version": "0.1.0"},
        # No `paths`, but webhook-only schemas are valid in 3.1+
        "webhooks": {
            "UserCreated": {
                "post": {
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {"type": "object", "properties": {"id": {"type": "integer"}}}
                            }
                        }
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
    }
    schema = schemathesis.openapi.from_dict(raw_schema)
    with pytest.raises(OperationNotFound, match="/users"):
        schema["/users"]


def test_schema_error_on_path(simple_schema):
    # When there is an error that affects only a subset of paths
    simple_schema["paths"] = {None: "", "/foo": {"post": RESPONSES}}
    # Then it should produce an `Err` instance on operation parsing
    schema = schemathesis.openapi.from_dict(simple_schema)
    operations = list(schema.get_all_operations())
    assert len(operations) == 2
    errors = [op for op in operations if isinstance(op, Err)]
    assert len(errors) == 1
    assert errors[0].err().path is None
    assert errors[0].err().method is None
    # And all valid operations should be parsed as `Ok`
    oks = [op for op in operations if isinstance(op, Ok)]
    assert len(oks) == 1
    assert oks[0].ok().path == "/foo"
    assert oks[0].ok().method == "post"


def test_response_validation_selects_media_type(ctx):
    raw_schema = ctx.openapi.build_schema(
        {
            "/value": {
                "get": {
                    "responses": {
                        "200": {
                            "description": "Success",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "required": ["id"],
                                        "properties": {"id": {"type": "integer"}},
                                    }
                                },
                                "application/problem+json": {
                                    "schema": {
                                        "type": "object",
                                        "required": ["title"],
                                        "properties": {"title": {"type": "string"}},
                                    }
                                },
                            },
                        }
                    }
                }
            }
        }
    )
    schema = schemathesis.openapi.from_dict(raw_schema)
    operation = schema["/value"]["GET"]
    request = requests.Request("GET", "http://example.com/value").prepare()

    def make_response(content_type: str, payload: str) -> HTTPResponse:
        return HTTPResponse(
            status_code=200,
            headers={"content-type": [content_type]},
            content=payload.encode(),
            request=request,
            elapsed=0.0,
            verify=False,
        )

    # application/json schema requires "id"
    schema.validate_response(operation, make_response("application/json", '{"id": 1}'))

    # application/problem+json schema requires "title"
    with pytest.raises(JsonSchemaError):
        schema.validate_response(operation, make_response("application/problem+json", '{"id": 1}'))
    schema.validate_response(operation, make_response("application/problem+json", '{"title": "Oops"}'))


@pytest.mark.parametrize(
    ("content_type", "payload", "expect_error"),
    [
        # Exact match to application/json (first schema - requires "id")
        ("application/json", '{"id": 1}', None),
        ("application/json; charset=utf-8", '{"id": 1}', None),
        ("application/json;charset=utf-8", '{"id": 1}', None),
        ("application/json", '{"id": 42}', None),
        ("application/json", '{"wrong": "data"}', JsonSchemaError),
        # Wildcard match to application/* (second schema - requires "data" as string)
        ("application/xml", '{"data": "test"}', None),
        # Exact match to application/vnd.api+json (third schema - requires "data" as array)
        ("application/vnd.api+json", '{"data": []}', None),
        ("application/vnd.api+json", '{"id": 42}', JsonSchemaError),
        # Unmatched content types fall back to first schema (application/json)
        # Note: Non-JSON content types may skip validation if no deserializer exists
        ("text/plain", '{"id": 1}', None),
        ("image/png", '{"id": 1}', None),
        ("text/html", '{"id": 1}', None),
        # Malformed Content-Type causes deserialization error
        ("invalid", '{"id": 1}', Failure),
        ("application", '{"id": 1}', Failure),
    ],
)
def test_response_validation_media_type_edge_cases(ctx, content_type, payload, expect_error):
    # Schema with multiple media types and different validation rules
    raw_schema = ctx.openapi.build_schema(
        {
            "/value": {
                "get": {
                    "responses": {
                        "200": {
                            "description": "Success",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "required": ["id"],
                                        "properties": {"id": {"type": "integer"}},
                                    }
                                },
                                "application/*": {
                                    "schema": {
                                        "type": "object",
                                        "required": ["data"],
                                        "properties": {"data": {"type": "string"}},
                                    }
                                },
                                "application/vnd.api+json": {
                                    "schema": {
                                        "type": "object",
                                        "required": ["data"],
                                        "properties": {"data": {"type": "array"}},
                                    }
                                },
                            },
                        }
                    }
                }
            }
        }
    )
    schema = schemathesis.openapi.from_dict(raw_schema)
    operation = schema["/value"]["GET"]
    request = requests.Request("GET", "http://example.com/value").prepare()

    headers = {"content-type": [content_type]}
    response = HTTPResponse(
        status_code=200,
        headers=headers,
        content=payload.encode(),
        request=request,
        elapsed=0.0,
        verify=False,
    )

    if expect_error:
        with pytest.raises(expect_error):
            schema.validate_response(operation, response)
    else:
        schema.validate_response(operation, response)


RESPONSES = {"responses": {"200": {"description": "OK"}}}
SCHEMA = {
    "openapi": "3.0.2",
    "info": {"title": "Test", "description": "Test", "version": "0.1.0"},
    "paths": {
        "/foo": {"get": {"operationId": "getFoo", **RESPONSES}, "post": {"operationId": "postFoo", **RESPONSES}},
        "/bar": {"get": {"operationId": "getBar", **RESPONSES}, "post": {"operationId": "postBar", **RESPONSES}},
    },
}


@pytest.mark.parametrize(
    ("operation_id", "reference", "path", "method"),
    [
        ("getFoo", "#/paths/~1foo/get", "/foo", "GET"),
        ("postBar", "#/paths/~1bar/post", "/bar", "POST"),
    ],
)
def test_get_operation(operation_id, reference, path, method):
    schema = schemathesis.openapi.from_dict(SCHEMA)
    for getter, key in ((schema.find_operation_by_id, operation_id), (schema.find_operation_by_reference, reference)):
        operation = getter(key)
        assert operation.path == path
        assert operation.method.upper() == method


def test_operation_lookup_cache_built_once(monkeypatch):
    schema = schemathesis.openapi.from_dict(SCHEMA)
    calls = 0
    original = OperationLookup._build_tables

    def tracking(self: OperationLookup) -> None:
        nonlocal calls
        calls += 1
        return original(self)

    monkeypatch.setattr(OperationLookup, "_build_tables", tracking)

    schema.find_operation_by_id("getFoo")
    schema.find_operation_by_reference("#/paths/~1foo/get")

    assert calls == 1


def test_find_operation_by_id_in_referenced_path(ctx):
    # When a path entry is behind a reference
    # it should be resolved correctly
    schema = ctx.openapi.build_schema(
        {"/foo": {"$ref": "#/components/x-paths/Path"}},
        components={
            "x-paths": {
                "Path": {"get": {"operationId": "getFoo", **RESPONSES}},
            },
        },
    )
    schema = schemathesis.openapi.from_dict(schema)
    operation = schema.find_operation_by_id("getFoo")
    assert operation.path == "/foo"
    assert operation.method.upper() == "GET"


def test_find_operation_by_id_in_referenced_path_shared_parameters(ctx):
    # When a path entry is behind a reference
    # and it shares parameters with the parent path
    # it should be resolved correctly
    # and the parameters should be merged
    parameter = {"name": "foo", "in": "query", "schema": {"type": "string"}}
    schema = ctx.openapi.build_schema(
        {"/foo": {"$ref": "#/components/x-paths/Path"}},
        components={
            "x-paths": {
                "Path": {
                    "get": {"operationId": "getFoo", **RESPONSES},
                    "parameters": [parameter],
                }
            },
        },
    )
    schema = schemathesis.openapi.from_dict(schema)
    operation = schema.find_operation_by_id("getFoo")
    assert operation.path == "/foo"
    assert operation.method.upper() == "GET"


def test_find_operation_by_id_no_paths_on_openapi_3_1():
    raw_schema = {
        "openapi": "3.1.0",
        "info": {"title": "Test", "version": "0.1.0"},
    }
    schema = schemathesis.openapi.from_dict(raw_schema)
    with pytest.raises(OperationNotFound):
        schema.find_operation_by_id("getFoo")


@pytest.mark.skipif(platform.python_implementation() == "PyPy", reason="PyPy behaves differently")
def test_ssl_error(server):
    with pytest.raises(LoaderError) as exc:
        schemathesis.openapi.from_url(f"https://127.0.0.1:{server['port']}")
    assert exc.value.message == "SSL verification problem"
    assert exc.value.extras[0].startswith(
        (
            "[SSL: WRONG_VERSION_NUMBER] wrong version number",
            "[SSL] record layer failure",
            "[SSL: RECORD_LAYER_FAILURE] record layer failure",
        )
    )
