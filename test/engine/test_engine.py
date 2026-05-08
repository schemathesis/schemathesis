from __future__ import annotations

import platform
from dataclasses import asdict
from unittest.mock import ANY

import pytest
from fastapi import FastAPI
from py import sys

import schemathesis
from schemathesis.checks import not_a_server_error
from schemathesis.config import SchemathesisWarning
from schemathesis.core import SCHEMATHESIS_TEST_CASE_HEADER
from schemathesis.core.transport import USER_AGENT
from schemathesis.engine import Status, StopReason, events, from_schema
from schemathesis.engine.recorder import Request
from schemathesis.engine.run import PhaseName
from schemathesis.generation import GenerationMode
from schemathesis.generation.hypothesis.builder import add_examples
from schemathesis.specs.openapi.checks import (
    content_type_conformance,
    response_schema_conformance,
    status_code_conformance,
)
from test.utils import EventStream

IS_PYPY = platform.python_implementation() == "PyPy"


def execute(schema, **options) -> EventStream:
    return EventStream(schema, **options).execute()


def _api_requests(api):
    return [request for request in api.requests if request.path.startswith("/api/")]


def _scenario(stream, **attrs):
    return stream.find(events.ScenarioFinished, **attrs)


def _last_scenario(stream):
    return stream.find_all(events.ScenarioFinished)[-1]


def _scenario_interactions(stream, **attrs):
    return list(_scenario(stream, **attrs).recorder.interactions.values())


def _scenario_cases(stream, **attrs):
    return list(_scenario(stream, **attrs).recorder.cases.values())


def _all_scenario_cases(stream):
    return [case for scenario in stream.find_all(events.ScenarioFinished) for case in scenario.recorder.cases.values()]


def _scenario_checks(scenario):
    return [check for checks in scenario.recorder.checks.values() for check in checks]


def _last_scenario_checks(stream):
    return _scenario_checks(_last_scenario(stream))


def test_execute_base_url_not_found(ctx):
    api = ctx.openapi.apps.success_and_failure()
    # When base URL is pointing to an unknown location
    schema = schemathesis.openapi.from_url(api.schema_url)
    schema.config.update(base_url=f"{api.base_url}/404/")
    EventStream(schema).execute()
    # Then the engine should use this base
    # And they will not reach the application
    assert _api_requests(api) == []


def test_execute(ctx):
    api = ctx.openapi.apps.success_and_failure()
    schema = schemathesis.openapi.from_url(api.schema_url)
    EventStream(schema).execute()

    # Filter out the engine's `/` capability probe; only assert against the API operations.
    api_calls = _api_requests(api)
    assert sorted(r.path for r in api_calls) == ["/api/failure", "/api/success"]
    for request in api_calls:
        assert request.method == "GET"
        assert request.headers["User-Agent"] == USER_AGENT


@pytest.mark.parametrize("workers", [1, 2])
def test_interactions(ctx, workers):
    api = ctx.openapi.apps.success_and_failure()
    schema = schemathesis.openapi.from_url(api.schema_url)
    stream = EventStream(schema, workers=workers).execute()

    if sys.version_info >= (3, 14):
        encoding = ["gzip, deflate, zstd"]
    else:
        encoding = ["gzip, deflate"]

    # failure
    interactions = _scenario_interactions(stream, status=Status.FAILURE)
    assert len(interactions) == 1
    failure = interactions[0]
    assert asdict(failure.request) == {
        "uri": f"{api.base_url}/api/failure",
        "method": "GET",
        "body": None,
        "body_size": None,
        "headers": {
            "Accept": ["*/*"],
            "Accept-Encoding": encoding,
            "Connection": ["keep-alive"],
            "User-Agent": [USER_AGENT],
            SCHEMATHESIS_TEST_CASE_HEADER: [ANY],
        },
    }
    assert failure.response.status_code == 500
    # success
    interactions = _scenario_interactions(stream, status=Status.SUCCESS)
    assert len(interactions) == 1
    success = interactions[0]
    assert asdict(success.request) == {
        "uri": f"{api.base_url}/api/success",
        "method": "GET",
        "body": None,
        "body_size": None,
        "headers": {
            "Accept": ["*/*"],
            "Accept-Encoding": encoding,
            "Connection": ["keep-alive"],
            "User-Agent": [USER_AGENT],
            SCHEMATHESIS_TEST_CASE_HEADER: [ANY],
        },
    }
    assert success.response.status_code == 200
    assert success.response.json() == {"success": True}
    assert success.response.headers["content-type"] == ["application/json"]


def test_asgi_interactions():
    app = FastAPI()

    @app.get("/users")
    async def users():
        return {"success": True}

    schema = schemathesis.openapi.from_asgi("/openapi.json", app)
    stream = EventStream(schema).execute()
    interactions = stream.find_all_interactions()
    assert interactions[0].request.uri == "http://localhost/users"


def test_empty_response_interaction(ctx):
    api = ctx.openapi.apps.empty()
    schema = schemathesis.openapi.from_url(api.schema_url)
    # When there is a GET request and a response that doesn't return content (e.g. 204)
    stream = EventStream(schema).execute()
    interactions = _scenario_interactions(stream)
    for interaction in interactions:  # There could be multiple calls
        # Then the stored request has no body
        assert interaction.request.body is None
        # And response encoding is missing
        assert interaction.response.encoding is None


