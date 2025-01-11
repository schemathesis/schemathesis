from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import pytest
from hypothesis import given, settings

import schemathesis
from schemathesis import Case
from schemathesis.checks import CheckContext, not_a_server_error
from schemathesis.core.errors import InvalidSchema
from schemathesis.core.failures import Failure, FailureGroup
from schemathesis.core.transport import Response
from schemathesis.engine.phases.unit._executor import validate_response
from schemathesis.engine.recorder import ScenarioRecorder
from schemathesis.openapi.checks import JsonSchemaError, UndefinedContentType, UndefinedStatusCode
from schemathesis.schemas import APIOperation, OperationDefinition
from schemathesis.specs.openapi.checks import (
    _coerce_header_value,
    content_type_conformance,
    response_headers_conformance,
    response_schema_conformance,
    status_code_conformance,
)

if TYPE_CHECKING:
    from schemathesis.schemas import BaseSchema

CTX = CheckContext(override=None, auth=None, headers=None, config={}, transport_kwargs=None)


def make_case(schema: BaseSchema, definition: dict[str, Any]) -> Case:
    return APIOperation(
        "/path", "GET", definition=OperationDefinition(definition, definition, ""), schema=schema
    ).Case()


@pytest.fixture
def spec(request):
    param = getattr(request, "param", None)
    if param == "swagger":
        return request.getfixturevalue("swagger_20")
    if param == "openapi":
        return request.getfixturevalue("openapi_30")
    return request.getfixturevalue("swagger_20")


@pytest.fixture
def response(request, response_factory):
    return Response.from_requests(response_factory.requests(content_type=request.param), True)


@pytest.fixture
def case(request, spec) -> Case:
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


@pytest.mark.parametrize("spec", ["swagger", "openapi"], indirect=["spec"])
@pytest.mark.parametrize(
    ("response", "case"),
    [
        ("application/json", []),
        ("application/json", ["application/json"]),
        ("application/json;charset=utf-8", ["application/json"]),
    ],
    indirect=["response", "case"],
)
def test_content_type_conformance_valid(spec, response, case):
    assert content_type_conformance(CTX, response, case) is None


@pytest.mark.parametrize(
    "raw_schema",
    [
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
    ],
)
@pytest.mark.parametrize(("content_type", "is_error"), [("application/json", False), ("application/xml", True)])
def test_content_type_conformance_integration(response_factory, raw_schema, content_type, is_error):
    assert_content_type_conformance(response_factory, raw_schema, content_type, is_error)


@pytest.mark.parametrize(
    ("content_type", "is_error"),
    [
        ("application/json", False),
        ("application/xml", True),
    ],
)
def test_content_type_conformance_default_response(response_factory, content_type, is_error):
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
    assert_content_type_conformance(response_factory, raw_schema, content_type, is_error)


@pytest.mark.parametrize(
    ("schema_media_type", "response_media_type"),
    [("application:json", "application/json"), ("application/json", "application:json")],
)
def test_malformed_content_type(schema_media_type, response_media_type, response_factory):
    # When the verified content type is malformed
    raw_schema = {
        "openapi": "3.0.2",
        "info": {"title": "Test", "description": "Test", "version": "0.1.0"},
        "paths": {
            "/users": {
                "get": {
                    "responses": {"default": {"description": "OK", "content": {schema_media_type: {"schema": {}}}}},
                }
            }
        },
    }
    # Then it should raise an assertion error, rather than an internal one
    assert_content_type_conformance(response_factory, raw_schema, response_media_type, True, "Malformed media type")


def test_content_type_conformance_another_status_code(response_factory):
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
    assert_content_type_conformance(response_factory, raw_schema, "application/xml", False)


