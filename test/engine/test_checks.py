from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import pytest

import schemathesis.checks
from schemathesis import Case
from schemathesis.checks import max_response_time, not_a_server_error, run_checks
from schemathesis.config._checks import ChecksConfig
from schemathesis.core.errors import InvalidSchema
from schemathesis.core.failures import CustomFailure, Failure, FailureGroup, ResponseTimeExceeded, ServerError
from schemathesis.core.transport import Response
from schemathesis.engine._validate import validate_response
from schemathesis.engine.recorder import ScenarioRecorder
from schemathesis.failure_codes import FAILURE_CLASSES
from schemathesis.openapi.checks import (
    DiscriminatorMismatch,
    JsonSchemaError,
    MalformedMediaType,
    MissingHeaders,
    UndefinedContentType,
    UndefinedStatusCode,
    UseAfterFree,
)
from schemathesis.specs.openapi.checks import (
    _coerce_header_value,
    content_type_conformance,
    response_headers_conformance,
    response_schema_conformance,
    status_code_conformance,
    use_after_free,
)
from test.utils import check_context, run_given

if TYPE_CHECKING:
    from schemathesis.schemas import BaseSchema


def _oas3_response(schema, *, description="text", media_type="application/json"):
    return {"description": description, "content": {media_type: {"schema": schema}}}


def _oas3_definition(schema, *, status="default", description="text", media_type="application/json"):
    return {"responses": {status: _oas3_response(schema, description=description, media_type=media_type)}}


def _swagger_definition(schema, *, status="default", description="text"):
    return {"responses": {status: {"description": description, "schema": schema}}}


def make_case(ctx, schema: BaseSchema, definition: dict[str, Any]) -> Case:
    return ctx.openapi.from_full_schema({**schema.raw_schema, "paths": {"/path": {"get": definition}}})["/path"][
        "GET"
    ].Case()


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
def case(ctx, request, spec) -> Case:
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
    return make_case(ctx, spec, data)


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
    assert content_type_conformance(check_context(), response, case) is None


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
def test_content_type_conformance_integration(ctx, response_factory, raw_schema, content_type, is_error):
    schema = ctx.openapi.from_full_schema(raw_schema)
    assert_content_type_conformance(response_factory, schema, content_type, is_error)


@pytest.mark.parametrize(
    ("content_type", "is_error"),
    [
        ("application/json", False),
        ("application/xml", True),
    ],
)
def test_content_type_conformance_default_response(ctx, response_factory, content_type, is_error):
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
    schema = ctx.openapi.from_full_schema(raw_schema)
    assert_content_type_conformance(response_factory, schema, content_type, is_error)


@pytest.mark.parametrize(
    ("schema_media_type", "response_media_type"),
    [("application:json", "application/json"), ("application/json", "application:json")],
)
def test_malformed_content_type(ctx, schema_media_type, response_media_type, response_factory):
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
    schema = ctx.openapi.from_full_schema(raw_schema)
    location = "Schema" if schema_media_type == "application:json" else "Response"
    response = Response.from_requests(response_factory.requests(content_type=response_media_type), True)
    with pytest.raises(MalformedMediaType) as exc_info:
        content_type_conformance(check_context(), response, schema["/users"]["GET"].Case())
    failure = exc_info.value
    assert (failure.operation, failure.message, failure.actual, failure.defined) == (
        "GET /users",
        f"Media type for {location} is incorrect\n\nReceived: application:json\nDocumented: {schema_media_type}",
        "application:json",
        schema_media_type,
    )


def test_content_type_conformance_another_status_code(ctx, response_factory):
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
    schema = ctx.openapi.from_full_schema(raw_schema)
    assert_content_type_conformance(response_factory, schema, "application/xml", False)


@pytest.mark.parametrize(
    ("content_type", "is_error"),
    [
        ("application/*", False),
        ("*/xml", False),
        ("*/*", False),
        ("application/json", True),
    ],
)
def test_content_type_wildcards(ctx, content_type, is_error, response_factory):
    schema = ctx.openapi.load_schema(
        {
            "/users": {
                "get": {
                    "responses": {"200": {"description": "Error", "content": {content_type: {"schema": {}}}}},
                }
            }
        }
    )
    assert_content_type_conformance(response_factory, schema, "application/xml", is_error)


def assert_content_type_conformance(response_factory, schema, content_type, is_error, match=None):
    operation = schema["/users"]["get"]
    case = operation.Case()
    response = Response.from_requests(response_factory.requests(content_type=content_type), True)
    if not is_error:
        assert content_type_conformance(check_context(), response, case) is None
    else:
        with pytest.raises(AssertionError, match=match):
            content_type_conformance(check_context(), response, case)


@pytest.mark.parametrize(
    ("limit", "elapsed", "expected"),
    [
        (None, 10.0, None),
        (None, 10.5, ("GET /path", "Actual: 10500.00ms\nLimit: 10000.00ms", 10.5, 10.0)),
        (0.25, 0.25, None),
        (0.25, 0.3, ("GET /path", "Actual: 300.00ms\nLimit: 250.00ms", 0.3, 0.25)),
    ],
    ids=["default-limit-reached", "default-limit-exceeded", "limit-reached", "limit-exceeded"],
)
def test_max_response_time(ctx, swagger_20, response_factory, limit, elapsed, expected):
    config = ChecksConfig.from_dict({} if limit is None else {"max_response_time": limit})
    response = response_factory.core(elapsed=elapsed)
    case = make_case(ctx, swagger_20, {})
    if expected is None:
        assert max_response_time(check_context(config), response, case) is None
    else:
        with pytest.raises(ResponseTimeExceeded) as exc_info:
            max_response_time(check_context(config), response, case)
        failure = exc_info.value
        assert (failure.operation, failure.message, failure.elapsed, failure.deadline) == expected


