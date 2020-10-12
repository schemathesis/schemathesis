from copy import deepcopy
from test.utils import as_param

import pytest
from jsonschema import ValidationError

import schemathesis
from schemathesis.exceptions import InvalidSchema
from schemathesis.specs.openapi.schemas import ConvertingResolver


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
    spy = mocker.patch("schemathesis.specs.openapi.schemas.ConvertingResolver", wraps=ConvertingResolver)
    assert "_resolver" not in schema.__dict__
    assert isinstance(schema.resolver, ConvertingResolver)
    assert spy.call_count == 1
    # Cached
    assert "_resolver" in schema.__dict__
    assert isinstance(schema.resolver, ConvertingResolver)
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
    assert schema["/teapot"]["post"].body == {
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
    }


@pytest.mark.parametrize("validate_schema, expected_exception", ((False, InvalidSchema), (True, ValidationError)))
@pytest.mark.parametrize("error_type", ("KeyError", "AttributeError", "RefResolutionError"))
def test_schema_parsing_error(simple_schema, error_type, validate_schema, expected_exception):
    raw_schema = deepcopy(simple_schema)
    if error_type == "KeyError":
        raw_schema.pop("paths")
    elif error_type == "AttributeError":
        raw_schema["paths"] = {None: ""}
    elif error_type == "RefResolutionError":
        raw_schema["paths"]["/users"]["get"]["parameters"] = [as_param({"$ref": "#/definitions/SimpleIntRef"})]
    with pytest.raises(expected_exception):
        schema = schemathesis.from_dict(raw_schema, validate_schema=validate_schema)
        list(schema.get_all_endpoints())


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
def test_get_endpoint_by_operation_id(operation_id, path, method):
    schema = schemathesis.from_dict(SCHEMA)
    endpoint = schema.get_endpoint_by_operation_id(operation_id)
    assert endpoint.path == path
    assert endpoint.method.upper() == method