def test_empty_string_response_interaction(ctx):
    api = ctx.openapi.apps.empty_string()
    schema = schemathesis.openapi.from_url(api.schema_url)
    # When there is a response that returns payload of length 0
    stream = EventStream(schema).execute()
    interactions = _scenario_interactions(stream)
    for interaction in interactions:  # There could be multiple calls
        # Then the stored response body should be an empty string
        assert interaction.response.content == b""
        assert interaction.response.encoding == "utf-8"


def test_auth(ctx):
    api = ctx.openapi.apps.success_and_failure()
    schema = schemathesis.openapi.from_url(api.schema_url)
    # When auth is specified as a tuple of 2 strings
    execute(schema, auth=("test", "test"))

    # Then each API request should contain corresponding basic auth header
    api_calls = _api_requests(api)
    assert sorted(r.path for r in api_calls) == ["/api/failure", "/api/success"]
    for request in api_calls:
        assert request.headers["Authorization"] == "Basic dGVzdDp0ZXN0"


@pytest.mark.parametrize("converter", [lambda x: x, lambda x: x + "/"])
def test_base_url(ctx, converter):
    api = ctx.openapi.apps.success_and_failure()
    base_url = converter(api.base_url)
    # When `base_url` is specified explicitly with or without trailing slash
    schema = schemathesis.openapi.from_url(api.schema_url)
    schema.config.update(base_url=base_url)
    execute(schema)

    # Then each request should reach the app in both cases
    api_calls = _api_requests(api)
    assert sorted(r.path for r in api_calls) == ["/api/failure", "/api/success"]


def test_root_url():
    app = FastAPI(
        title="Silly",
        version="1.0.0",
    )

    @app.get("/")
    def empty():
        return {}

    def check(ctx, response, case):
        assert case.as_transport_kwargs()["url"] == "/"
        assert response.status_code == 200

    schema = schemathesis.openapi.from_asgi("/openapi.json", app=app)
    stream = execute(schema, checks=(check,))
    stream.assert_no_failures()


def test_execute_with_headers(ctx):
    api = ctx.openapi.apps.success_and_failure()
    schema = schemathesis.openapi.from_url(api.schema_url)
    # When headers are specified for the `execute` call
    headers = {"Authorization": "Bearer 123"}
    execute(schema, headers=headers)

    # Then each API request should contain these headers
    api_calls = _api_requests(api)
    assert sorted(r.path for r in api_calls) == ["/api/failure", "/api/success"]
    for request in api_calls:
        assert request.headers["Authorization"] == "Bearer 123"


def test_execute_filter_endpoint(ctx):
    api = ctx.openapi.apps.success_and_failure()
    schema = schemathesis.openapi.from_url(api.schema_url).include(path_regex="success")
    # When `endpoint` is passed in the `execute` call
    execute(schema)

    # Then the engine will make calls only to the specified path
    api_calls = _api_requests(api)
    assert [(r.method, r.path) for r in api_calls] == [("GET", "/api/success")]


def test_execute_filter_method(ctx):
    api = ctx.openapi.apps.success_and_failure()
    schema = schemathesis.openapi.from_url(api.schema_url).include(method="POST")
    # When `method` corresponds to a method that is not defined in the app schema
    execute(schema)
    # Then engine will not make any requests
    assert _api_requests(api) == []


def test_form_data(ctx):
    api = ctx.openapi.apps.multipart()
    schema = schemathesis.openapi.from_url(api.schema_url)

    def is_ok(ctx, response, case):
        assert response.status_code == 200

    def check_content(ctx, response, case):
        data = response.json()
        assert isinstance(data["key"], str)
        assert data["value"].lstrip("-").isdigit()

    # When API operation specifies parameters with `in=formData`
    # Then responses should have 200 status, and not 415 (unsupported media type)
    stream = execute(schema, checks=(is_ok, check_content), max_examples=3)
    # And there should be no errors or failures
    stream.assert_no_errors()
    stream.assert_no_failures()
    multipart_requests = [r for r in api.requests if r.method == "POST" and r.path == "/api/multipart"]
    # And the application should receive 3 requests as specified in `max_examples`
    assert len(multipart_requests) == 3
    # And the Content-Type of incoming requests should be `multipart/form-data`
    assert multipart_requests[0].headers["Content-Type"].startswith("multipart/form-data")


def test_headers_override(ctx):
    api = ctx.openapi.apps.headers()
    schema = schemathesis.openapi.from_url(api.schema_url)

    def check_headers(ctx, response, case):
        data = response.json()
        assert data["X-Token"] == "test"

    stream = EventStream(schema, checks=(check_headers,), headers={"X-Token": "test"}, max_examples=1).execute()
    stream.assert_no_failures()
    stream.assert_no_errors()


def test_unknown_response_code(ctx):
    api = ctx.openapi.apps.teapot()
    schema = schemathesis.openapi.from_url(api.schema_url)
    # When API operation returns a status code, that is not listed in "responses"
    # And "status_code_conformance" is specified
    stream = EventStream(schema, checks=(status_code_conformance,), max_examples=1).execute()

    # Then there should be a failure
    assert stream.failures_count == 1
    check = _last_scenario_checks(stream)[0]
    assert check.name == "status_code_conformance"
    assert check.status == Status.FAILURE
    assert check.failure_info.failure.status_code == 418
    assert check.failure_info.failure.allowed_status_codes == [200]
    assert check.failure_info.failure.defined_status_codes == ["200"]