def test_run_checks_reports_kept_failures_and_custom_assertions(ctx, response_factory):
    schema = ctx.openapi.load_schema({"/foo": {"get": {"responses": {"200": {"description": "OK"}}}}})

    @schema.hooks.hook
    def filter_failure(context, failure, case, response):
        return not (isinstance(failure, ServerError) and failure.status_code == 502)

    def server_errors(ctx, response, case):
        raise FailureGroup(
            [ServerError(operation="GET /foo", status_code=502), ServerError(operation="GET /foo", status_code=503)]
        )

    def dropped(ctx, response, case):
        raise ServerError(operation="GET /foo", status_code=502)

    def custom(ctx, response, case):
        raise AssertionError("broken")

    def passing(ctx, response, case):
        return None

    case = schema["/foo"]["GET"].Case()
    recorder = ScenarioRecorder(label="run-checks")
    recorder.record_case(parent_id=None, case=case, transition=None, is_transition_applied=False)
    failures = []
    successes = []
    run_checks(
        case=case,
        response=response_factory.core(),
        ctx=check_context(recorder=recorder),
        checks=[server_errors, dropped, custom, passing],
        on_failure=lambda name, collected, failure: failures.append((name, failure)),
        on_success=lambda name, case: successes.append((name, case.id)),
    )

    (server_errors_name, server_error), (custom_name, custom) = failures
    assert (
        (server_errors_name, type(server_error), server_error.operation, server_error.status_code),
        (custom_name, type(custom), custom.operation, custom.title, custom.message),
    ) == (
        ("server_errors", ServerError, "GET /foo", 503),
        ("custom", CustomFailure, "GET /foo", "Custom check failed: `custom`", "broken"),
    )
    assert successes == [("dropped", case.id), ("passing", case.id)]
    assert recorder.filtered_failures == {case.id: 2}


@pytest.mark.parametrize("value", [500, 502])
def test_not_a_server_error(ctx, value, swagger_20, response_factory):
    response = response_factory.requests()
    response.status_code = value
    case = make_case(ctx, swagger_20, {})
    with pytest.raises(AssertionError) as exc_info:
        not_a_server_error(check_context(), response, case)
    assert exc_info.type.__name__ == "ServerError"


@pytest.mark.parametrize("value", [400, 405])
def test_status_code_conformance_valid(ctx, value, swagger_20, response_factory):
    response = response_factory.requests()
    response.status_code = value
    case = make_case(ctx, swagger_20, {"responses": {"4XX": {}}})
    status_code_conformance(check_context(), response, case)


@pytest.mark.parametrize("value", [400, 405])
def test_status_code_conformance_invalid(ctx, value, swagger_20, response_factory):
    response = response_factory.requests()
    response.status_code = value
    case = make_case(ctx, swagger_20, {"responses": {"5XX": {}}})
    with pytest.raises(UndefinedStatusCode):
        status_code_conformance(check_context(), response, case)


@pytest.mark.parametrize("spec", ["swagger", "openapi"], indirect=["spec"])
@pytest.mark.parametrize(
    ("response", "case"),
    [("text/plain", ["application/json"]), ("text/plain;charset=utf-8", ["application/json"])],
    indirect=["response", "case"],
)
def test_content_type_conformance_invalid(spec, response, case):
    with pytest.raises(UndefinedContentType, match="Undocumented Content-Type") as exc_info:
        content_type_conformance(check_context(), response, case)
    assert exc_info.value.message == f"Received: {response.headers['content-type'][0]}\nDocumented: application/json"


def test_invalid_schema_on_content_type_check(ctx, response_factory):
    # When schema validation is disabled, and it doesn't contain "responses" key
    schema = ctx.openapi.load_schema({"/users": {"get": {}}})
    operation = schema["/users"]["get"]
    case = operation.Case()
    response = response_factory.requests(content_type="application/json")
    # Then there should be no error
    content_type_conformance(check_context(), response, case)


def test_missing_content_type_header(case, response_factory):
    # When the response has no `Content-Type` header
    response = response_factory.requests(content_type=None)
    # Then an error should be risen
    with pytest.raises(Failure, match="Missing Content-Type header"):
        content_type_conformance(check_context(), response, case)


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
        (b'{"success": true}', _swagger_definition(SUCCESS_SCHEMA, status="200")),
        (b'{"success": true}', _swagger_definition(SUCCESS_SCHEMA, status="2XX")),
        (b'{"success": true}', _swagger_definition(SUCCESS_SCHEMA)),
        (
            b'{"value": "2017-07-21"}',
            _swagger_definition(STRING_FORMAT_SCHEMA),
        ),
    ],
)
def test_response_schema_conformance_swagger(ctx, swagger_20, content, definition, response_factory):
    response = Response.from_requests(response_factory.requests(content=content), True)
    case = make_case(ctx, swagger_20, definition)
    assert response_schema_conformance(check_context(), response, case) is None
    assert case.operation.is_valid_response(response)


def test_response_schema_conformance_type_after_large_required(ctx, openapi_30, response_factory):
    # Schema with a large 'required' array followed by 'type' keyword
    many_fields = [f"field_{i}" for i in range(100)]
    schema = {
        "required": many_fields,
        "type": "object",
        "properties": {field: {"type": "string"} for field in many_fields},
    }

    # Response is a string, not an object - will fail on 'type' validation
    response = Response.from_requests(response_factory.requests(content=b'"not an object"'), True)
    case = make_case(ctx, openapi_30, _oas3_definition(schema, status="200"))

    with pytest.raises(JsonSchemaError) as exc:
        response_schema_conformance(check_context(), response, case)
    assert (
        str(exc.value)
        == """Response violates schema

"not an object" is not of type "object"

Validated against the response schema for status code 200.

Schema:

    {
        "type": "object",
        "required": [
            "field_0",
            "field_1",
            "field_2",
            "field_3",
            "field_4",
            "field_5",
            "field_6",
            "field_7",
            "field_8",
            "field_9",
            "field_10",
            "field_11",
            "field_12",
            "field_13",
            "field_14",
            "field_15",
        // Output truncated...
    }

Value:

    "not an object\""""
    )


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
            _oas3_definition(SUCCESS_SCHEMA, status="200"),
        ),
        (
            b'{"success": true}',
            _oas3_definition(SUCCESS_SCHEMA, status="2XX"),
        ),
        (
            b'{"success": true}',
            {
                "responses": {
                    # Specific status code should be used instead of a more general one
                    "200": {
                        "description": "text",
                        "content": {"application/json": {"schema": SUCCESS_SCHEMA}},
                    },
                    "2XX": {
                        "description": "text",
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {"success": {"type": "string"}},
                                    "required": ["success"],
                                }
                            }
                        },
                    },
                }
            },
        ),
        (
            b'{"success": true}',
            {"responses": {"default": _oas3_response(SUCCESS_SCHEMA)}},
        ),
        (
            b'{"value": "2017-07-21"}',
            {"responses": {"default": _oas3_response(STRING_FORMAT_SCHEMA)}},
        ),
    ],
)
def test_response_schema_conformance_openapi(ctx, openapi_30, content, definition, response_factory):
    response = Response.from_requests(response_factory.requests(content=content), True)
    case = make_case(ctx, openapi_30, definition)
    assert response_schema_conformance(check_context(), response, case) is None
    assert case.operation.is_valid_response(response)