@pytest.mark.parametrize(
    ("content_type", "is_error"),
    [
        ("application/*", False),
        ("*/xml", False),
        ("*/*", False),
        ("application/json", True),
    ],
)
def test_content_type_wildcards(content_type, is_error, response_factory):
    raw_schema = {
        "openapi": "3.0.2",
        "info": {"title": "Test", "description": "Test", "version": "0.1.0"},
        "paths": {
            "/users": {
                "get": {
                    "responses": {"200": {"description": "Error", "content": {content_type: {"schema": {}}}}},
                }
            }
        },
    }
    assert_content_type_conformance(response_factory, raw_schema, "application/xml", is_error)


def assert_content_type_conformance(response_factory, raw_schema, content_type, is_error, match=None):
    schema = schemathesis.openapi.from_dict(raw_schema)
    operation = schema["/users"]["get"]
    case = operation.Case()
    response = Response.from_requests(response_factory.requests(content_type=content_type), True)
    if not is_error:
        assert content_type_conformance(CTX, response, case) is None
    else:
        with pytest.raises(AssertionError, match=match):
            content_type_conformance(CTX, response, case)


@pytest.mark.parametrize("value", [500, 502])
def test_not_a_server_error(value, swagger_20, response_factory):
    response = response_factory.requests()
    response.status_code = value
    case = make_case(swagger_20, {})
    with pytest.raises(AssertionError) as exc_info:
        not_a_server_error(CTX, response, case)
    assert exc_info.type.__name__ == "ServerError"


@pytest.mark.parametrize("value", [400, 405])
def test_status_code_conformance_valid(value, swagger_20, response_factory):
    response = response_factory.requests()
    response.status_code = value
    case = make_case(swagger_20, {"responses": {"4XX"}})
    status_code_conformance(CTX, response, case)


@pytest.mark.parametrize("value", [400, 405])
def test_status_code_conformance_invalid(value, swagger_20, response_factory):
    response = response_factory.requests()
    response.status_code = value
    case = make_case(swagger_20, {"responses": {"5XX"}})
    with pytest.raises(UndefinedStatusCode):
        status_code_conformance(CTX, response, case)


@pytest.mark.parametrize("spec", ["swagger", "openapi"], indirect=["spec"])
@pytest.mark.parametrize(
    ("response", "case"),
    [("text/plain", ["application/json"]), ("text/plain;charset=utf-8", ["application/json"])],
    indirect=["response", "case"],
)
def test_content_type_conformance_invalid(spec, response, case):
    with pytest.raises(UndefinedContentType, match="Undocumented Content-Type") as exc_info:
        content_type_conformance(CTX, response, case)
    assert exc_info.value.message == f"Received: {response.headers['content-type'][0]}\nDocumented: application/json"


def test_invalid_schema_on_content_type_check(response_factory):
    # When schema validation is disabled, and it doesn't contain "responses" key
    schema = schemathesis.openapi.from_dict(
        {
            "openapi": "3.0.2",
            "info": {"title": "Test", "description": "Test", "version": "0.1.0"},
            "paths": {"/users": {"get": {}}},
        }
    )
    operation = schema["/users"]["get"]
    case = operation.Case()
    response = response_factory.requests(content_type="application/json")
    # Then an error should be risen
    with pytest.raises(InvalidSchema):
        content_type_conformance(CTX, response, case)


def test_missing_content_type_header(case, response_factory):
    # When the response has no `Content-Type` header
    response = response_factory.requests(content_type=None)
    # Then an error should be risen
    with pytest.raises(Failure, match="Missing Content-Type header"):
        content_type_conformance(CTX, response, case)


SUCCESS_SCHEMA = {"type": "object", "properties": {"success": {"type": "boolean"}}, "required": ["success"]}
STRING_FORMAT_SCHEMA = {
    "type": "object",
    "properties": {"value": {"type": "string", "format": "date"}},
    "required": ["value"],
}


