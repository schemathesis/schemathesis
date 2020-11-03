import json
from typing import Any, Dict, Optional

import pytest
import requests
from hypothesis import given, settings

import schemathesis
from schemathesis import models
from schemathesis.checks import (
    content_type_conformance,
    not_a_server_error,
    response_schema_conformance,
    status_code_conformance,
)
from schemathesis.exceptions import CheckFailed, InvalidSchema
from schemathesis.models import EndpointDefinition
from schemathesis.schemas import BaseSchema


def make_case(schema: BaseSchema, definition: Dict[str, Any]) -> models.Case:
    endpoint = models.Endpoint(
        "/path", "GET", definition=EndpointDefinition(definition, definition, None, []), schema=schema
    )
    return models.Case(endpoint)


def make_response(content=b"{}", content_type: Optional[str] = "application/json") -> requests.Response:
    response = requests.Response()
    response._content = content
    response.status_code = 200
    if content_type:
        response.headers["Content-Type"] = content_type
    return response


@pytest.fixture()
def spec(request):
    param = getattr(request, "param", None)
    if param == "swagger":
        return request.getfixturevalue("swagger_20")
    elif param == "openapi":
        return request.getfixturevalue("openapi_30")
    return request.getfixturevalue("swagger_20")


@pytest.fixture()
def response(request):
    return make_response(content_type=request.param)


@pytest.fixture()
def case(request, spec) -> models.Case:
    if "swagger" in spec.raw_schema:
        data = {"produces": getattr(request, "param", ["application/json"])}
    else:
        data = {
            "responses": {
                "200": {
                    "content": {
                        # There should be a content type in OAS3. But in test below it is omitted
                        # For simplicity a default value is implemented here
                        key: {"schema": {"type": "string"}}
                        for key in request.param or ["application/json"]
                    }
                }
            }
        }
    return make_case(spec, data)


@pytest.mark.parametrize("spec", ("swagger", "openapi"), indirect=["spec"])
@pytest.mark.parametrize(
    "response, case",
    (
        ("application/json", []),
        ("application/json", ["application/json"]),
        ("application/json;charset=utf-8", ["application/json"]),
    ),
    indirect=["response", "case"],
)
def test_content_type_conformance_valid(spec, response, case):
    assert content_type_conformance(response, case) is None


@pytest.mark.parametrize(
    "raw_schema",
    (
        {
            "swagger": "2.0",
            "info": {"title": "Sample API", "description": "API description in Markdown.", "version": "1.0.0"},
            "host": "api.example.com",
            "basePath": "/",
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
        },
        {
            "openapi": "3.0.2",
            "info": {"title": "Test", "description": "Test", "version": "0.1.0"},
            "paths": {
                "/users": {
                    "get": {
                        "responses": {
                            "200": {
                                "description": "OK",
                                "content": {"application/json": {"schema": {"type": "object"}}},
                            }
                        },
                    }
                }
            },
        },
    ),
)
@pytest.mark.parametrize("content_type, is_error", (("application/json", False), ("application/xml", True)))
def test_content_type_conformance_integration(raw_schema, content_type, is_error):
    assert_content_type_conformance(raw_schema, content_type, is_error)


@pytest.mark.parametrize(
    "content_type, is_error",
    (
        ("application/json", False),
        ("application/xml", True),
    ),
)
def test_content_type_conformance_default_response(content_type, is_error):
    raw_schema = {
        "openapi": "3.0.2",
        "info": {"title": "Test", "description": "Test", "version": "0.1.0"},
        "paths": {
            "/users": {
                "get": {
                    "responses": {"default": {"description": "OK", "content": {"application/json": {"schema": {}}}}},
                }
            }
        },
    }
    assert_content_type_conformance(raw_schema, content_type, is_error)


def test_content_type_conformance_another_status_code():
    # When the schema only defines a response for status code 400
    raw_schema = {
        "openapi": "3.0.2",
        "info": {"title": "Test", "description": "Test", "version": "0.1.0"},
        "paths": {
            "/users": {
                "get": {
                    "responses": {"400": {"description": "Error", "content": {"application/json": {"schema": {}}}}},
                }
            }
        },
    }
    # And the response has another status code
    # Then the content type should be ignored, since the schema does not contain relevant definitions
    assert_content_type_conformance(raw_schema, "application/xml", False)