def test_response_schema_conformance_yaml(ctx, openapi_30, response_factory):
    content = b"success: true"
    definition = _oas3_definition(SUCCESS_SCHEMA, media_type="application/yaml")
    response = Response.from_requests(
        response_factory.requests(
            content=content,
            content_type="application/yaml",
        ),
        True,
    )
    case = make_case(ctx, openapi_30, definition)
    assert response_schema_conformance(check_context(), response, case) is None
    assert case.operation.is_valid_response(response)


@pytest.mark.parametrize(
    "schema",
    [
        {"type": "object", "properties": {"success": True}, "required": ["success"]},
        True,
    ],
)
def test_response_schema_conformance_openapi_31_boolean(ctx, openapi_31, response_factory, schema):
    response = Response.from_requests(response_factory.requests(content=b'{"success": true}'), True)
    case = make_case(
        ctx,
        openapi_31,
        _oas3_definition(schema),
    )
    assert response_schema_conformance(check_context(), response, case) is None
    assert case.operation.is_valid_response(response)


def test_response_schema_conformance_nullable_type_array_enum(ctx, openapi_31, response_factory):
    # Regression for #4249: a `type: [string, "null"]` enum inside additionalProperties/anyOf accepts null.
    schema = {
        "type": "object",
        "additionalProperties": {
            "anyOf": [
                {"type": "array", "items": {"type": ["number", "null"], "minimum": 0, "maximum": 360}},
                {"type": "array", "items": {"type": ["string", "null"], "enum": ["N", "E", "S", "W"]}},
            ]
        },
    }
    response = Response.from_requests(
        response_factory.requests(content=b'{"direction": ["N", "E", "S", "W", null]}'), True
    )
    case = make_case(ctx, openapi_31, _oas3_definition(schema, status="200"))
    assert response_schema_conformance(check_context(), response, case) is None
    assert case.operation.is_valid_response(response)


def test_response_schema_conformance_invalid_properties(ctx, openapi_30, response_factory):
    response = Response.from_requests(response_factory.requests(content=b'{"success": true}'), True)
    case = make_case(
        ctx,
        openapi_30,
        {
            "responses": {
                "default": {
                    "description": "text",
                    "content": {
                        "application/json": {
                            "schema": {
                                # Seen in real-life schema
                                "properties": "ABC",
                            }
                        }
                    },
                }
            }
        },
    )
    with pytest.raises(InvalidSchema):
        assert response_schema_conformance(check_context(), response, case) is None


@pytest.mark.parametrize(
    "extra",
    [
        # "content" is not required
        {},
        # "content" can be empty
        {"content": {}},
    ],
)
def test_response_conformance_openapi_no_media_types(ctx, openapi_30, extra, response_factory):
    # When there is no media type defined in the schema
    definition = {"responses": {"default": {"description": "text", **extra}}}
    assert_no_media_types(ctx, response_factory, openapi_30, definition)


def test_response_conformance_swagger_no_media_types(ctx, swagger_20, response_factory):
    # When there is no media type defined in the schema
    definition = {"responses": {"default": {"description": "text"}}}
    assert_no_media_types(ctx, response_factory, swagger_20, definition)


def assert_no_media_types(ctx, response_factory, schema, definition):
    case = make_case(ctx, schema, definition)
    # And no "Content-Type" header in the received response
    response = Response.from_requests(response_factory.requests(content_type=None, status_code=204), True)
    # Then there should be no errors
    assert response_schema_conformance(check_context(), response, case) is None


def test_response_conformance_unresolvable_file(ctx, cli, snapshot_cli):
    schema_path = ctx.openapi.write_schema(
        {
            "/test": {
                "put": {
                    "responses": {
                        "default": {
                            "schema": {
                                "$ref": "#/components/schemas/Unit",
                            }
                        }
                    }
                }
            }
        },
        components={
            "schemas": {
                "Unit": {
                    "$ref": "./unknown.json",
                }
            }
        },
        version="2.0",
    )
    api = ctx.openapi.apps.success()
    assert (
        cli.run(str(schema_path), f"--url={api.base_url}/api", "--checks=response_schema_conformance") == snapshot_cli
    )


@pytest.mark.parametrize("spec", ["swagger_20", "openapi_30"])
def test_response_conformance_no_content_type(ctx, request, spec, response_factory):
    # When there is a media type defined in the schema
    schema = request.getfixturevalue(spec)
    if spec == "swagger_20":
        definition = _swagger_definition(SUCCESS_SCHEMA)
        definition["produces"] = ["application/json"]
    else:
        definition = _oas3_definition(SUCCESS_SCHEMA)
    case = make_case(ctx, schema, definition)
    # And no "Content-Type" header in the received response
    response = Response.from_requests(response_factory.requests(content_type=None, status_code=200), True)
    # Then the check should fail
    with pytest.raises(FailureGroup) as exc:
        response_schema_conformance(check_context(), response, case)
    assert (
        str(exc.value.exceptions[0])
        == """Missing Content-Type header

The following media types are documented in the schema:
- `application/json`"""
    )
    assert (
        str(exc.value.exceptions[1])
        == """Response violates schema

"success" is a required property

Validated against the `default` response schema (status code 200).

Schema:

    {
        "required": [
            "success"
        ],
        "type": "object",
        "properties": {
            "success": {
                "type": "boolean"
            }
        }
    }

Value:

    {}"""
    )


@pytest.mark.parametrize(
    ("content", "definition"),
    [
        (b'{"random": "text"}', _swagger_definition(SUCCESS_SCHEMA, status="200")),
        (b'{"random": "text"}', _swagger_definition(SUCCESS_SCHEMA, status="2XX")),
        (b'{"random": "text"}', _swagger_definition(SUCCESS_SCHEMA)),
        (b'{"value": "text"}', _swagger_definition(STRING_FORMAT_SCHEMA)),
    ],
)
def test_response_schema_conformance_invalid_swagger(ctx, swagger_20, content, definition, response_factory):
    response = Response.from_requests(response_factory.requests(content=content), True)
    case = make_case(ctx, swagger_20, definition)
    with pytest.raises(JsonSchemaError):
        response_schema_conformance(check_context(), response, case)
    assert not case.operation.is_valid_response(response)