def test_unknown_response_code_with_default(ctx):
    api = ctx.openapi.apps.failure()
    # When API operation returns a status code, that is not listed in "responses", but there is a "default" response
    # And "status_code_conformance" is specified
    stream = EventStream(
        schemathesis.openapi.from_url(api.schema_url), checks=(status_code_conformance,), max_examples=1
    ).execute()
    # Then there should be no failure
    stream.assert_no_failures()
    check = _last_scenario_checks(stream)[0]
    assert check.name == "status_code_conformance"
    assert check.status == Status.SUCCESS


def test_unknown_content_type(ctx):
    api = ctx.openapi.apps.text()
    schema = schemathesis.openapi.from_url(api.schema_url)
    # When API operation returns a response with content type, not specified in "produces"
    # And "content_type_conformance" is specified
    stream = EventStream(schema, checks=(content_type_conformance,), max_examples=1).execute()
    # Then there should be a failure
    assert stream.failures_count == 1
    check = _last_scenario_checks(stream)[0]
    assert check.name == "content_type_conformance"
    assert check.status == Status.FAILURE
    assert check.failure_info.failure.content_type == "text/plain"
    assert check.failure_info.failure.defined_content_types == ["application/json"]


def test_known_content_type(ctx):
    api = ctx.openapi.apps.success()
    # When API operation returns a response with a proper content type
    # And "content_type_conformance" is specified
    stream = execute(
        schemathesis.openapi.from_url(api.schema_url),
        checks=(content_type_conformance,),
        max_examples=1,
    )
    # Then there should be no failures
    stream.assert_no_failures()


def test_response_conformance_invalid(ctx):
    api = ctx.openapi.apps.invalid_response()
    schema = schemathesis.openapi.from_url(api.schema_url)
    # When API operation returns a response that doesn't conform to the schema
    # And "response_schema_conformance" is specified
    stream = EventStream(
        schema, checks=(response_schema_conformance,), max_examples=1, phases=[PhaseName.FUZZING]
    ).execute()
    # Then there should be a failure
    assert stream.failures_count == 1
    check = _last_scenario_checks(stream)[-1]
    assert check.failure_info.failure.title == "Response violates schema", check
    assert (
        check.failure_info.failure.message
        == """"success" is a required property

Schema:

    {
        "required": [
            "success"
        ],
        "properties": {
            "success": {
                "type": "boolean"
            }
        },
        "type": "object"
    }

Value:

    {
        "random": "key"
    }"""
    )
    assert check.failure_info.failure.instance == {"random": "key"}
    assert check.failure_info.failure.instance_path == []
    assert check.failure_info.failure.schema == {
        "required": ["success"],
        "properties": {"success": {"type": "boolean"}},
        "type": "object",
    }
    assert check.failure_info.failure.schema_path == ["required"]
    assert check.failure_info.failure.validation_message == '"success" is a required property'


def test_response_conformance_valid(ctx):
    api = ctx.openapi.apps.success()
    # When API operation returns a response that conforms to the schema
    # And "response_schema_conformance" is specified
    stream = execute(
        schemathesis.openapi.from_url(api.schema_url), checks=(response_schema_conformance,), max_examples=1
    )
    # Then there should be no failures or errors
    stream.assert_no_failures()
    stream.assert_no_errors()


def test_response_conformance_recursive_valid(ctx):
    api = ctx.openapi.apps.recursive()
    schema = schemathesis.openapi.from_url(api.schema_url)
    # When API operation contains a response that have recursive references
    # And "response_schema_conformance" is specified
    stream = execute(
        schema,
        checks=(response_schema_conformance,),
        max_examples=1,
    )
    # Then there should be no failures or errors
    stream.assert_no_failures()
    stream.assert_no_errors()


def test_response_conformance_text(ctx):
    api = ctx.openapi.apps.text()
    schema = schemathesis.openapi.from_url(api.schema_url)
    # When API operation returns a response that is not JSON
    # And "response_schema_conformance" is specified
    stream = execute(
        schema,
        checks=(response_schema_conformance,),
        max_examples=1,
    )
    # Then the check should be ignored if the response headers are not application/json
    stream.assert_no_failures()
    stream.assert_no_errors()


def test_response_conformance_malformed_json(ctx):
    api = ctx.openapi.apps.malformed_json()
    schema = schemathesis.openapi.from_url(api.schema_url)
    # When API operation returns a response that contains a malformed JSON, but has a valid content type header
    # And "response_schema_conformance" is specified
    stream = EventStream(
        schema,
        checks=(response_schema_conformance,),
        max_examples=1,
    ).execute()
    # Then there should be a failure
    assert stream.failures_count == 1
    stream.assert_no_errors()

    check = _last_scenario_checks(stream)[-1]
    assert check.failure_info.failure.title == "JSON deserialization error"
    if IS_PYPY:
        expected = "Key name must be string at char"
    else:
        expected = "Expecting property name enclosed in double quotes"
    assert check.failure_info.failure.validation_message == expected
    assert check.failure_info.failure.position == 1


@pytest.fixture
def filter_path_parameters():
    # ".." and "." strings are treated specially, but this behavior is outside the test's scope
    # "" shouldn't be allowed as a valid path parameter

    def before_generate_path_parameters(ctx, strategy):
        return strategy.filter(
            lambda x: x["key"] not in ("..", ".", "", "/") and not (isinstance(x["key"], str) and "/" in x["key"])
        )

    schemathesis.hook(before_generate_path_parameters)


@pytest.mark.usefixtures("filter_path_parameters")
def test_path_parameters_encoding(ctx):
    api = ctx.openapi.apps.path_variable()
    schema = schemathesis.openapi.from_url(api.schema_url)
    # NOTE. WSGI and ASGI applications decode %2F as / and returns 404
    # When API operation has a path parameter
    stream = execute(
        schema,
        checks=(status_code_conformance,),
        deterministic=True,
    )
    # Then there should be no failures
    # since all path parameters are quoted
    stream.assert_no_errors()
    stream.assert_no_failures()