def assert_content_type_conformance(raw_schema, content_type, is_error):
    schema = schemathesis.from_dict(raw_schema)
    endpoint = schema.endpoints["/users"]["get"]
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


@pytest.mark.parametrize("spec", ("swagger", "openapi"), indirect=["spec"])
@pytest.mark.parametrize(
    "response, case",
    (("plain/text", ["application/json"]), ("plain/text;charset=utf-8", ["application/json"])),
    indirect=["response", "case"],
)
def test_content_type_conformance_invalid(spec, response, case):
    message = (
        f"^Received a response with '{response.headers['Content-Type']}' Content-Type, "
        "but it is not declared in the schema.\n\nDefined content types: application/json$"
    )
    with pytest.raises(AssertionError, match=message) as exc_info:
        content_type_conformance(response, case)
    assert "SchemaValidationError" in exc_info.type.__name__


def test_invalid_schema_on_content_type_check():
    # When schema validation is disabled, and it doesn't contain "responses" key
    schema = schemathesis.from_dict(
        {
            "openapi": "3.0.2",
            "info": {"title": "Test", "description": "Test", "version": "0.1.0"},
            "paths": {"/users": {"get": {}}},
        },
        validate_schema=False,
    )
    endpoint = schema.endpoints["/users"]["get"]
    case = models.Case(endpoint)
    response = make_response(content_type="application/json")
    # Then an error should be risen
    with pytest.raises(InvalidSchema):
        content_type_conformance(response, case)


def test_missing_content_type_header(case):
    # When the response has no `Content-Type` header
    response = make_response(content_type=None)
    # Then an error should be risen
    with pytest.raises(CheckFailed, match="Response is missing the `Content-Type` header"):
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
    assert case.endpoint.is_response_valid(response)


def test_response_schema_conformance_swagger_no_content_header(swagger_20):
    """Regression: response_schema_conformance does not raise KeyError when response does not have a "Content-Type"."""
    response = requests.Response()
    case = make_case(swagger_20, {})

    with pytest.raises(CheckFailed, match="Response is missing the `Content-Type` header"):
        response_schema_conformance(response, case)


@pytest.mark.parametrize(
    "content, definition",
    (
        (b'{"success": true}', {}),
        (b'{"success": true}', {"responses": {"200": {"description": "text"}}}),
        (b'{"random": "text"}', {"responses": {"200": {"description": "text"}}}),
        (
            b'{"success": null}',
            {
                "responses": {
                    "200": {
                        "description": "text",
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {"success": {"type": "boolean", "nullable": True}},
                                    "required": ["success"],
                                }
                            }
                        },
                    }
                }
            },
        ),
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
    assert case.endpoint.is_response_valid(response)


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
    assert not case.endpoint.is_response_valid(response)
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
    assert not case.endpoint.is_response_valid(response)


@pytest.mark.hypothesis_nested
def test_response_schema_conformance_references_invalid(complex_schema):
    schema = schemathesis.from_path(complex_schema)

    @given(case=schema.endpoints["/teapot"]["POST"].as_strategy())
    @settings(max_examples=3)
    def test(case):
        response = make_response(json.dumps({"foo": 1}).encode())
        with pytest.raises(AssertionError):
            case.validate_response(response)
        assert not case.endpoint.is_response_valid(response)

    test()


@pytest.mark.hypothesis_nested
@pytest.mark.parametrize("value", ("foo", None))
def test_response_schema_conformance_references_valid(complex_schema, value):
    schema = schemathesis.from_path(complex_schema)

    @given(case=schema.endpoints["/teapot"]["POST"].as_strategy())
    @settings(max_examples=3)
    def test(case):
        response = make_response(json.dumps({"key": value, "referenced": value}).encode())
        case.validate_response(response)

    test()