@pytest.mark.parametrize(
    ("media_type", "content", "definition"),
    [
        (
            "application/json",
            b'{"random": "text"}',
            _oas3_definition(SUCCESS_SCHEMA, status="200"),
        ),
        (
            "application/json",
            b'{"random": "text"}',
            _oas3_definition(SUCCESS_SCHEMA, status="2XX"),
        ),
        (
            "application/json",
            b'{"random": "text"}',
            _oas3_definition(SUCCESS_SCHEMA),
        ),
        (
            "application/json",
            b'{"value": "text"}',
            _oas3_definition(STRING_FORMAT_SCHEMA),
        ),
        (
            "application/json",
            b'{"value": "text"}',
            _oas3_definition(False),
        ),
        (
            "application/problem+json",
            b'{"random": "text"}',
            _oas3_definition(SUCCESS_SCHEMA, media_type="application/problem+json"),
        ),
    ],
)
def test_response_schema_conformance_invalid_openapi(
    ctx, openapi_31, media_type, content, definition, response_factory
):
    response = Response.from_requests(response_factory.requests(content=content, content_type=media_type), True)
    case = make_case(ctx, openapi_31, definition)
    with pytest.raises(AssertionError):
        response_schema_conformance(check_context(), response, case)
    assert not case.operation.is_valid_response(response)


def test_no_schema(ctx, openapi_30, response_factory):
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
    case = make_case(ctx, openapi_30, definition)
    # Then the check should be ignored
    response_schema_conformance(check_context(), response, case)
    assert case.operation.is_valid_response(response)


PROBLEM_JSON_BODY = b'{"detail":"nope","status":400,"title":"Bad Request","type":"about:blank"}'
PROBLEM_JSON_DEFINITION = {
    "responses": {
        "400": {
            "description": "Error",
            "content": {
                "application/json": {
                    "schema": {"type": "object", "properties": {"error": {"type": "string"}}, "required": ["error"]}
                }
            },
        }
    }
}


def test_response_schema_conformance_undocumented_json_subtype(ctx, openapi_30, response_factory):
    # An RFC 7807 body is not described by the schema documented for `application/json`.
    response = Response.from_requests(
        response_factory.requests(status_code=400, content=PROBLEM_JSON_BODY, content_type="application/problem+json"),
        True,
    )
    case = make_case(ctx, openapi_30, PROBLEM_JSON_DEFINITION)
    assert response_schema_conformance(check_context(), response, case) is None


def test_content_type_conformance_undocumented_json_subtype(ctx, openapi_30, response_factory):
    response = Response.from_requests(
        response_factory.requests(status_code=400, content=PROBLEM_JSON_BODY, content_type="application/problem+json"),
        True,
    )
    case = make_case(ctx, openapi_30, PROBLEM_JSON_DEFINITION)
    with pytest.raises(UndefinedContentType, match="Undocumented Content-Type"):
        content_type_conformance(check_context(), response, case)


@pytest.mark.hypothesis_nested
def test_response_schema_conformance_references_invalid(complex_schema, response_factory):
    schema = schemathesis.openapi.from_path(complex_schema)

    @run_given(case=schema["/teapot"]["POST"].as_strategy(), max_examples=3)
    def test(case):
        response = Response.from_requests(response_factory.requests(content=json.dumps({"foo": 1}).encode()), True)
        with pytest.raises(FailureGroup):
            case.validate_response(response)
        assert not case.operation.is_valid_response(response)


@pytest.mark.hypothesis_nested
@pytest.mark.parametrize("value", ["foo", None])
def test_response_schema_conformance_references_valid(complex_schema, value, response_factory):
    schema = schemathesis.openapi.from_path(complex_schema)

    @run_given(case=schema["/teapot"]["POST"].as_strategy(), max_examples=3)
    def test(case):
        response = Response.from_requests(
            response_factory.requests(content=json.dumps({"key": value, "referenced": value}).encode()), True
        )
        case.validate_response(response)


def test_response_schema_conformance_custom_deserializer(ctx, openapi_30, response_factory):
    media_type = "application/vnd.custom"
    definition = _oas3_definition(
        {
            "type": "object",
            "properties": {"name": {"type": "string"}, "count": {"type": "integer", "minimum": 1}},
            "required": ["name", "count"],
        },
        status="200",
        description="OK",
        media_type=media_type,
    )
    case = make_case(ctx, openapi_30, definition)
    # Response content that violates the schema
    response = Response.from_requests(
        response_factory.requests(content=b"name=Alice&count=0", content_type=media_type),
        True,
    )

    assert case.operation.validate_response(response) is None

    @schemathesis.deserializer(media_type)
    def _custom_deserializer(_ctx, http_response):
        text = http_response.content.decode("utf-8")
        result = {}
        for pair in text.split("&"):
            key, value = pair.split("=", 1)
            # Convert count to integer for schema validation
            result[key] = int(value) if key == "count" else value
        return result

    # With deserializer, should fail validation
    with pytest.raises(AssertionError):
        case.operation.validate_response(response)


def test_deduplication(ctx, response_factory):
    # See GH-1394
    operation = ctx.openapi.load_operation(
        "GET /data",
        {
            "responses": {"200": _oas3_response({"type": "integer"}, description="OK")},
        },
    )
    case = operation.Case()
    response = Response.from_requests(response_factory.requests(), True)
    recorder = ScenarioRecorder(label="test")
    recorder.record_case(parent_id=None, case=case, transition=None, is_transition_applied=False)
    recorder.record_response(case_id=case.id, response=response)
    # When there are two checks that raise the same failure
    with pytest.raises(FailureGroup):
        validate_response(
            case=case,
            ctx=check_context(),
            recorder=recorder,
            response=response,
            continue_on_failure=False,
        )
    # Then the resulting output should be deduplicated
    assert (
        len([check for checks in recorder.checks.values() for check in checks if check.failure_info is not None]) == 1
    )