def test_exceptions(ctx):
    api = ctx.openapi.apps.slow()
    schema = schemathesis.openapi.from_url(api.schema_url)
    schema.config.update(base_url="http://127.0.0.1:1/")
    stream = execute(schema)
    assert any(event.status == Status.ERROR for event in stream.find_all(events.ScenarioFinished))


def test_internal_exceptions(ctx, mocker):
    # GH: #236
    # When there is an exception during the test
    # And Hypothesis consider this test as a flaky one
    api = ctx.openapi.apps.multipart()
    schema = schemathesis.openapi.from_url(api.schema_url)
    mocker.patch("schemathesis.Case.call", side_effect=ValueError)
    stream = execute(schema, max_examples=3)
    # Then the execution result should indicate errors
    stream.assert_errors()
    # And an error from the buggy code should be collected
    exceptions = [error.value.__class__.__name__ for error in stream.find_all(events.NonFatalError)]
    assert "ValueError" in exceptions
    assert len(exceptions) == 1


def test_payload_explicit_example(ctx):
    api = ctx.openapi.apps.payload()
    schema = schemathesis.openapi.from_url(api.schema_url)
    # When API operation has an example specified
    stream = execute(schema)
    # Then run should be successful
    stream.assert_no_errors()
    stream.assert_no_failures()

    payload_requests = [r for r in api.requests if r.method == "POST" and r.path == "/api/payload"]
    # And this example should be sent to the app
    assert payload_requests[0].json() == {"name": "John"}


def test_explicit_examples_from_response(ctx):
    api = ctx.openapi.apps.success()
    schema = ctx.openapi.load_schema(
        {
            "/items/{itemId}/": {
                "get": {
                    "parameters": [{"name": "itemId", "in": "path", "required": True, "schema": {"type": "string"}}],
                    "responses": {
                        "200": {
                            "description": "",
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/Item"},
                                    "examples": {
                                        "Example1": {"value": {"id": "123456"}},
                                        "Example2": {"value": {"itemId": "456789"}},
                                    },
                                }
                            },
                        }
                    },
                }
            }
        },
        components={"schemas": {"Item": {"properties": {"id": {"type": "string"}}}}},
    )
    schema.config.update(base_url=f"{api.base_url}/api")
    stream = EventStream(schema, max_examples=1, phases=[PhaseName.EXAMPLES]).execute()
    assert [case.value.path_parameters for case in _scenario_cases(stream)] == [
        {"itemId": "456789"},
        {"itemId": "123456"},
    ]


def test_explicit_example_disable(ctx, mocker):
    api = ctx.openapi.apps.payload()
    schema = schemathesis.openapi.from_url(api.schema_url)
    # When API operation has an example specified
    # And the `explicit` phase is excluded
    spy = mocker.patch("schemathesis.generation.hypothesis.builder.add_examples", wraps=add_examples)
    stream = execute(
        schema,
        max_examples=1,
        phases=[PhaseName.FUZZING],
    )
    # Then run should be successful
    stream.assert_no_errors()
    stream.assert_no_failures()
    payload_requests = [r for r in api.requests if r.method == "POST" and r.path == "/api/payload"]
    assert len(payload_requests) == 1

    # And this example should NOT be used
    assert payload_requests[0].json() != {"name": "John"}
    # And examples are not evaluated at all
    assert not spy.called


def test_plain_text_body(ctx):
    api = ctx.openapi.apps.plain_text_body()
    schema = schemathesis.openapi.from_url(api.schema_url)

    # When the expected payload is text/plain
    # Then the payload is not encoded as JSON
    def check_content(ctx, response, case):
        data = response.content
        assert case.body.encode("utf8") == data

    stream = execute(schema, checks=(check_content,), max_examples=3)
    stream.assert_no_errors()
    stream.assert_no_failures()


def test_invalid_path_parameter(ctx):
    api = ctx.openapi.apps.invalid_path_parameter()
    # When a path parameter is marked as not required
    # And schema validation is disabled
    schema = schemathesis.openapi.from_url(api.schema_url)
    stream = execute(schema, max_examples=3)
    # Then Schemathesis enforces all path parameters to be required
    # And there should be no errors
    stream.assert_no_errors()


def test_missing_path_parameter(ctx):
    api = ctx.openapi.apps.missing_path_parameter()
    schema = schemathesis.openapi.from_url(api.schema_url)
    # When a path parameter is missing
    stream = EventStream(schema, max_examples=3).execute()
    # Then it leads to an error
    stream.assert_errors()
    assert "Path parameter 'id' is not defined" in str(stream.find(events.NonFatalError).info)
    # And tests still should be executed
    event = _last_scenario(stream)
    assert len(event.recorder.cases) > 0


def test_max_failures(ctx):
    api = ctx.openapi.apps.failure_multiple_failures_unsatisfiable()
    schema = schemathesis.openapi.from_url(api.schema_url)
    # When `max_failures` is specified
    stream = execute(schema, max_failures=2, phases=[PhaseName.FUZZING])
    # Then the total numbers of failures and errors should not exceed this number
    assert stream.failures_count <= 2
    errors = stream.find_all(events.NonFatalError)
    assert stream.failures_count + len(errors) == 2
    assert stream.finished.stop_reason == StopReason.FAILURE_LIMIT


