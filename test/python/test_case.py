import pytest

from schemathesis.core.errors import InvalidSchema


def test_formatted_path(ctx):
    operation = ctx.openapi.load_operation("GET /users/{name}", {"responses": {"200": {"description": "OK"}}})
    case = operation.Case(path_parameters={"name": "test"})
    assert case.formatted_path == "/users/test"


def test_formatted_path_missing_parameter(ctx):
    operation = ctx.openapi.load_operation(
        "GET /users/{name}",
        {
            "parameters": [{"name": "name", "in": "path", "required": True, "schema": {"type": "string"}}],
            "responses": {"200": {"description": "OK"}},
        },
    )
    case = operation.Case(path_parameters={})
    with pytest.raises(InvalidSchema, match="^Path parameter 'name' is not defined$"):
        assert case.formatted_path
