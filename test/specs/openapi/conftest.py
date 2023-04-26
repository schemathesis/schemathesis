from dataclasses import fields

import pytest

import schemathesis
from schemathesis.specs.openapi.definitions import OPENAPI_30_VALIDATOR, SWAGGER_20_VALIDATOR
from schemathesis.utils import fast_deepcopy


def make_object_schema(is_loose=False, **properties):
    schema = {
        "additionalProperties": False,
        "properties": properties,
        "required": list(properties),
    }
    if not is_loose:
        schema["type"] = "object"
    return schema


def make_user_schema(**kwargs):
    return make_object_schema(first_name={"type": "string"}, last_name={"type": "string"}, **kwargs)


@pytest.fixture(name="make_user_schema")
def _make_user_schema():
    return make_user_schema


@pytest.fixture
def user_jsonschema():
    """JSON Schema for a User, which is common for all spec versions."""
    return make_user_schema(
        middle_name={"anyOf": [{"type": "string"}, {"type": "null"}]},
    )


@pytest.fixture
def user_jsonschema_with_file():
    """JSON Schema for a User with a file upload, which is common for all spec versions."""
    return make_user_schema(
        middle_name={"anyOf": [{"type": "string"}, {"type": "null"}]}, scan={"type": "string", "format": "binary"}
    )


@pytest.fixture
def open_api_2_user():
    return make_user_schema(
        middle_name={"type": "string", "x-nullable": True},
    )


@pytest.fixture
def open_api_2_user_form_parameters():
    return [
        {"in": "formData", "name": "first_name", "required": True, "type": "string"},
        {"in": "formData", "name": "last_name", "required": True, "type": "string"},
        {
            "in": "formData",
            "name": "middle_name",
            "required": True,
            "type": "string",
            "x-nullable": True,
        },
    ]


@pytest.fixture
def open_api_2_user_in_body(open_api_2_user):
    return {
        "in": "body",
        "name": "user",
        "schema": open_api_2_user,
    }


@pytest.fixture
def open_api_2_user_form_with_file_parameters(open_api_2_user_form_parameters):
    return open_api_2_user_form_parameters + [{"in": "formData", "name": "scan", "required": True, "type": "file"}]


@pytest.fixture
def make_openapi_2_schema(empty_open_api_2_schema):
    def maker(consumes, parameters):
        schema = fast_deepcopy(empty_open_api_2_schema)
        schema["paths"]["/users"] = {
            "post": {
                "summary": "Test operation",
                "description": "Test",
                "parameters": parameters,
                "consumes": consumes,
                "produces": ["application/json"],
                "responses": {"200": {"description": "OK"}},
            }
        }
        SWAGGER_20_VALIDATOR.validate(schema)
        return schema

    return maker


@pytest.fixture
def open_api_3_user():
    return make_user_schema(
        middle_name={"type": "string", "nullable": True},
    )


@pytest.fixture
def open_api_3_user_with_file():
    return make_user_schema(
        middle_name={"type": "string", "nullable": True}, scan={"type": "string", "format": "binary"}
    )


@pytest.fixture
def make_openapi_3_schema(empty_open_api_3_schema):
    def maker(body=None, parameters=None):
        schema = fast_deepcopy(empty_open_api_3_schema)
        definition = {
            "summary": "Test operation",
            "description": "Test",
            "responses": {"200": {"description": "OK"}},
        }
        if body is not None:
            definition["requestBody"] = body
        if parameters is not None:
            definition["parameters"] = parameters
        schema["paths"]["/users"] = {"post": definition}
        OPENAPI_30_VALIDATOR.validate(schema)
        return schema

    return maker


@pytest.fixture
def assert_parameters():
    def _compare(left, right):
        assert type(left) == type(right)
        for field in fields(left):
            left_attr = getattr(left, field.name)
            right_attr = getattr(right, field.name)
            if isinstance(left_attr, list):
                assert len(left_attr) == len(right_attr)
                for sub_left, sub_right in zip(left_attr, right_attr):
                    _compare(sub_left, sub_right)
            else:
                assert left_attr == right_attr

    def check(schema, expected, json_schemas, location="body"):
        schema = schemathesis.from_dict(schema)
        operation = schema["/users"]["POST"]
        container = getattr(operation, location)
        _compare(container, expected)
        assert [item.as_json_schema(operation) for item in container] == json_schemas

    return check