@pytest.fixture(params=["2.0", "3.0"])
def schema_with_optional_headers(ctx, request):
    if request.param == "2.0":
        return ctx.openapi.load_schema(
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
        return ctx.openapi.load_schema(
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
    schema = schema_with_optional_headers
    case = schema["/data"]["GET"].Case()
    response = Response.from_requests(response_factory.requests(), True)
    # Then it should not be reported as missing
    assert response_headers_conformance(check_context(), response, case) is None


INTEGER_HEADER = {"type": "integer", "maximum": 100}
DATETIME_HEADER = {"type": "string", "format": "date-time"}
EXTENT_SCHEMA = {
    "type": "object",
    "properties": {"start": {"type": "integer"}, "end": {"type": "integer"}},
    "required": ["start"],
}
RATE_LIMIT_ROWS = [
    ("X-RateLimit-Limit", INTEGER_HEADER, "42", True, "integer-valid"),
    ("X-RateLimit-Limit", INTEGER_HEADER, "150", False, "integer-above-maximum"),
    ("X-RateLimit-Reset", DATETIME_HEADER, "2021-01-01T00:00:00Z", True, "date-time-valid"),
    ("X-RateLimit-Reset", DATETIME_HEADER, "Invalid", False, "date-time-invalid"),
]


@pytest.mark.parametrize(
    ("version", "name", "definition", "value", "expected"),
    [
        *(
            pytest.param(
                version,
                name,
                {"description": "Header", **({"schema": schema} if version == "3.0.2" else schema)},
                value,
                expected,
                id=f"{case_id}-{version}",
            )
            for version in ("2.0", "3.0.2")
            for name, schema, value, expected, case_id in RATE_LIMIT_ROWS
        ),
        pytest.param("3.1.0", "X-Count", {"schema": {"type": ["integer", "null"]}}, "5", True, id="type-list-integer"),
        pytest.param("3.1.0", "X-Count", {"schema": {"type": ["integer", "null"]}}, "null", True, id="type-list-null"),
        pytest.param(
            "3.1.0", "X-Count", {"schema": {"type": ["integer", "null"]}}, "five", False, id="type-list-invalid"
        ),
        pytest.param("3.1.0", "X-Count", {"schema": {"type": "integer"}}, "1_0", False, id="underscore-integer"),
        pytest.param("3.1.0", "X-Count", {"schema": {"type": "number"}}, "1_0.5", False, id="underscore-number"),
        pytest.param(
            "3.1.0",
            "X-Count",
            {"schema": {"type": ["array", "null"], "items": {"type": "integer"}}},
            "1,2",
            True,
            id="type-list-array",
        ),
        pytest.param(
            "3.1.0",
            "X-Count",
            {"schema": {"type": ["array", "null"], "items": {"type": "integer"}}},
            "1,x",
            False,
            id="type-list-array-invalid",
        ),
        pytest.param(
            "3.0.2", "X-Extent", {"schema": EXTENT_SCHEMA, "explode": True}, "start=-5,end=7", True, id="exploded"
        ),
        pytest.param(
            "3.0.2",
            "X-Extent",
            {"schema": EXTENT_SCHEMA, "explode": False},
            "start,-5,end,7",
            True,
            id="not-exploded",
        ),
        pytest.param("3.0.2", "X-Extent", {"schema": EXTENT_SCHEMA}, "start,-5,end,7", True, id="default-not-exploded"),
        pytest.param(
            "3.0.2",
            "X-Extent",
            {"schema": EXTENT_SCHEMA, "explode": True},
            "start=x,end=7",
            False,
            id="exploded-invalid-value",
        ),
        pytest.param(
            "3.0.2",
            "X-Extent",
            {"schema": EXTENT_SCHEMA, "explode": True},
            "end=7",
            False,
            id="exploded-missing-required",
        ),
        pytest.param(
            "3.0.2", "X-Extent", {"schema": EXTENT_SCHEMA, "explode": True}, "start", False, id="exploded-malformed"
        ),
        pytest.param(
            "3.0.2",
            "X-Extent",
            {"schema": EXTENT_SCHEMA, "explode": False},
            "start,-5,end",
            False,
            id="not-exploded-odd-items",
        ),
    ],
)
def test_header_conformance(ctx, response_factory, version, name, definition, value, expected):
    operation = ctx.openapi.load_operation(
        "GET /data", {"responses": {"200": {"description": "OK", "headers": {name: definition}}}}, version=version
    )
    case = operation.Case()
    response = response_factory.core(headers={name: [value]})
    if expected:
        assert response_headers_conformance(check_context(), response, case) is None
    else:
        with pytest.raises(AssertionError, match="Response header does not conform to the schema"):
            response_headers_conformance(check_context(), response, case)


def test_header_conformance_definition_behind_ref(ctx, response_factory):
    schema = ctx.openapi.load_schema(
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
    case = schema["/data"]["GET"].Case()
    response = Response.from_requests(response_factory.requests(headers={"Link": "Test"}), True)
    with pytest.raises(AssertionError, match="Response header does not conform to the schema"):
        response_headers_conformance(check_context(), response, case)


def test_header_conformance_schema_behind_ref(ctx, response_factory):
    schema = ctx.openapi.load_schema(
        {
            "/data": {
                "get": {
                    "responses": {
                        "200": {
                            "description": "OK",
                            "headers": {
                                "X-RateLimit-Limit": {
                                    "description": "Header",
                                    "schema": {"$ref": "#/components/schemas/IntegerLimit"},
                                }
                            },
                        }
                    },
                }
            },
        },
        components={
            "schemas": {
                "IntegerLimit": {"type": "integer", "maximum": 100},
            },
        },
    )
    case = schema["/data"]["GET"].Case()

    response = Response.from_requests(response_factory.requests(headers={"X-RateLimit-Limit": "50"}), True)
    assert response_headers_conformance(check_context(), response, case) is None

    response = Response.from_requests(response_factory.requests(headers={"X-RateLimit-Limit": "150"}), True)
    with pytest.raises(AssertionError, match="Response header does not conform to the schema"):
        response_headers_conformance(check_context(), response, case)


def test_header_conformance_no_headers_defined(ctx, response_factory):
    schema = ctx.openapi.load_schema(
        {
            "/data": {
                "get": {
                    "responses": {
                        "200": {
                            "description": "OK",
                            "content": {"application/json": {"schema": {"type": "object"}}},
                        }
                    },
                }
            },
        }
    )
    case = schema["/data"]["GET"].Case()
    response = Response.from_requests(response_factory.requests(), True)

    assert response_headers_conformance(check_context(), response, case) is None


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
    schema = ctx.openapi.load_schema(MULTIPLE_HEADERS)
    case = schema["/data"]["GET"].Case()
    response = Response.from_requests(
        response_factory.requests(headers={"X-RateLimit-Limit": "150", "X-RateLimit-Reset": "Invalid"}), True
    )
    with pytest.raises(FailureGroup) as exc:
        response_headers_conformance(check_context(), response, case)
    assert (
        str(exc.value.exceptions[0])
        == """Response header does not conform to the schema

150 is greater than the maximum of 100

Schema:

    {
        "maximum": 100,
        "type": "integer"
    }

Value:

    150"""
    )
    assert (
        str(exc.value.exceptions[1])
        == """Response header does not conform to the schema

"Invalid" is not a "date-time"

Schema:

    {
        "format": "date-time",
        "type": "string"
    }

Value:

    "Invalid\""""
    )


def test_header_conformance_missing_and_invalid(ctx, response_factory):
    schema = ctx.openapi.load_schema(MULTIPLE_HEADERS)
    case = schema["/data"]["GET"].Case()
    response = Response.from_requests(response_factory.requests(headers={"X-RateLimit-Limit": "150"}), True)
    with pytest.raises(FailureGroup) as exc:
        response_headers_conformance(check_context(), response, case)
    assert (
        str(exc.value.exceptions[0])
        == """Response header does not conform to the schema

150 is greater than the maximum of 100

Schema:

    {
        "maximum": 100,
        "type": "integer"
    }

Value:

    150"""
    )
    assert (
        str(exc.value.exceptions[1])
        == """Missing required response headers

The following required headers are missing from the response:
- `X-RateLimit-Reset`"""
    )


@pytest.mark.parametrize(
    ("value", "schema", "explode", "expected"),
    [
        # String type
        ("test", {"type": "string"}, False, "test"),
        ("123", {"type": "string"}, False, "123"),
        # Integer type
        ("123", {"type": "integer"}, False, 123),
        ("-456", {"type": "integer"}, False, -456),
        ("12.34", {"type": "integer"}, False, "12.34"),  # Non-integer string
        ("abc", {"type": "integer"}, False, "abc"),  # Non-numeric string
        # Number type
        ("123.45", {"type": "number"}, False, 123.45),
        ("-67.89", {"type": "number"}, False, -67.89),
        ("123", {"type": "number"}, False, 123.0),
        ("abc", {"type": "number"}, False, "abc"),  # Non-numeric string
        # Null type
        ("null", {"type": "null"}, False, None),
        ("NULL", {"type": "null"}, False, None),
        ("Null", {"type": "null"}, False, None),
        ("not null", {"type": "null"}, False, "not null"),
        # Boolean type
        ("true", {"type": "boolean"}, False, True),
        ("false", {"type": "boolean"}, False, False),
        ("1", {"type": "boolean"}, False, True),
        ("0", {"type": "boolean"}, False, False),
        # Array type — Swagger 2.0 collectionFormat splits the wire value into items.
        ("a,b,c", {"type": "array", "items": {"type": "string"}}, False, ["a", "b", "c"]),
        (
            "1,2,3",
            {"type": "array", "items": {"type": "integer"}, "collectionFormat": "csv"},
            False,
            [1, 2, 3],
        ),
        ("a b c", {"type": "array", "items": {"type": "string"}, "collectionFormat": "ssv"}, False, ["a", "b", "c"]),
        ("a,b", {"type": "array", "items": {"type": "string"}, "collectionFormat": "multi"}, False, "a,b"),
        ("123", {"type": ["string", "integer"]}, False, "123"),
        ("a,1,b,2", {"type": "object", "properties": {"a": {"type": "integer"}}}, False, {"a": 1, "b": "2"}),
        (
            "a,1,b,2",
            {"type": "object", "properties": {"a": {"type": "integer"}}, "additionalProperties": {"type": "integer"}},
            False,
            {"a": 1, "b": 2},
        ),
        # Unsupported type
        ("test", {"type": "object"}, False, "test"),
        # No type specified
        ("test", {}, False, "test"),
        (
            "a=1,b=x=y",
            {"type": "object", "properties": {"a": {"type": "integer"}, "b": {"type": "string"}}},
            True,
            {"a": 1, "b": "x=y"},
        ),
    ],
)
def test_coerce_header_value(value, schema, explode, expected):
    assert _coerce_header_value(value, schema, explode=explode) == expected


def test_module_access():
    # It is done via `__getattr__`
    _ = schemathesis.checks.negative_data_rejection
    assert "negative_data_rejection" in dir(schemathesis.checks)


def test_response_schema_conformance_reports_all_errors(ctx, response_factory):
    schema = ctx.openapi.load_schema(
        {
            "/data": {
                "get": {
                    "responses": {
                        "200": {
                            "description": "OK",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {
                                            "id": {"type": "integer"},
                                            "name": {"type": "string"},
                                        },
                                        "required": ["id", "name"],
                                    }
                                }
                            },
                        }
                    }
                }
            }
        }
    )
    case = schema["/data"]["GET"].Case()
    response = Response.from_requests(
        response_factory.requests(content=b'{"id": "not-an-int", "name": 42}'),
        True,
    )
    with pytest.raises(FailureGroup) as exc_info:
        response_schema_conformance(check_context(), response, case)
    failures = exc_info.value.exceptions
    assert len(failures) == 2
    assert all(isinstance(f, JsonSchemaError) for f in failures)
    schema_paths = {"/".join(str(s) for s in f.schema_path) for f in failures}
    assert schema_paths == {"properties/id/type", "properties/name/type"}