@pytest.mark.parametrize(
    ("content", "definition"),
    [
        (b'{"success": true}', {}),
        (b'{"success": true}', {"responses": {"200": {"description": "text"}}}),
        (b'{"random": "text"}', {"responses": {"200": {"description": "text"}}}),
        (b'{"success": true}', {"responses": {"200": {"description": "text", "schema": SUCCESS_SCHEMA}}}),
        (b'{"success": true}', {"responses": {"default": {"description": "text", "schema": SUCCESS_SCHEMA}}}),
        (
            b'{"value": "2017-07-21"}',
            {"responses": {"default": {"description": "text", "schema": STRING_FORMAT_SCHEMA}}},
        ),
    ],
)
def test_response_schema_conformance_swagger(swagger_20, content, definition, response_factory):
    response = Response.from_requests(response_factory.requests(content=content), True)
    case = make_case(swagger_20, definition)
    assert response_schema_conformance(CTX, response, case) is None
    assert case.operation.is_response_valid(response)


@pytest.mark.parametrize(
    ("content", "definition"),
    [
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
        (
            b'{"value": "2017-07-21"}',
            {
                "responses": {
                    "default": {
                        "description": "text",
                        "content": {"application/json": {"schema": STRING_FORMAT_SCHEMA}},
                    }
                }
            },
        ),
    ],
)
def test_response_schema_conformance_openapi(openapi_30, content, definition, response_factory):
    response = Response.from_requests(response_factory.requests(content=content), True)
    case = make_case(openapi_30, definition)
    assert response_schema_conformance(CTX, response, case) is None
    assert case.operation.is_response_valid(response)


def test_response_schema_conformance_openapi_31_boolean(openapi_30, response_factory):
    response = Response.from_requests(response_factory.requests(content=b'{"success": true}'), True)
    case = make_case(
        openapi_30,
        {
            "responses": {
                "default": {
                    "description": "text",
                    "content": {
                        "application/json": {
                            "schema": {"type": "object", "properties": {"success": True}, "required": ["success"]}
                        }
                    },
                }
            }
        },
    )
    openapi_30.raw_schema["openapi"] = "3.1.0"
    assert response_schema_conformance(CTX, response, case) is None
    assert case.operation.is_response_valid(response)


@pytest.mark.parametrize(
    "extra",
    [
        # "content" is not required
        {},
        # "content" can be empty
        {"content": {}},
    ],
)
def test_response_conformance_openapi_no_media_types(openapi_30, extra, response_factory):
    # When there is no media type defined in the schema
    definition = {"responses": {"default": {"description": "text", **extra}}}
    assert_no_media_types(response_factory, openapi_30, definition)


def test_response_conformance_swagger_no_media_types(swagger_20, response_factory):
    # When there is no media type defined in the schema
    definition = {"responses": {"default": {"description": "text"}}}
    assert_no_media_types(response_factory, swagger_20, definition)


def assert_no_media_types(response_factory, schema, definition):
    case = make_case(schema, definition)
    # And no "Content-Type" header in the received response
    response = Response.from_requests(response_factory.requests(content_type=None, status_code=204), True)
    # Then there should be no errors
    assert response_schema_conformance(CTX, response, case) is None


@pytest.mark.parametrize("spec", ["swagger_20", "openapi_30"])
def test_response_conformance_no_content_type(request, spec, response_factory):
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
    response = Response.from_requests(response_factory.requests(content_type=None, status_code=200), True)
    # Then the check should fail
    with pytest.raises(FailureGroup) as exc:
        response_schema_conformance(CTX, response, case)
    assert (
        str(exc.value.exceptions[0])
        == """Missing Content-Type header

The following media types are documented in the schema:
- `application/json`"""
    )
    assert (
        str(exc.value.exceptions[1])
        == """Response violates schema

'success' is a required property

Schema:

    {
        "type": "object",
        "properties": {
            "success": {
                "type": "boolean"
            }
        },
        "required": [
            "success"
        ]
    }

Value:

    {}"""
    )


