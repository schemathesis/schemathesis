from dataclasses import fields

import pytest

import schemathesis
from schemathesis.specs.openapi.definitions import OPENAPI_30_VALIDATOR, SWAGGER_20_VALIDATOR


def make_user_schema(is_loose=False, **properties):
    properties = {"first_name": {"type": "string"}, "last_name": {"type": "string"}, **properties}
    schema = {
        "additionalProperties": False,
        "properties": properties,
        "required": list(properties),
    }
    if not is_loose:
        schema["type"] = "object"
    return schema


# JSON Schema for a User, which is common for all spec versions.
USER_JSONSCHEMA = make_user_schema(middle_name={"anyOf": [{"type": "string"}, {"type": "null"}]})
USER_JSONSCHEMA_WITH_FILE = make_user_schema(
    middle_name={"anyOf": [{"type": "string"}, {"type": "null"}]}, scan={"type": "string", "format": "binary"}
)
OPEN_API_2_USER_FORM_PARAMETERS = [
    {"in": "formData", "name": "first_name", "required": True, "type": "string"},
    {"in": "formData", "name": "last_name", "required": True, "type": "string"},
    {"in": "formData", "name": "middle_name", "required": True, "type": "string", "x-nullable": True},
]
OPEN_API_2_USER_FORM_WITH_FILE_PARAMETERS = [
    *OPEN_API_2_USER_FORM_PARAMETERS,
    {"in": "formData", "name": "scan", "required": True, "type": "file"},
]
OPEN_API_2_USER_IN_BODY = {
    "in": "body",
    "name": "user",
    "schema": make_user_schema(middle_name={"type": "string", "x-nullable": True}),
}
OPEN_API_3_USER = make_user_schema(middle_name={"type": "string", "nullable": True})
OPEN_API_3_USER_WITH_FILE = make_user_schema(
    middle_name={"type": "string", "nullable": True}, scan={"type": "string", "format": "binary"}
)


@pytest.fixture
def make_openapi_2_schema(ctx):
    def maker(consumes, parameters):
        schema = ctx.openapi.build_schema(
            {
                "/users": {
                    "post": {
                        "summary": "Test operation",
                        "description": "Test",
                        "parameters": parameters,
                        "consumes": consumes,
                        "produces": ["application/json"],
                        "responses": {"200": {"description": "OK"}},
                    }
                }
            },
            version="2.0",
        )
        SWAGGER_20_VALIDATOR.validate(schema)
        return schema

    return maker


@pytest.fixture
def make_openapi_3_schema(ctx):
    def maker(body=None, parameters=None):
        definition = {
            "summary": "Test operation",
            "description": "Test",
            "responses": {"200": {"description": "OK"}},
        }
        if body is not None:
            definition["requestBody"] = body
        if parameters is not None:
            definition["parameters"] = parameters
        schema = ctx.openapi.build_schema({"/users": {"post": definition}})
        OPENAPI_30_VALIDATOR.validate(schema)
        return schema

    return maker


def _compare(left, right):
    assert type(left) is type(right)
    for field in fields(left):
        left_attr = getattr(left, field.name)
        right_attr = getattr(right, field.name)
        if isinstance(left_attr, list):
            assert len(left_attr) == len(right_attr)
            for sub_left, sub_right in zip(left_attr, right_attr, strict=True):
                _compare(sub_left, sub_right)
        else:
            assert left_attr == right_attr


def assert_parameters(schema, expected, json_schemas, location="body"):
    schema = schemathesis.openapi.from_dict(schema)
    operation = schema["/users"]["POST"]
    container = getattr(operation, location)
    _compare(container, expected)
    assert [item.optimized_schema for item in container] == json_schemas