def test_response_schema_conformance_deduplicates_same_schema_path(ctx, response_factory):
    schema = ctx.openapi.load_schema(
        {
            "/data": {
                "get": {
                    "responses": {
                        "200": {
                            "description": "OK",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "array",
                                        "items": {"type": "string"},
                                    }
                                }
                            },
                        }
                    }
                }
            }
        }
    )
    case = schema["/data"]["GET"].Case()
    # All three items violate the same schema_path (items/type) — dedup collapses to one error
    response = Response.from_requests(
        response_factory.requests(content=b"[1, 2, 3]"),
        True,
    )
    with pytest.raises(JsonSchemaError) as exc_info:
        response_schema_conformance(check_context(), response, case)
    assert exc_info.value.schema_path == ["items", "type"]


def test_header_conformance_reports_multiple_errors_from_one_header(ctx, response_factory):
    schema = ctx.openapi.load_schema(
        {
            "/data": {
                "get": {
                    "responses": {
                        "200": {
                            "description": "OK",
                            "headers": {"X-Rate-Limit": {"schema": {"type": "integer", "minimum": 100, "maximum": 50}}},
                        }
                    }
                }
            }
        }
    )
    case = schema["/data"]["GET"].Case()
    # 75 is both less than minimum=100 and greater than maximum=50 — two distinct violations
    response = Response.from_requests(
        response_factory.requests(headers={"X-Rate-Limit": "75"}),
        True,
    )
    with pytest.raises(FailureGroup) as exc_info:
        response_headers_conformance(check_context(), response, case)
    failures = exc_info.value.exceptions
    assert len(failures) == 2
    assert all(isinstance(f, JsonSchemaError) for f in failures)
    schema_paths = {"/".join(str(s) for s in f.schema_path) for f in failures}
    assert schema_paths == {"minimum", "maximum"}


