import pytest

from schemathesis.core.errors import InvalidSchema
from schemathesis.schemas import APIOperation


def test_formatted_path(swagger_20):
    operation = APIOperation(
        "/users/{name}",
        "GET",
        {},
        swagger_20,
        responses=swagger_20._parse_responses({}, ""),
        security=swagger_20._parse_security({}),
    )
    case = operation.Case(path_parameters={"name": "test"})
    assert case.formatted_path == "/users/test"


def test_formatted_path_missing_parameter(ctx):
    schema = ctx.openapi.load_schema(
        {
            "/users/{name}": {
                "get": {
                    "parameters": [{"name": "name", "in": "path", "required": True, "schema": {"type": "string"}}],
                    "responses": {"200": {"description": "OK"}},
                }
            }
        }
    )
    case = schema["/users/{name}"]["GET"].Case(path_parameters={})
    with pytest.raises(InvalidSchema, match="^Path parameter 'name' is not defined$"):
        assert case.formatted_path
