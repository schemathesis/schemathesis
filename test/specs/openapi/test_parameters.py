import pytest

from schemathesis.specs.openapi.definitions import OPENAPI_30_VALIDATOR, SWAGGER_20_VALIDATOR
from schemathesis.specs.openapi.parameters import (
    OpenAPI20Body,
    OpenAPI20CompositeBody,
    OpenAPI20Parameter,
    OpenAPI30Body,
    OpenAPI30Parameter,
    OpenAPIBody,
)


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
        SWAGGER_20_VALIDATOR.validate(template)
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
        OPENAPI_30_VALIDATOR.validate(template)
    kwargs = {"media_type": "text/plain"} if issubclass(cls, OpenAPIBody) else {}

    assert cls(schema, **kwargs).example == expected


DESCRIPTION = "Foo"


@pytest.mark.parametrize(
    "cls, kwargs, expected",
    (
        (
            OpenAPI20Parameter,
            {"definition": {"description": DESCRIPTION, "in": "query", "name": "foo", "type": "string"}},
            DESCRIPTION,
        ),
        (
            OpenAPI30Parameter,
            {"definition": {"description": DESCRIPTION, "in": "query", "name": "foo", "schema": {"type": "string"}}},
            DESCRIPTION,
        ),
        (
            OpenAPI20Body,
            {
                "definition": {"description": DESCRIPTION, "in": "body", "name": "foo", "schema": {"type": "string"}},
                "media_type": "application/json",
            },
            DESCRIPTION,
        ),
        (
            OpenAPI30Body,
            {
                "definition": {"schema": {"type": "string"}},
                "description": DESCRIPTION,
                "media_type": "application/json",
            },
            DESCRIPTION,
        ),
        (
            OpenAPI20CompositeBody,
            {
                "definition": [OpenAPI20Parameter({"in": "formData", "name": "foo", "type": "string"})],
                "media_type": "application/x-www-form-urlencoded",
            },
            None,
        ),
    ),
)
def test_description(cls, kwargs, expected):
    assert cls(**kwargs).description == expected