@pytest.mark.skipif(platform.system() == "Windows", reason="Fails on Windows due to recursion")
def test_skip_operations_with_recursive_references(ctx, schema_with_recursive_references):
    # When the test schema contains recursive references
    schema = ctx.openapi.from_full_schema(schema_with_recursive_references)
    schema.config.generation.update(modes=[GenerationMode.POSITIVE])
    stream = EventStream(schema).execute()
    # Then it causes an error with a proper error message
    stream.assert_after_execution_status(Status.ERROR)
    assert "Schema `#/components/schemas/Node` has a required reference to itself" in str(
        stream.find(events.NonFatalError).info
    )


@pytest.mark.parametrize(
    ("phases", "expected", "total_errors"),
    [
        ([PhaseName.EXAMPLES, PhaseName.FUZZING], "Cannot generate test data for query parameter 'key'", 2),
        ([PhaseName.EXAMPLES], "Failed to generate test cases from examples for this API operation", 1),
    ],
)
def test_unsatisfiable_example(ctx, phases, expected, total_errors):
    # See GH-904
    # When filling missing properties during examples generation leads to unsatisfiable schemas
    schema = ctx.openapi.load_schema(
        {
            "/success": {
                "post": {
                    "parameters": [
                        # This parameter is not satisfiable
                        {
                            "name": "key",
                            "in": "query",
                            "required": True,
                            "schema": {"type": "integer", "minimum": 5, "maximum": 4},
                        }
                    ],
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "foo": {"type": "string", "example": "foo example string"},
                                    },
                                },
                            }
                        }
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            }
        }
    )
    # Then the testing process should not raise an internal error
    schema.config.generation.update(modes=[GenerationMode.POSITIVE])
    stream = EventStream(schema, max_examples=1, phases=phases).execute()
    # And the tests are failing because of the unsatisfiable schema
    stream.assert_errors()
    errors = stream.find_all(events.NonFatalError)
    assert expected in [str(err.value).splitlines()[0] for err in errors]
    assert len(errors) == total_errors


@pytest.mark.parametrize(
    ("phases", "expected"),
    [
        (
            [PhaseName.FUZZING],
            "No supported serializers for media types",
        ),
        (
            [PhaseName.EXAMPLES],
            (
                "Failed to generate test cases from examples for this API operation because of "
                "unsupported payload media types"
            ),
        ),
    ],
)
def test_non_serializable_example(ctx, phases, expected):
    # When filling missing request body during examples generation leads to serialization error
    schema = ctx.openapi.load_schema(
        {
            "/success": {
                "post": {
                    "parameters": [
                        {"name": "key", "in": "query", "required": True, "schema": {"type": "integer"}, "example": 42}
                    ],
                    "requestBody": {
                        "content": {
                            "image/jpeg": {
                                "schema": {"format": "base64", "type": "string"},
                            }
                        }
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            }
        }
    )
    # Then the testing process should not raise an internal error
    stream = EventStream(schema, phases=phases, max_examples=1).execute()
    # And the tests are failing because of the serialization error
    stream.assert_errors()
    errors = stream.find_all(events.NonFatalError)
    assert len(errors) == len(phases)
    assert expected in str(errors[0].info)


def test_unsupported_regex_removed_with_warning(ctx):
    # When a schema contains an unsupported regex pattern
    schema = ctx.openapi.load_schema(
        {
            "/success": {
                "post": {
                    "parameters": [
                        {"name": "key", "in": "query", "required": True, "schema": {"type": "integer"}, "example": 42}
                    ],
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "properties": {
                                        "region": {
                                            "nullable": True,
                                            "pattern": "^[\\w\\s\\-\\/\\p{Greek},.#;:()']+$",
                                            "type": "string",
                                        },
                                    },
                                    "required": ["region"],
                                    "type": "object",
                                }
                            }
                        },
                        "required": True,
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            }
        }
    )
    # Then the pattern is removed and a warning is emitted
    warnings = list(schema.analysis.iter_warnings())
    assert len(warnings) > 0
    assert any("^[\\w\\s\\-\\/\\p{Greek},.#;:()']+$" in w.message for w in warnings)


def test_unsupported_regex_in_parameter_removed_with_warning(ctx):
    # When a parameter schema contains an unsupported regex pattern
    schema = ctx.openapi.load_schema(
        {
            "/users/{id}": {
                "get": {
                    "parameters": [
                        {
                            "name": "id",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "string", "pattern": "\\p{Greek}+"},
                        }
                    ],
                    "responses": {"200": {"description": "OK"}},
                }
            }
        }
    )
    # Then the pattern is removed and a warning is emitted
    warnings = list(schema.analysis.iter_warnings())
    assert len(warnings) > 0
    assert any("\\p{Greek}+" in w.message for w in warnings)


def test_invalid_header_in_example(ctx):
    api = ctx.openapi.apps.success()
    schema = ctx.openapi.load_schema(
        {
            "/success": {
                "post": {
                    "parameters": [
                        {
                            "name": "SESSION",
                            "in": "header",
                            "required": True,
                            "schema": {"type": "string"},
                            "example": "test\ntest",
                        }
                    ],
                    "responses": {"200": {"description": "OK"}},
                }
            }
        }
    )
    # Then the testing process should not raise an internal error
    schema.config.update(base_url=f"{api.base_url}/api")
    stream = EventStream(schema, max_examples=1).execute()
    # And the tests are failing
    stream.assert_errors()
    expected = (
        "Failed to generate test cases from examples for this API operation because of some header examples are invalid"
    )
    errors = stream.find_all(events.NonFatalError)
    assert len(errors) == 1
    assert expected in str(errors[0].info)


