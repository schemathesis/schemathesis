from typing import Any, Dict

import pytest
import requests

from schemathesis import models
from schemathesis.checks import content_type_conformance, response_schema_conformance, status_code_conformance
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
    with pytest.raises(AssertionError):
        status_code_conformance(response, case)


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
    with pytest.raises(AssertionError, match=message):
        content_type_conformance(response, case)


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
    with pytest.raises(AssertionError):
        response_schema_conformance(response, case)


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
