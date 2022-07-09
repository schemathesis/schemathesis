import pytest

import schemathesis
from schemathesis.exceptions import InvalidSchema, SchemaLoadingError
from schemathesis.specs.openapi.parameters import OpenAPI20Body
from schemathesis.specs.openapi.schemas import InliningResolver
from schemathesis.utils import Err, Ok


@pytest.mark.parametrize("base_path", ("/v1", "/v1/"))
def test_base_path_suffix(swagger_20, base_path):
    # When suffix is present or not present in the raw schema's "basePath"
    swagger_20.raw_schema["basePath"] = base_path
    # Then base path ends with "/" anyway in the swagger instance
    assert swagger_20.base_path == "/v1/"
    assert swagger_20.verbose_name == "Swagger 2.0"
    assert swagger_20.spec_version == "2.0"


@pytest.mark.parametrize(
    "server, base_path",
    (
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
    ),
)
def test_open_api_base_path(openapi_30, server, base_path):
    openapi_30.raw_schema["servers"] = server
    assert openapi_30.base_path == base_path


def test_open_api_verbose_name(openapi_30):
    assert openapi_30.verbose_name == "Open API 3.0.0"
    assert openapi_30.spec_version == "3.0.0"


def test_resolver_cache(simple_schema, mocker):
    schema = schemathesis.from_dict(simple_schema)
    spy = mocker.patch("schemathesis.specs.openapi.schemas.InliningResolver", wraps=InliningResolver)
    assert "_resolver" not in schema.__dict__
    assert isinstance(schema.resolver, InliningResolver)
    assert spy.call_count == 1
    # Cached
    assert "_resolver" in schema.__dict__
    assert isinstance(schema.resolver, InliningResolver)
    assert spy.call_count == 1


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
    schema = schemathesis.from_dict(raw_schema)
    assert len(schema["/teapot"]["post"].body) == 1
    body = schema["/teapot"]["post"].body[0]
    assert isinstance(body, OpenAPI20Body)
    assert body.media_type == "application/json"
    assert body.definition == {
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
        "in": "body",
        "name": "user",
        "required": True,
    }


def test_schema_parsing_error(simple_schema):
    # When API operation contains unresolvable reference on its parameter level
    simple_schema["paths"]["/users"]["get"]["parameters"] = [{"$ref": "#/definitions/SimpleIntRef"}]
    simple_schema["paths"]["/foo"] = {"post": RESPONSES}
    # Then it is not detectable during the schema validation
    schema = schemathesis.from_dict(simple_schema)
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


@pytest.mark.parametrize("validate_schema, expected_exception", ((False, InvalidSchema), (True, SchemaLoadingError)))
def test_not_recoverable_schema_error(simple_schema, validate_schema, expected_exception):
    # When there is an error in the API schema that leads to inability to generate any tests
    del simple_schema["paths"]
    # Then it is an explicit exception either during schema loading or processing API operations
    with pytest.raises(expected_exception):
        schema = schemathesis.from_dict(simple_schema, validate_schema=validate_schema)
        list(schema.get_all_operations())


def test_schema_error_on_path(simple_schema):
    # When there is an error that affects only a subset of paths
    simple_schema["paths"] = {None: "", "/foo": {"post": RESPONSES}}
    # Then it should be rejected during loading if schema validation is enabled
    with pytest.raises(SchemaLoadingError):
        schemathesis.from_dict(simple_schema, validate_schema=True)
    # And should produce an `Err` instance on operation parsing
    schema = schemathesis.from_dict(simple_schema, validate_schema=False)
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
    "operation_id, path, method",
    (
        ("getFoo", "/foo", "GET"),
        ("postBar", "/bar", "POST"),
    ),
)
def test_get_operation_by_id(operation_id, path, method):
    schema = schemathesis.from_dict(SCHEMA)
    operation = schema.get_operation_by_id(operation_id)
    assert operation.path == path
    assert operation.method.upper() == method