def test_use_after_free_no_false_positive_on_idempotent_delete(ctx, response_factory):
    # RFC 7231 §4.3.5: a repeated DELETE may legitimately return 200/204 instead of 404.
    schema = ctx.openapi.load_schema(
        {
            "/users": {
                "post": {
                    "requestBody": {"content": {"application/json": {"schema": {"type": "object"}}}},
                    "responses": {"201": {"description": "Created"}},
                },
            },
            "/users/{userId}": {
                "delete": {
                    "parameters": [{"name": "userId", "in": "path", "required": True, "schema": {"type": "integer"}}],
                    "responses": {"204": {"description": "No Content"}, "404": {"description": "Not Found"}},
                }
            },
        }
    )
    post_op = schema["/users"]["POST"]
    delete_op = schema["/users/{userId}"]["DELETE"]

    parent = post_op.Case(method="POST", body={}, media_type="application/json")
    first_delete = delete_op.Case(method="DELETE", path_parameters={"userId": 1})
    second_delete = delete_op.Case(method="DELETE", path_parameters={"userId": 1})

    recorder = ScenarioRecorder(label="use-after-free-test")
    recorder.record_case(parent_id=None, case=parent, transition=None, is_transition_applied=False)
    recorder.record_response(
        case_id=parent.id,
        response=Response.from_requests(response_factory.requests(status_code=201), True),
    )
    recorder.record_case(parent_id=parent.id, case=first_delete, transition=None, is_transition_applied=False)
    recorder.record_response(
        case_id=first_delete.id,
        response=Response.from_requests(response_factory.requests(status_code=204), True),
    )
    recorder.record_case(parent_id=parent.id, case=second_delete, transition=None, is_transition_applied=False)
    second_response = Response.from_requests(response_factory.requests(status_code=200), True)
    recorder.record_response(case_id=second_delete.id, response=second_response)

    check_ctx = check_context(recorder=recorder)

    assert use_after_free(check_ctx, second_response, second_delete) is None


def test_use_after_free_failure_references_prior_delete(ctx, response_factory):
    schema = ctx.openapi.load_schema(
        {
            "/users": {
                "post": {
                    "requestBody": {"content": {"application/json": {"schema": {"type": "object"}}}},
                    "responses": {"201": {"description": "Created"}},
                },
            },
            "/users/{userId}": {
                "get": {
                    "parameters": [{"name": "userId", "in": "path", "required": True, "schema": {"type": "integer"}}],
                    "responses": {"200": {"description": "OK"}, "404": {"description": "Not Found"}},
                },
                "delete": {
                    "parameters": [{"name": "userId", "in": "path", "required": True, "schema": {"type": "integer"}}],
                    "responses": {"204": {"description": "No Content"}, "404": {"description": "Not Found"}},
                },
            },
        }
    )
    post_op = schema["/users"]["POST"]
    get_op = schema["/users/{userId}"]["GET"]
    delete_op = schema["/users/{userId}"]["DELETE"]

    parent = post_op.Case(method="POST", body={}, media_type="application/json")
    sibling_delete = delete_op.Case(method="DELETE", path_parameters={"userId": 1})
    current_get = get_op.Case(method="GET", path_parameters={"userId": 1})

    recorder = ScenarioRecorder(label="sibling-curl-test")
    recorder.record_case(parent_id=None, case=parent, transition=None, is_transition_applied=False)
    recorder.record_response(
        case_id=parent.id,
        response=Response.from_requests(response_factory.requests(status_code=201), True),
    )
    recorder.record_case(parent_id=parent.id, case=sibling_delete, transition=None, is_transition_applied=False)
    recorder.record_response(
        case_id=sibling_delete.id,
        response=Response.from_requests(response_factory.requests(status_code=204), True),
    )
    recorder.record_case(parent_id=parent.id, case=current_get, transition=None, is_transition_applied=False)
    get_response = Response.from_requests(response_factory.requests(status_code=200), True)
    recorder.record_response(case_id=current_get.id, response=get_response)

    check_ctx = check_context(recorder=recorder)

    with pytest.raises(UseAfterFree) as exc_info:
        use_after_free(check_ctx, get_response, current_get)

    assert exc_info.value.deleted_case_id == sibling_delete.id


