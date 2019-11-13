from typing import Any, Dict

import pytest
import requests

from schemathesis import models
from schemathesis.runner import content_type_conformance, response_schema_conformance
from schemathesis.schemas import BaseSchema


def make_test_result(schema: BaseSchema, definition: Dict[str, Any]) -> models.TestResult:
    endpoint = models.Endpoint("/path", "GET", definition=definition)
    return models.TestResult(endpoint, schema)


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
def results(request, swagger_20) -> models.TestResult:
    return make_test_result(swagger_20, {"produces": request.param})


@pytest.mark.parametrize(
    "response, results",
    (
        ("application/json", []),
        ("application/json", ["application/json"]),
        ("application/json;charset=utf-8", ["application/json"]),
    ),
    indirect=["response", "results"],
)
def test_content_type_conformance_valid(response, results):
    assert content_type_conformance(response, results) is None


@pytest.mark.parametrize(
    "response, results",
    (("plain/text", ["application/json"]), ("plain/text;charset=utf-8", ["application/json"])),
    indirect=["response", "results"],
)
def test_content_type_conformance_invalid(response, results):
    with pytest.raises(AssertionError, match="^Content type is not listed in 'produces' field$"):
        content_type_conformance(response, results)


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
def test_response_schema_conformance(swagger_20, content, definition):
    response = make_response(content)
    results = make_test_result(swagger_20, definition)
    assert response_schema_conformance(response, results) is None


@pytest.mark.parametrize(
    "content, definition",
    (
        (b'{"random": "text"}', {"responses": {"200": {"description": "text", "schema": SUCCESS_SCHEMA}}}),
        (b'{"random": "text"}', {"responses": {"default": {"description": "text", "schema": SUCCESS_SCHEMA}}}),
    ),
)
def test_response_schema_conformance_invalid(swagger_20, content, definition):
    response = make_response(content)
    results = make_test_result(swagger_20, definition)
    with pytest.raises(AssertionError):
        response_schema_conformance(response, results)