@pytest.mark.parametrize(
    ("content", "definition"),
    [
        (b'{"random": "text"}', {"responses": {"200": {"description": "text", "schema": SUCCESS_SCHEMA}}}),
        (b'{"random": "text"}', {"responses": {"default": {"description": "text", "schema": SUCCESS_SCHEMA}}}),
        (b'{"value": "text"}', {"responses": {"default": {"description": "text", "schema": STRING_FORMAT_SCHEMA}}}),
    ],
)
def test_response_schema_conformance_invalid_swagger(swagger_20, content, definition, response_factory):
    response = Response.from_requests(response_factory.requests(content=content), True)
    case = make_case(swagger_20, definition)
    with pytest.raises(JsonSchemaError):
        response_schema_conformance(CTX, response, case)
    assert not case.operation.is_response_valid(response)


@pytest.mark.parametrize(
    ("media_type", "content", "definition"),
    [
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
            "application/json",
            b'{"value": "text"}',
            {
                "responses": {
                    "default": {
                        "description": "text",
                        "content": {"application/json": {"schema": STRING_FORMAT_SCHEMA}},
                    }
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
    ],
)
def test_response_schema_conformance_invalid_openapi(openapi_30, media_type, content, definition, response_factory):
    response = Response.from_requests(response_factory.requests(content=content, content_type=media_type), True)
    case = make_case(openapi_30, definition)
    with pytest.raises(AssertionError):
        response_schema_conformance(CTX, response, case)
    assert not case.operation.is_response_valid(response)


def test_no_schema(openapi_30, response_factory):
    # See GH-1220
    # When the response definition has no "schema" key
    response = Response.from_requests(response_factory.requests(), True)
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
    response_schema_conformance(CTX, response, case)
    assert case.operation.is_response_valid(response)


@pytest.mark.hypothesis_nested
def test_response_schema_conformance_references_invalid(complex_schema, response_factory):
    schema = schemathesis.openapi.from_path(complex_schema)

    @given(case=schema["/teapot"]["POST"].as_strategy())
    @settings(max_examples=3, deadline=None)
    def test(case):
        response = Response.from_requests(response_factory.requests(content=json.dumps({"foo": 1}).encode()), True)
        with pytest.raises(FailureGroup):
            case.validate_response(response)
        assert not case.operation.is_response_valid(response)

    test()


@pytest.mark.hypothesis_nested
@pytest.mark.parametrize("value", ["foo", None])
def test_response_schema_conformance_references_valid(complex_schema, value, response_factory):
    schema = schemathesis.openapi.from_path(complex_schema)

    @given(case=schema["/teapot"]["POST"].as_strategy())
    @settings(max_examples=3, deadline=None)
    def test(case):
        response = Response.from_requests(
            response_factory.requests(content=json.dumps({"key": value, "referenced": value}).encode()), True
        )
        case.validate_response(response)

    test()


def test_deduplication(ctx, response_factory):
    # See GH-1394
    schema = ctx.openapi.build_schema(
        {
            "/data": {
                "get": {
                    "responses": {
                        "200": {"description": "OK", "content": {"application/json": {"schema": {"type": "integer"}}}}
                    },
                },
            },
        }
    )
    schema = schemathesis.openapi.from_dict(schema)
    operation = schema["/data"]["GET"]
    case = operation.Case()
    response = Response.from_requests(response_factory.requests(), True)
    recorder = ScenarioRecorder(label="test")
    recorder.record_case(parent_id=None, case=case)
    recorder.record_response(case_id=case.id, response=response)
    # When there are two checks that raise the same failure
    with pytest.raises(FailureGroup):
        validate_response(
            case=case,
            ctx=CTX,
            checks=(content_type_conformance, response_schema_conformance),
            recorder=recorder,
            response=response,
            no_failfast=False,
        )
    # Then the resulting output should be deduplicated
    assert (
        len([check for checks in recorder.checks.values() for check in checks if check.failure_info is not None]) == 1
    )


@pytest.fixture(params=["2.0", "3.0"])
def schema_with_optional_headers(ctx, request):
    if request.param == "2.0":
        return ctx.openapi.build_schema(
            {
                "/data": {
                    "get": {
                        "responses": {
                            "200": {
                                "description": "OK",
                                "schema": {"type": "object"},
                                "headers": {
                                    "X-Optional": {
                                        "description": "Optional header",
                                        "type": "integer",
                                        "x-required": False,
                                    }
                                },
                            }
                        },
                    }
                },
            },
            version="2.0",
        )
    if request.param == "3.0":
        return ctx.openapi.build_schema(
            {
                "/data": {
                    "get": {
                        "responses": {
                            "200": {
                                "description": "OK",
                                "content": {"application/json": {"schema": {"type": "object"}}},
                                "headers": {
                                    "X-Optional": {
                                        "description": "Optional header",
                                        "schema": {"type": "integer"},
                                        "required": False,
                                    }
                                },
                            }
                        },
                    }
                },
            }
        )


def test_optional_headers_missing(schema_with_optional_headers, response_factory):
    # When a response header is declared as optional
    # NOTE: Open API 2.0 headers are much simpler and do not contain any notion of declaring them as optional
    # For this reason we support `x-required` instead
    schema = schemathesis.openapi.from_dict(schema_with_optional_headers)
    case = make_case(schema, schema_with_optional_headers["paths"]["/data"]["get"])
    response = Response.from_requests(response_factory.requests(), True)
    # Then it should not be reported as missing
    assert response_headers_conformance(CTX, response, case) is None


INTEGER_HEADER = {"type": "integer", "maximum": 100}
DATETIME_HEADER = {"type": "string", "format": "date-time"}


@pytest.mark.parametrize("version", ["2.0", "3.0.2"])
@pytest.mark.parametrize(
    ("header", "schema", "value", "expected"),
    [
        ("X-RateLimit-Limit", INTEGER_HEADER, "42", True),
        ("X-RateLimit-Limit", INTEGER_HEADER, "150", False),
        ("X-RateLimit-Reset", DATETIME_HEADER, "2021-01-01T00:00:00Z", True),
        ("X-RateLimit-Reset", DATETIME_HEADER, "Invalid", False),
    ],
)
def test_header_conformance(ctx, response_factory, version, header, schema, value, expected):
    base_schema = ctx.openapi.build_schema(
        {
            "/data": {
                "get": {
                    "responses": {
                        "200": {
                            "description": "OK",
                            "headers": {
                                header: {
                                    "description": "Header",
                                    **({"schema": schema} if version == "3.0.2" else schema),
                                }
                            },
                        }
                    },
                }
            },
        },
        version=version,
    )
    schema = schemathesis.openapi.from_dict(base_schema)
    case = make_case(schema, base_schema["paths"]["/data"]["get"])
    response = Response.from_requests(response_factory.requests(headers={header: value}), True)
    if expected is True:
        assert response_headers_conformance(CTX, response, case) is None
    else:
        with pytest.raises(AssertionError, match="Response header does not conform to the schema"):
            response_headers_conformance(CTX, response, case)


def test_header_conformance_definition_behind_ref(ctx, response_factory):
    raw_schema = ctx.openapi.build_schema(
        {
            "/data": {
                "get": {
                    "responses": {
                        "200": {
                            "description": "OK",
                            "headers": {
                                "Link": {
                                    "$ref": "#/components/headers/Link",
                                }
                            },
                        }
                    },
                }
            },
        },
        components={
            "headers": {
                "Link": {
                    "schema": {"type": "integer"},
                },
            },
        },
    )
    schema = schemathesis.openapi.from_dict(raw_schema)
    case = make_case(schema, raw_schema["paths"]["/data"]["get"])
    response = Response.from_requests(response_factory.requests(headers={"Link": "Test"}), True)
    with pytest.raises(AssertionError, match="Response header does not conform to the schema"):
        response_headers_conformance(CTX, response, case)


MULTIPLE_HEADERS = {
    "/data": {
        "get": {
            "responses": {
                "200": {
                    "description": "OK",
                    "headers": {
                        "X-RateLimit-Limit": {"description": "Header", "schema": INTEGER_HEADER, "required": True},
                        "X-RateLimit-Reset": {"description": "Header", "schema": DATETIME_HEADER, "required": True},
                    },
                }
            },
        }
    },
}


def test_header_conformance_multiple_invalid_headers(ctx, response_factory):
    raw_schema = ctx.openapi.build_schema(MULTIPLE_HEADERS)
    schema = schemathesis.openapi.from_dict(raw_schema)
    case = make_case(schema, raw_schema["paths"]["/data"]["get"])
    response = Response.from_requests(
        response_factory.requests(headers={"X-RateLimit-Limit": "150", "X-RateLimit-Reset": "Invalid"}), True
    )
    with pytest.raises(FailureGroup) as exc:
        response_headers_conformance(CTX, response, case)
    assert (
        str(exc.value.exceptions[0])
        == """Response header does not conform to the schema

150 is greater than the maximum of 100

Schema:

    {
        "type": "integer",
        "maximum": 100
    }

Value:

    150"""
    )
    assert (
        str(exc.value.exceptions[1])
        == """Response header does not conform to the schema

'Invalid' is not a 'date-time'

Schema:

    {
        "type": "string",
        "format": "date-time"
    }

Value:

    "Invalid\""""
    )


def test_header_conformance_missing_and_invalid(ctx, response_factory):
    raw_schema = ctx.openapi.build_schema(MULTIPLE_HEADERS)
    schema = schemathesis.openapi.from_dict(raw_schema)
    case = make_case(schema, raw_schema["paths"]["/data"]["get"])
    response = Response.from_requests(response_factory.requests(headers={"X-RateLimit-Limit": "150"}), True)
    with pytest.raises(FailureGroup) as exc:
        response_headers_conformance(CTX, response, case)
    assert (
        str(exc.value.exceptions[0])
        == """Missing required headers

The following required headers are missing from the response:
- `X-RateLimit-Reset`"""
    )
    assert (
        str(exc.value.exceptions[1])
        == """Response header does not conform to the schema

150 is greater than the maximum of 100

Schema:

    {
        "type": "integer",
        "maximum": 100
    }

Value:

    150"""
    )


@pytest.mark.parametrize(
    ("value", "schema", "expected"),
    [
        # String type
        ("test", {"type": "string"}, "test"),
        ("123", {"type": "string"}, "123"),
        # Integer type
        ("123", {"type": "integer"}, 123),
        ("-456", {"type": "integer"}, -456),
        ("12.34", {"type": "integer"}, "12.34"),  # Non-integer string
        ("abc", {"type": "integer"}, "abc"),  # Non-numeric string
        # Number type
        ("123.45", {"type": "number"}, 123.45),
        ("-67.89", {"type": "number"}, -67.89),
        ("123", {"type": "number"}, 123.0),
        ("abc", {"type": "number"}, "abc"),  # Non-numeric string
        # Null type
        ("null", {"type": "null"}, None),
        ("NULL", {"type": "null"}, None),
        ("Null", {"type": "null"}, None),
        ("not null", {"type": "null"}, "not null"),
        # Boolean type
        ("true", {"type": "boolean"}, True),
        ("false", {"type": "boolean"}, False),
        ("1", {"type": "boolean"}, True),
        ("0", {"type": "boolean"}, False),
        # Unsupported type
        ("test", {"type": "array"}, "test"),
        ("test", {"type": "object"}, "test"),
        # No type specified
        ("test", {}, "test"),
    ],
)
def test_coerce_header_value(value, schema, expected):
    assert _coerce_header_value(value, schema) == expected