def test_use_after_free_no_false_positive_on_collection_delete(ctx, response_factory):
    # DELETE on a bare collection (no path parameters) does not free a specific resource;
    # a follow-up GET on the same collection returning 200 is a list read, not a use-after-free.
    schema = ctx.openapi.load_schema(
        {
            "/locations": {
                "post": {
                    "requestBody": {"content": {"application/json": {"schema": {"type": "object"}}}},
                    "responses": {"201": {"description": "Created"}},
                },
                "get": {"responses": {"200": {"description": "OK"}}},
                "delete": {"responses": {"200": {"description": "OK"}, "204": {"description": "No Content"}}},
            },
        }
    )
    post_op = schema["/locations"]["POST"]
    get_op = schema["/locations"]["GET"]
    delete_op = schema["/locations"]["DELETE"]

    parent = post_op.Case(method="POST", body={}, media_type="application/json")
    collection_delete = delete_op.Case(method="DELETE")
    current_get = get_op.Case(method="GET")

    recorder = ScenarioRecorder(label="collection-delete-test")
    recorder.record_case(parent_id=None, case=parent, transition=None, is_transition_applied=False)
    recorder.record_response(
        case_id=parent.id,
        response=Response.from_requests(response_factory.requests(status_code=201), True),
    )
    recorder.record_case(parent_id=parent.id, case=collection_delete, transition=None, is_transition_applied=False)
    recorder.record_response(
        case_id=collection_delete.id,
        response=Response.from_requests(response_factory.requests(status_code=204), True),
    )
    recorder.record_case(parent_id=parent.id, case=current_get, transition=None, is_transition_applied=False)
    get_response = Response.from_requests(response_factory.requests(status_code=200), True)
    recorder.record_response(case_id=current_get.id, response=get_response)

    check_ctx = check_context(recorder=recorder)

    assert use_after_free(check_ctx, get_response, current_get) is None


@pytest.mark.parametrize(("deleted_case_id", "expected"), [(None, ()), ("abc", ("abc",))], ids=["unknown", "known"])
def test_use_after_free_related_case_ids(deleted_case_id, expected):
    failure = UseAfterFree(
        operation="GET /users/{id}",
        message="Used after delete",
        free="DELETE /users/{id}",
        usage="GET /users/{id}",
        deleted_case_id=deleted_case_id,
    )

    assert failure.related_case_ids() == expected


def test_iter_chain_cases_includes_referenced_sibling(ctx, response_factory):
    schema = ctx.openapi.load_schema(
        {
            "/users": {
                "post": {
                    "requestBody": {"content": {"application/json": {"schema": {"type": "object"}}}},
                    "responses": {"201": {"description": "Created"}},
                },
            },
            "/users/{userId}": {
                "get": {
                    "parameters": [{"name": "userId", "in": "path", "required": True, "schema": {"type": "integer"}}],
                    "responses": {"200": {"description": "OK"}},
                },
                "delete": {
                    "parameters": [{"name": "userId", "in": "path", "required": True, "schema": {"type": "integer"}}],
                    "responses": {"204": {"description": "No Content"}},
                },
            },
        }
    )
    post_op = schema["/users"]["POST"]
    get_op = schema["/users/{userId}"]["GET"]
    delete_op = schema["/users/{userId}"]["DELETE"]

    parent = post_op.Case(method="POST", body={}, media_type="application/json")
    sibling_delete = delete_op.Case(method="DELETE", path_parameters={"userId": 1})
    current_get = get_op.Case(method="GET", path_parameters={"userId": 1})

    recorder = ScenarioRecorder(label="chain-helper-test")
    for c in (parent, sibling_delete, current_get):
        recorder.record_case(
            parent_id=parent.id if c is not parent else None,
            case=c,
            transition=None,
            is_transition_applied=False,
        )

    chain = list(recorder.iter_chain_cases(case_id=current_get.id, related_case_ids=(sibling_delete.id,)))
    assert [c.id for c in chain] == [parent.id, sibling_delete.id, current_get.id]


_RESPONSES_WITH_UNRESOLVABLE_404 = {
    "200": {"description": "OK", "content": {"application/json": {"schema": {"type": "object"}}}},
    "404": {
        "description": "Not Found",
        "content": {"*/*": {"schema": {"$ref": "#/components/schemas/Missing"}}},
    },
}


@pytest.mark.parametrize(
    ("status_code", "content", "raises"),
    [(404, b'{"detail": "nope"}', False), (200, b"[]", True)],
    ids=["unresolvable-is-skipped", "sibling-still-validates"],
)
def test_response_schema_conformance_with_unresolvable_reference(ctx, response_factory, status_code, content, raises):
    schema = ctx.openapi.load_schema({"/data": {"get": {"responses": _RESPONSES_WITH_UNRESOLVABLE_404}}})
    case = schema["/data"]["GET"].Case()
    response = Response.from_requests(response_factory.requests(status_code=status_code, content=content), True)

    if raises:
        with pytest.raises(JsonSchemaError):
            response_schema_conformance(check_context(), response, case)
    else:
        assert response_schema_conformance(check_context(), response, case) is None


@pytest.mark.parametrize(("value", "is_valid"), [("42", True), ("not-an-integer", False)])
def test_response_headers_conformance_skips_unresolvable_reference(ctx, response_factory, value, is_valid):
    schema = ctx.openapi.load_schema(
        {
            "/data": {
                "get": {
                    "responses": {
                        "200": {
                            "description": "OK",
                            "headers": {
                                "X-Total": {"schema": {"$ref": "#/components/schemas/Missing"}},
                                "X-Limit": {"schema": {"type": "integer"}},
                            },
                        }
                    }
                }
            }
        }
    )
    case = schema["/data"]["GET"].Case()
    response = Response.from_requests(
        response_factory.requests(headers={"X-Total": "anything", "X-Limit": value}), True
    )

    if is_valid:
        assert response_headers_conformance(check_context(), response, case) is None
    else:
        with pytest.raises(AssertionError, match="Response header does not conform to the schema"):
            response_headers_conformance(check_context(), response, case)


def test_missing_headers_identity_ignores_message():
    # The same missing headers are one finding however the message is worded.
    assert MissingHeaders(
        operation="GET /users", missing_headers=["X-Rate-Limit"], message="one wording"
    ) == MissingHeaders(operation="GET /users", missing_headers=["X-Rate-Limit"], message="another wording")


def test_malformed_media_type_identity_ignores_message():
    assert MalformedMediaType(
        operation="GET /users", actual="application.json", defined="application/json", message="one wording"
    ) == MalformedMediaType(
        operation="GET /users", actual="application.json", defined="application/json", message="another wording"
    )


def test_discriminator_mismatch_identity_ignores_value():
    assert DiscriminatorMismatch(operation="GET /pets", message="`kind` is 'bird'") == DiscriminatorMismatch(
        operation="GET /pets", message="`kind` is 'fish'"
    )


def test_no_failure_class_keys_on_its_message():
    # A message-derived key changes whenever wording improves, which a stored key cannot survive.
    schemathesis.checks.load_all_checks()

    assert [cls.__name__ for cls in FAILURE_CLASSES if cls._unique_key is Failure._unique_key] == []
