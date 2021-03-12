import pytest

from schemathesis.exceptions import NUMERIC_STATUS_CODE_ERROR, InvalidSchema, validate_schema
from schemathesis.specs.openapi.definitions import OPENAPI_30, SWAGGER_20


def test_numeric_status_codes():
    schema = {
        "openapi": "3.0.2",
        "info": {"title": "Test", "description": "Test", "version": "0.1.0"},
        "paths": {
            "/users": {
                "get": {
                    "responses": {
                        # Response code should be a string
                        200: {"description": "OK"}
                    }
                }
            }
        },
    }
    with pytest.raises(InvalidSchema, match=NUMERIC_STATUS_CODE_ERROR):
        validate_schema(schema, OPENAPI_30)


def test_root_error():
    schema = {
        "openapi": 123,
        "info": {"title": "Test", "description": "Test", "version": "0.1.0"},
        "paths": {},
    }
    # TODO. Add more detail error message check
    with pytest.raises(InvalidSchema, match="Your schema does not conform to the Open API 3.0 specification!"):
        validate_schema(schema, OPENAPI_30)


def test_error_in_parameters():
    schema = {
        "swagger": "2.0",
        "info": {"title": "Test", "description": "Test", "version": "0.1.0"},
        "paths": {
            "/users": {
                "get": {
                    "parameters": [
                        # Type should be "integer"
                        {"in": "query", "type": "int"}
                    ],
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
    }
    # with pytest.raises(InvalidSchema):
    validate_schema(schema, SWAGGER_20)
