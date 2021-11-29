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
from schemathesis.models import OperationDefinition
from schemathesis.schemas import BaseSchema


def make_case(schema: BaseSchema, definition: Dict[str, Any]) -> models.Case:
    operation = models.APIOperation(
        "/path", "GET", definition=OperationDefinition(definition, definition, None, []), schema=schema
    )
    return models.Case(operation)


def make_response(
    content: bytes = b"{}", content_type: Optional[str] = "application/json", status_code: int = 200
) -> requests.Response:
    response = requests.Response()
    response._content = content
    response.status_code = status_code
    if content_type:
        response.headers["Content-Type"] = content_type
    request = requests.Request(method="POST", url="http://127.0.0.1", headers={})
    response.request = request.prepare()
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


def test_malformed_content_type():
    # When the verified content type is malformed
    raw_schema = {
        "openapi": "3.0.2",
        "info": {"title": "Test", "description": "Test", "version": "0.1.0"},
        "paths": {
            "/users": {
                "get": {
                    "responses": {"default": {"description": "OK", "content": {"application:json": {"schema": {}}}}},
                }
            }
        },
    }
    # Then it should raise an assertion error, rather than an internal one
    assert_content_type_conformance(raw_schema, "application/json", True, "Malformed media type: `application:json`")


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


def assert_content_type_conformance(raw_schema, content_type, is_error, match=None):
    schema = schemathesis.from_dict(raw_schema)
    operation = schema["/users"]["get"]
    case = models.Case(operation)
    response = make_response(content_type=content_type)
    if not is_error:
        assert content_type_conformance(response, case) is None
    else:
        with pytest.raises(AssertionError, match=match):
            content_type_conformance(response, case)


@pytest.mark.parametrize("value", (500, 502))
def test_not_a_server_error(value, swagger_20):
    response = make_response()
    response.status_code = value
    case = make_case(swagger_20, {})
    with pytest.raises(AssertionError) as exc_info:
        not_a_server_error(response, case)
    assert exc_info.type.__name__ == "CheckFailed"


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
    assert exc_info.type.__name__ == "CheckFailed"


@pytest.mark.parametrize("spec", ("swagger", "openapi"), indirect=["spec"])
@pytest.mark.parametrize(
    "response, case",
    (("text/plain", ["application/json"]), ("text/plain;charset=utf-8", ["application/json"])),
    indirect=["response", "case"],
)
def test_content_type_conformance_invalid(spec, response, case):
    message = (
        f"^Received a response with '{response.headers['Content-Type']}' Content-Type, "
        "but it is not declared in the schema.\n\nDefined content types: application/json$"
    )
    with pytest.raises(AssertionError, match=message) as exc_info:
        content_type_conformance(response, case)
    assert exc_info.type.__name__ == "CheckFailed"


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
    operation = schema["/users"]["get"]
    case = models.Case(operation)
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
    assert case.operation.is_response_valid(response)


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
    assert case.operation.is_response_valid(response)


@pytest.mark.parametrize(
    "extra",
    (
        # "content" is not required
        {},
        # "content" can be empty
        {"content": {}},
    ),
)
def test_response_conformance_openapi_no_media_types(openapi_30, extra):
    # When there is no media type defined in the schema
    definition = {"responses": {"default": {"description": "text", **extra}}}
    assert_no_media_types(openapi_30, definition)


def test_response_conformance_swagger_no_media_types(swagger_20):
    # When there is no media type defined in the schema
    definition = {"responses": {"default": {"description": "text"}}}
    assert_no_media_types(swagger_20, definition)


def assert_no_media_types(schema, definition):
    case = make_case(schema, definition)
    # And no "Content-Type" header in the received response
    response = make_response(content_type=None, status_code=204)
    # Then there should be no errors
    assert response_schema_conformance(response, case) is None


@pytest.mark.parametrize("spec", ("swagger_20", "openapi_30"))
def test_response_conformance_no_content_type(request, spec):
    # When there is a media type defined in the schema
    schema = request.getfixturevalue(spec)
    if spec == "swagger_20":
        definition = {
            "produces": ["application/json"],
            "responses": {"default": {"description": "text", "schema": SUCCESS_SCHEMA}},
        }
    else:
        definition = {
            "responses": {
                "default": {"description": "text", "content": {"application/json": {"schema": SUCCESS_SCHEMA}}}
            }
        }
    case = make_case(schema, definition)
    # And no "Content-Type" header in the received response
    response = make_response(content_type=None, status_code=200)
    # Then the check should fail
    with pytest.raises(
        CheckFailed,
        match="The response is missing the `Content-Type` header. "
        "The schema defines the following media types:\n\n    application/json",
    ):
        response_schema_conformance(response, case)


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
    assert not case.operation.is_response_valid(response)
    assert exc_info.type.__name__ == "CheckFailed"


@pytest.mark.parametrize(
    "media_type, content, definition",
    (
        (
            "application/json",
            b'{"random": "text"}',
            {
                "responses": {
                    "200": {"description": "text", "content": {"application/json": {"schema": SUCCESS_SCHEMA}}}
                }
            },
        ),
        (
            "application/json",
            b'{"random": "text"}',
            {
                "responses": {
                    "default": {"description": "text", "content": {"application/json": {"schema": SUCCESS_SCHEMA}}}
                }
            },
        ),
        (
            "application/problem+json",
            b'{"random": "text"}',
            {
                "responses": {
                    "default": {
                        "description": "text",
                        "content": {"application/problem+json": {"schema": SUCCESS_SCHEMA}},
                    }
                }
            },
        ),
    ),
)
def test_response_schema_conformance_invalid_openapi(openapi_30, media_type, content, definition):
    response = make_response(content, media_type)
    case = make_case(openapi_30, definition)
    with pytest.raises(AssertionError):
        response_schema_conformance(response, case)
    assert not case.operation.is_response_valid(response)


def test_no_schema(openapi_30):
    # See GH-1220
    # When the response definition has no "schema" key
    response = make_response(b"{}", "application/json")
    definition = {
        "responses": {
            "default": {
                "description": "text",
                "content": {"application/problem": {"examples": {"test": {}}}},
            }
        }
    }
    case = make_case(openapi_30, definition)
    # Then the check should be ignored
    response_schema_conformance(response, case)
    assert case.operation.is_response_valid(response)


@pytest.mark.hypothesis_nested
def test_response_schema_conformance_references_invalid(complex_schema):
    schema = schemathesis.from_path(complex_schema)

    @given(case=schema["/teapot"]["POST"].as_strategy())
    @settings(max_examples=3, deadline=None)
    def test(case):
        response = make_response(json.dumps({"foo": 1}).encode())
        with pytest.raises(AssertionError):
            case.validate_response(response)
        assert not case.operation.is_response_valid(response)

    test()


@pytest.mark.hypothesis_nested
@pytest.mark.parametrize("value", ("foo", None))
def test_response_schema_conformance_references_valid(complex_schema, value):
    schema = schemathesis.from_path(complex_schema)

    @given(case=schema["/teapot"]["POST"].as_strategy())
    @settings(max_examples=3, deadline=None)
    def test(case):
        response = make_response(json.dumps({"key": value, "referenced": value}).encode())
        case.validate_response(response)

    test()
