import jsonschema
import pytest

from schemathesis.specs.openapi.definitions import OPENAPI_30, SWAGGER_20
from schemathesis.specs.openapi.parameters import OpenAPI20Body, OpenAPI20Parameter, OpenAPI30Parameter, OpenAPIBody


@pytest.mark.parametrize(
    "cls, schema, expected",
    (
        (
            OpenAPI20Parameter,
            {"name": "id", "in": "query", "required": True, "type": "string", "x-example": "test"},
            "test",
        ),
        (
            OpenAPI20Body,
            {"name": "id", "in": "body", "required": True, "schema": {"type": "string"}, "x-example": "test"},
            "test",
        ),
        (
            OpenAPI20Body,
            {"name": "id", "in": "body", "required": True, "schema": {"type": "string", "example": "test"}},
            "test",
        ),
        (
            OpenAPI30Parameter,
            {"name": "id", "in": "query", "required": True, "schema": {"type": "string"}, "example": "test"},
            "test",
        ),
        (
            OpenAPI30Parameter,
            {"name": "id", "in": "query", "required": True, "schema": {"type": "string", "example": "test"}},
            "test",
        ),
        # Parameters precedence
        (
            OpenAPI20Body,
            {
                "name": "id",
                "in": "body",
                "required": True,
                "x-example": "foo",  # This vendor extension is still more important that the one inside "schema"
                "schema": {"type": "string", "example": "bar"},
            },
            "foo",
        ),
        (
            OpenAPI30Parameter,
            {
                "name": "id",
                "in": "query",
                "required": True,
                "example": "foo",
                "schema": {"type": "string", "example": "bar"},
            },
            "foo",
        ),
    ),
)
def test_examples(request, cls, schema, expected):
    # Check that we have a valid schema
    if issubclass(cls, OpenAPI20Parameter):
        template = request.getfixturevalue("empty_open_api_2_schema")
        template["paths"]["/users"] = {"get": {"parameters": [schema], "responses": {"200": {"description": "OK"}}}}
        jsonschema.validate(template, SWAGGER_20)
    else:
        template = request.getfixturevalue("empty_open_api_3_schema")
        if issubclass(cls, OpenAPIBody):
            template["paths"]["/users"] = {
                "get": {
                    "requestBody": {"content": {"text/plain": {"schema": schema}}},
                    "responses": {"200": {"description": "OK"}},
                }
            }
        else:
            template["paths"]["/users"] = {"get": {"parameters": [schema], "responses": {"200": {"description": "OK"}}}}
        jsonschema.validate(template, OPENAPI_30)
    kwargs = {"media_type": "text/plain"} if issubclass(cls, OpenAPIBody) else {}

    assert cls(schema, **kwargs).example == expected
