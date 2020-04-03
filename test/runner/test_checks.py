from typing import Any, Dict

import pytest
import requests

import schemathesis
from schemathesis import models
from schemathesis.checks import (
    content_type_conformance,
    not_a_server_error,
    response_schema_conformance,
    status_code_conformance,
)
from schemathesis.schemas import BaseSchema


def make_case(schema: BaseSchema, definition: Dict[str, Any]) -> models.Case:
    endpoint = models.Endpoint("/path", "GET", definition=definition, schema=schema)
    return models.Case(endpoint)


def make_response(content=b"{}", content_type="application/json") -> requests.Response:
    response = requests.Response()
    response._content = content
    response.status_code = 200
    response.headers["Content-Type"] = content_type
    return response


@pytest.fixture()
def response(request):
    return make_response(content_type=request.param)


@pytest.fixture()
def case(request, swagger_20) -> models.Case:
    return make_case(swagger_20, {"produces": request.param})


@pytest.mark.parametrize(
    "response, case",
    (
        ("application/json", []),
        ("application/json", ["application/json"]),
        ("application/json;charset=utf-8", ["application/json"]),
    ),
    indirect=["response", "case"],
)
def test_content_type_conformance_valid(response, case):
    assert content_type_conformance(response, case) is None


@pytest.mark.parametrize("content_type, is_error", (("application/json", False), ("application/xml", True),))
def test_global_produces_override(content_type, is_error):
    # When "produces" is specified on the schema level and on the operation level
    schema = schemathesis.from_dict(
        {
            "swagger": "2.0",
            "info": {"title": "Sample API", "description": "API description in Markdown.", "version": "1.0.0"},
            "host": "api.example.com",
            "basePath": "/v1",
            "schemes": ["https"],
            "produces": ["application/xml"],
            "paths": {
                "/users": {
                    "get": {
                        "summary": "Returns a list of users.",
                        "description": "Optional extended description in Markdown.",
                        "produces": ["application/json"],
                        "responses": {"200": {"description": "OK"}},
                    }
                }
            },
        }
    )
    endpoint = schema.endpoints["/v1/users"]["get"]
    case = models.Case(endpoint)
    response = make_response(content_type=content_type)
    if not is_error:
        assert content_type_conformance(response, case) is None
    else:
        with pytest.raises(AssertionError):
            content_type_conformance(response, case)


@pytest.mark.parametrize("value", (500, 502))
def test_not_a_server_error(value, swagger_20):
    response = make_response()
    response.status_code = value
    case = make_case(swagger_20, {})
    with pytest.raises(AssertionError) as exc_info:
        not_a_server_error(response, case)
    assert exc_info.type.__name__ == f"StatusCodeError{value}"


@pytest.mark.parametrize("value", (400, 405))
def test_status_code_conformance_valid(value, swagger_20):
    response = make_response()
    response.status_code = value
    case = make_case(swagger_20, {"responses": {"4XX"}})
    status_code_conformance(response, case)


@pytest.mark.parametrize("value", (400, 405))
def test_status_code_conformance_invalid(value, swagger_20):
    response = make_response()
    response.status_code = value
    case = make_case(swagger_20, {"responses": {"5XX"}})
    with pytest.raises(AssertionError) as exc_info:
        status_code_conformance(response, case)
    assert exc_info.type.__name__ == f"StatusCodeError{value}"


@pytest.mark.parametrize(
    "response, case",
    (("plain/text", ["application/json"]), ("plain/text;charset=utf-8", ["application/json"])),
    indirect=["response", "case"],
)
def test_content_type_conformance_invalid(response, case):
    message = (
        f"^Received a response with '{response.headers['Content-Type']}' Content-Type, "
        "but it is not declared in the schema.\n\nDefined content types: application/json$"
    )
    with pytest.raises(AssertionError, match=message) as exc_info:
        content_type_conformance(response, case)
    assert "SchemaValidationError" in exc_info.type.__name__


SUCCESS_SCHEMA = {"type": "object", "properties": {"success": {"type": "boolean"}}, "required": ["success"]}


@pytest.mark.parametrize(
    "content, definition",
    (
        (b'{"success": true}', {}),
        (b'{"success": true}', {"responses": {"200": {"description": "text"}}}),
        (b'{"random": "text"}', {"responses": {"200": {"description": "text"}}}),
        (b'{"success": true}', {"responses": {"200": {"description": "text", "schema": SUCCESS_SCHEMA}}}),
        (b'{"success": true}', {"responses": {"default": {"description": "text", "schema": SUCCESS_SCHEMA}}}),
    ),
)
def test_response_schema_conformance_swagger(swagger_20, content, definition):
    response = make_response(content)
    case = make_case(swagger_20, definition)
    assert response_schema_conformance(response, case) is None


def test_response_schema_conformance_swagger_no_content_header(swagger_20):
    """Regression: response_schema_conformance does not raise KeyError when response does not have a "Content-Type"."""
    response = requests.Response()
    response.status_code = 418
    case = make_case(swagger_20, {})

    result = response_schema_conformance(response, case)

    assert result is None


@pytest.mark.parametrize(
    "content, definition",
    (
        (b'{"success": true}', {}),
        (b'{"success": true}', {"responses": {"200": {"description": "text"}}}),
        (b'{"random": "text"}', {"responses": {"200": {"description": "text"}}}),
        (
            b'{"success": true}',
            {
                "responses": {
                    "200": {"description": "text", "content": {"application/json": {"schema": SUCCESS_SCHEMA}}}
                }
            },
        ),
        (
            b'{"success": true}',
            {
                "responses": {
                    "default": {"description": "text", "content": {"application/json": {"schema": SUCCESS_SCHEMA}}}
                }
            },
        ),
    ),
)
def test_response_schema_conformance_openapi(openapi_30, content, definition):
    response = make_response(content)
    case = make_case(openapi_30, definition)
    assert response_schema_conformance(response, case) is None


@pytest.mark.parametrize(
    "content, definition",
    (
        (b'{"random": "text"}', {"responses": {"200": {"description": "text", "schema": SUCCESS_SCHEMA}}}),
        (b'{"random": "text"}', {"responses": {"default": {"description": "text", "schema": SUCCESS_SCHEMA}}}),
    ),
)
def test_response_schema_conformance_invalid_swagger(swagger_20, content, definition):
    response = make_response(content)
    case = make_case(swagger_20, definition)
    with pytest.raises(AssertionError) as exc_info:
        response_schema_conformance(response, case)
    assert "SchemaValidationError" in exc_info.type.__name__


@pytest.mark.parametrize(
    "content, definition",
    (
        (
            b'{"random": "text"}',
            {
                "responses": {
                    "200": {"description": "text", "content": {"application/json": {"schema": SUCCESS_SCHEMA}}}
                }
            },
        ),
        (
            b'{"random": "text"}',
            {
                "responses": {
                    "default": {"description": "text", "content": {"application/json": {"schema": SUCCESS_SCHEMA}}}
                }
            },
        ),
    ),
)
def test_response_schema_conformance_invalid_openapi(openapi_30, content, definition):
    response = make_response(content)
    case = make_case(openapi_30, definition)
    with pytest.raises(AssertionError):
        response_schema_conformance(response, case)