def test_connection_error(ctx):
    schema = ctx.openapi.load_schema({"/success": {"post": {"responses": {"200": {"description": "OK"}}}}})
    schema.config.update(base_url="http://127.0.0.1:1")
    stream = EventStream(schema, max_examples=1).execute()
    # And the tests are failing
    stream.assert_errors()
    expected = "Max retries exceeded with url"
    errors = stream.find_all(events.NonFatalError)
    assert len(errors) == 1
    assert expected in str(errors[0].info)


def test_reserved_characters_in_operation_name(ctx):
    # See GH-992
    api = ctx.openapi.apps.reserved()
    schema = schemathesis.openapi.from_url(api.schema_url)

    def check(ctx, response, case):
        assert response.status_code == 200

    # When there is `:` in the API operation path
    stream = execute(schema, checks=(check,))
    # Then it should be reachable
    stream.assert_no_errors()
    stream.assert_no_failures()


def test_hypothesis_errors_propagation(ctx):
    # See: GH-1046
    # When the operation contains a media type, that Schemathesis can't serialize
    # And there is still a supported media type
    api = ctx.openapi.apps.success()
    schema = ctx.openapi.load_schema(
        {
            "/data": {
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {
                            # This one is known
                            "application/json": {
                                "schema": {
                                    "type": "array",
                                },
                            },
                            # This one is not
                            "application/xml": {
                                "schema": {
                                    "type": "array",
                                }
                            },
                        },
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            }
        }
    )

    max_examples = 10
    schema.config.update(base_url=f"{api.base_url}/api")
    stream = EventStream(
        schema,
        max_examples=max_examples,
        checks=[not_a_server_error],
    ).execute()
    # Then the test outcomes should not contain errors
    after = _last_scenario(stream)
    assert after.status == Status.SUCCESS
    # And there should be requested amount of test examples
    assert len(_scenario_checks(after)) == max_examples
    stream.assert_no_failures()
    stream.assert_no_errors()


def test_encoding_octet_stream(ctx):
    # See: GH-1134
    # When the operation contains the `application/octet-stream` media type
    # And has no `format: binary` in its schema
    api = ctx.openapi.apps.success()
    schema = ctx.openapi.load_schema(
        {
            "/data": {
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/octet-stream": {
                                "schema": {
                                    "type": "string",
                                },
                            },
                        },
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            }
        }
    )
    schema.config.update(base_url=f"{api.base_url}/api")
    stream = EventStream(
        schema,
        checks=[not_a_server_error],
    ).execute()
    # Then the test outcomes should not contain errors
    # And it should not lead to encoding errors
    stream.assert_after_execution_status(Status.SUCCESS)
    stream.assert_no_failures()
    stream.assert_no_errors()


def test_graphql(ctx):
    schema = schemathesis.graphql.from_url(ctx.graphql.apps.books().schema_url)
    stream = EventStream(schema, max_examples=5).execute()
    expected_order = [
        "Mutation.addAuthor",
        "Mutation.addBook",
        "Query.getAuthors",
        "Query.getBooks",
    ]
    for event, expected in zip(stream.find_all(events.ScenarioFinished), expected_order, strict=False):
        assert event.recorder.label == expected
        for case in event.recorder.cases.values():
            assert case.value.operation.label == expected


@pytest.mark.usefixtures("restore_checks")
def test_interrupted_in_test(ctx):
    api = ctx.openapi.apps.success()
    schema = schemathesis.openapi.from_url(api.schema_url)

    # When an interrupt happens within a test body (check is called within a test body)
    @schemathesis.check
    def interrupt_check(ctx, response, case):
        raise KeyboardInterrupt

    stream = EventStream(schema, checks=(interrupt_check,)).execute()
    interrupted = stream.find(events.Interrupted)
    # Then the `Interrupted` event should be emitted
    assert interrupted is not None
    scenario_finished = _last_scenario(stream)
    assert scenario_finished is not None
    assert scenario_finished.recorder.cases
    assert scenario_finished.recorder.interactions


def test_interrupted_outside_test(ctx, mocker):
    api = ctx.openapi.apps.success()
    schema = schemathesis.openapi.from_url(api.schema_url)
    # See GH-1325
    # When an interrupt happens outside a test body
    mocker.patch("schemathesis.engine.events.ScenarioFinished.__init__", side_effect=KeyboardInterrupt)

    stream = EventStream(schema).execute()
    try:
        interrupted = stream.find(events.Interrupted)
        # Then the `Interrupted` event should be emitted
        assert interrupted is not None
    except KeyboardInterrupt:
        pytest.fail("KeyboardInterrupt should be handled")


@pytest.fixture(params=[1, 2], ids=["single-worker", "multi-worker"])
def workers_num(request):
    return request.param


@pytest.fixture
def engine(workers_num, swagger_20):
    swagger_20.config.update(workers=workers_num)
    return from_schema(swagger_20)


@pytest.fixture
def event_stream(engine):
    return engine.execute()


def test_stop_event_stream(event_stream):
    assert isinstance(next(event_stream), events.EngineStarted)
    event_stream.stop()
    assert isinstance(next(event_stream), events.EngineFinished)
    assert next(event_stream, None) is None


def test_stop_event_stream_immediately(event_stream):
    event_stream.stop()
    assert isinstance(next(event_stream), events.EngineStarted)
    assert isinstance(next(event_stream), events.EngineFinished)
    assert next(event_stream, None) is None


def test_stop_event_stream_has_stop_reason_interrupted(event_stream):
    # When the engine is stopped externally
    assert isinstance(next(event_stream), events.EngineStarted)
    event_stream.stop()
    finished = next(event_stream)
    assert isinstance(finished, events.EngineFinished)
    # Then the stop reason is reflected in the final event
    assert finished.stop_reason == StopReason.INTERRUPTED


def test_engine_finished_stop_reason_completed(ctx):
    api = ctx.openapi.apps.success_and_failure()
    schema = schemathesis.openapi.from_url(api.schema_url)
    # When the engine runs to completion
    stream = EventStream(schema).execute()
    # Then the finished event reports completed
    assert stream.finished.stop_reason == StopReason.COMPLETED


def test_stop_event_stream_after_second_event(event_stream):
    next(event_stream)
    next(event_stream)
    next(event_stream)
    next(event_stream)
    next(event_stream)
    next(event_stream)
    next(event_stream)
    next(event_stream)
    next(event_stream)
    next(event_stream)
    event_stream.stop()
    next(event_stream)
    next(event_stream)
    assert isinstance(next(event_stream), events.EngineFinished)
    assert next(event_stream, None) is None


def test_finish(event_stream):
    assert isinstance(next(event_stream), events.EngineStarted)
    event = event_stream.finish()
    assert isinstance(event, events.EngineFinished)
    assert next(event_stream, None) is None


if IS_PYPY:
    REPLACEMENT_ERROR = "out of range: index 0 but only 0 arguments"
else:
    REPLACEMENT_ERROR = "Replacement index 0 out of range for positional args tuple"


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("/foo}/", "Single '}' encountered in format string"),
        ("/{.format}/", REPLACEMENT_ERROR),
    ],
)
def test_malformed_path_template(ctx, path, expected):
    # When schema contains a malformed path template
    schema = ctx.openapi.load_schema({path: {"get": {"responses": {"200": {"description": "OK"}}}}})
    # Then it should not cause a fatal error
    stream = EventStream(schema).execute()
    stream.assert_after_execution_status(Status.ERROR)
    # And should produce the proper error message
    assert str(stream.find(events.NonFatalError).value) == f"Malformed path template: `{path}`\n\n  {expected}"


@pytest.mark.parametrize(
    ("parameters", "expected"),
    [
        ([{"name": "key", "in": "query", "required": True, "schema": {"type": "integer"}}], Status.SUCCESS),
        ([], Status.SKIP),
    ],
)
def test_explicit_header_negative(ctx, parameters, expected):
    api = ctx.openapi.apps.success()
    schema = ctx.openapi.load_schema(
        {
            "/test": {
                "get": {
                    "parameters": parameters,
                    "security": [{"basicAuth": []}],
                    "responses": {"200": {"description": ""}},
                }
            }
        },
        components={"securitySchemes": {"basicAuth": {"type": "http", "scheme": "basic"}}},
    )
    schema.config.generation.update(modes=[GenerationMode.NEGATIVE])
    schema.config.update(base_url=f"{api.base_url}/api")
    stream = EventStream(schema, headers={"Authorization": "TEST"}, max_examples=1).execute()

    # There should not be unsatisfiable
    stream.assert_no_errors()
    stream.assert_after_execution_status(expected)


def test_skip_non_negated_headers(ctx):
    schema = ctx.openapi.load_schema(
        {
            "/test": {
                "get": {
                    "parameters": [{"in": "header", "name": "If-Modified-Since", "schema": {"type": "string"}}],
                    "responses": {"200": {"description": ""}},
                }
            }
        }
    )
    schema.config.generation.update(modes=[GenerationMode.NEGATIVE])
    stream = EventStream(schema, max_examples=1).execute()
    # There should not be unsatisfiable
    stream.assert_no_errors()
    stream.assert_after_execution_status(Status.SKIP)


STATEFUL_KWARGS = {
    "max_examples": 1,
    "max_steps": 2,
}


def test_stateful_auth(ctx):
    api = ctx.openapi.apps.users_crud()
    schema = schemathesis.openapi.from_url(api.schema_url)
    stream = EventStream(
        schema,
        phases=[PhaseName.STATEFUL_TESTING],
        auth=("admin", "password"),
        **STATEFUL_KWARGS,
    ).execute()
    interactions = stream.find_all_interactions()
    assert len(interactions) > 0
    for interaction in interactions:
        assert interaction.request.headers["Authorization"] == ["Basic YWRtaW46cGFzc3dvcmQ="]


def test_stateful_all_generation_modes(ctx):
    api = ctx.openapi.apps.users_crud()
    schema = schemathesis.openapi.from_url(api.schema_url)
    mode = GenerationMode.NEGATIVE
    schema.config.generation.update(modes=[mode])
    stream = EventStream(schema, phases=[PhaseName.STATEFUL_TESTING], **STATEFUL_KWARGS).execute()
    cases = _all_scenario_cases(stream)
    assert len(cases) > 0
    for case in cases:
        assert case.value.meta.generation.mode == mode


def test_stateful_seed(ctx):
    api = ctx.openapi.apps.users_crud()
    schema = schemathesis.openapi.from_url(api.schema_url)
    requests = []
    for _ in range(3):
        stream = EventStream(
            schema,
            phases=[PhaseName.STATEFUL_TESTING],
            seed=42,
            modes=[GenerationMode.POSITIVE],
            **STATEFUL_KWARGS,
        ).execute()
        current = []
        for interaction in stream.find_all_interactions():
            if interaction.request.method != "POST":
                continue
            data = {key: getattr(interaction.request, key) for key in Request.__slots__}
            del data["headers"][SCHEMATHESIS_TEST_CASE_HEADER]
            current.append(data)
            break
        requests.append(current)
    assert requests[0][0] == requests[1][0] == requests[2][0]


def test_stateful_override(ctx):
    api = ctx.openapi.apps.users_crud()
    schema = schemathesis.openapi.from_url(api.schema_url)
    stream = EventStream(
        schema,
        phases=[PhaseName.STATEFUL_TESTING],
        parameters={"user_id": "42"},
        max_examples=80,
        max_steps=2,
    ).execute()
    interactions = stream.find_all_interactions()
    assert len(interactions) > 0
    # Check any request that uses user_id (GET or PATCH)
    user_requests = [
        i.request for i in interactions if "/users/" in i.request.uri and i.request.method in ("GET", "PATCH")
    ]
    assert len(user_requests) > 0
    for request in user_requests:
        assert "/users/42" in request.uri


def test_generation_config_in_explicit_examples(ctx):
    api = ctx.openapi.apps.success()
    schema = ctx.openapi.load_schema(
        {
            "/what": {
                "post": {
                    "parameters": [
                        {
                            "in": "header",
                            "name": "X-VO-Api-Id",
                            "required": True,
                            "type": "string",
                        },
                        {
                            "in": "body",
                            "name": "body",
                            "required": True,
                            "schema": {
                                "properties": {
                                    "type": {
                                        "example": "email",
                                        "type": "string",
                                    },
                                },
                                "type": "object",
                            },
                        },
                    ],
                    "responses": {"200": {"description": "Ok"}},
                }
            },
        },
        version="2.0",
    )
    schema.config.update(base_url=f"{api.base_url}/api")
    schema.config.generation.update(
        with_security_parameters=False,
        exclude_header_characters="".join({chr(i) for i in range(256)} - {"a"}),
    )
    stream = EventStream(schema, max_examples=10).execute()
    for case in _scenario_cases(stream):
        for header in case.value.headers.values():
            if header:
                assert set(header) == {"a"}


def test_missing_deserializer_warnings_collected(ctx):
    api = ctx.openapi.apps.success()
    schema = ctx.openapi.load_schema(
        {
            "/users": {
                "get": {
                    "responses": {
                        "200": {
                            "description": "Success",
                            "content": {
                                "application/msgpack": {
                                    "schema": {"type": "object", "properties": {"id": {"type": "integer"}}}
                                }
                            },
                        }
                    }
                }
            }
        }
    )
    schema.config.update(base_url=f"{api.base_url}/api")
    stream = EventStream(schema, max_examples=1).execute()

    warning_event = stream.find(events.SchemaAnalysisWarnings)
    assert warning_event is not None
    assert len(warning_event.warnings) == 1
    warning = warning_event.warnings[0]
    assert warning.kind == SchemathesisWarning.MISSING_DESERIALIZER
    assert warning.operation_label == "GET /users"
    assert warning.status_code == "200"
    assert warning.content_type == "application/msgpack"


def test_no_warnings_for_json(ctx):
    api = ctx.openapi.apps.success()
    schema = ctx.openapi.load_schema(
        {
            "/users": {
                "get": {
                    "responses": {
                        "200": {
                            "description": "Success",
                            "content": {
                                "application/json": {
                                    "schema": {"type": "object", "properties": {"id": {"type": "integer"}}}
                                }
                            },
                        }
                    }
                }
            }
        }
    )
    schema.config.update(base_url=f"{api.base_url}/api")
    stream = EventStream(schema, max_examples=1).execute()

    warning_event = stream.find(events.SchemaAnalysisWarnings)
    assert warning_event is None


def test_stateful_phase_missing_deserializer_warnings(ctx):
    api = ctx.openapi.apps.success()
    schema = ctx.openapi.load_schema(
        {
            "/users": {
                "post": {
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {"type": "object", "properties": {"name": {"type": "string"}}}
                            }
                        }
                    },
                    "responses": {
                        "201": {
                            "description": "Created",
                            "content": {
                                "application/msgpack": {
                                    "schema": {"type": "object", "properties": {"id": {"type": "integer"}}}
                                }
                            },
                            "links": {
                                "GetUser": {
                                    "operationId": "getUser",
                                    "parameters": {"userId": "$response.body#/id"},
                                }
                            },
                        }
                    },
                }
            },
            "/users/{userId}": {
                "get": {
                    "operationId": "getUser",
                    "parameters": [{"name": "userId", "in": "path", "required": True, "schema": {"type": "integer"}}],
                    "responses": {
                        "200": {
                            "description": "Success",
                            "content": {
                                "application/msgpack": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {"id": {"type": "integer"}, "name": {"type": "string"}},
                                    }
                                }
                            },
                        }
                    },
                }
            },
        }
    )

    schema.config.update(base_url=f"{api.base_url}/api")

    # Run only stateful phase
    stream = EventStream(schema, phases=[PhaseName.STATEFUL_TESTING], **STATEFUL_KWARGS).execute()

    # Verify warnings were detected once for the run
    warning_event = stream.find(events.SchemaAnalysisWarnings)
    assert warning_event is not None

    warning_messages = {(w.operation_label, w.status_code, w.content_type) for w in warning_event.warnings}
    assert ("POST /users", "201", "application/msgpack") in warning_messages
    assert ("GET /users/{userId}", "200", "application/msgpack") in warning_messages
