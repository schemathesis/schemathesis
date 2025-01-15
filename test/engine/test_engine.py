from __future__ import annotations

import platform
from dataclasses import asdict
from typing import TYPE_CHECKING
from unittest.mock import ANY

import hypothesis
import pytest
from aiohttp.streams import EmptyStreamReader
from fastapi import FastAPI
from hypothesis import Phase, settings
from hypothesis import strategies as st

import schemathesis
from schemathesis.checks import not_a_server_error
from schemathesis.core import SCHEMATHESIS_TEST_CASE_HEADER
from schemathesis.core.errors import RECURSIVE_REFERENCE_ERROR_MESSAGE
from schemathesis.core.transport import USER_AGENT
from schemathesis.engine import Status, events, from_schema
from schemathesis.engine.config import EngineConfig, ExecutionConfig, NetworkConfig
from schemathesis.engine.phases import PhaseName
from schemathesis.engine.recorder import Request
from schemathesis.generation import GenerationConfig, GenerationMode, HeaderConfig
from schemathesis.generation.hypothesis.builder import add_examples
from schemathesis.generation.overrides import Override
from schemathesis.specs.openapi.checks import (
    content_type_conformance,
    response_schema_conformance,
    status_code_conformance,
)
from test.utils import EventStream

if TYPE_CHECKING:
    from aiohttp import web


def execute(schema, **options) -> EventStream:
    return EventStream(schema, **options).execute()


def assert_request(
    app: web.Application, idx: int, method: str, path: str, headers: dict[str, str] | None = None
) -> None:
    request = app["incoming_requests"][idx]
    assert request.method == method
    if request.method == "GET":
        # Ref: #200
        # GET requests should not contain bodies
        if not isinstance(request.content, EmptyStreamReader):
            assert request.content._read_nowait(-1) != b"{}"
    assert request.path == path
    if headers:
        for key, value in headers.items():
            assert request.headers.get(key) == value


def assert_not_request(app: web.Application, method: str, path: str) -> None:
    for request in app["incoming_requests"]:
        assert not (request.path == path and request.method == method)


def assert_incoming_requests_num(app, number):
    assert len(app["incoming_requests"]) == number


def assert_schema_requests_num(app, number):
    assert len(app["schema_requests"]) == number


def test_execute_base_url_not_found(openapi3_base_url, schema_url, app):
    # When base URL is pointing to an unknown location
    schema = schemathesis.openapi.from_url(schema_url).configure(base_url=f"{openapi3_base_url}/404/")
    EventStream(schema).execute()
    # Then the engine should use this base
    # And they will not reach the application
    assert_incoming_requests_num(app, 0)


def test_execute(app, real_app_schema):
    # When the engine is executed against the default test app
    EventStream(real_app_schema).execute()

    # Then there are three executed cases
    # Two errors - the second one is a flakiness check
    headers = {"User-Agent": USER_AGENT}
    assert_schema_requests_num(app, 1)
    schema_requests = app["schema_requests"]
    assert schema_requests[0].headers.get("User-Agent") == headers["User-Agent"]
    assert_incoming_requests_num(app, 2)
    assert_request(app, 0, "GET", "/api/failure", headers)
    assert_request(app, 1, "GET", "/api/success", headers)


@pytest.mark.parametrize("workers", [1, 2])
def test_interactions(openapi3_base_url, real_app_schema, workers):
    stream = EventStream(real_app_schema, workers_num=workers).execute()

    # failure
    interactions = list(stream.find(events.ScenarioFinished, status=Status.FAILURE).recorder.interactions.values())
    assert len(interactions) == 1
    failure = interactions[0]
    assert asdict(failure.request) == {
        "uri": f"{openapi3_base_url}/failure",
        "method": "GET",
        "body": None,
        "body_size": None,
        "headers": {
            "Accept": ["*/*"],
            "Accept-Encoding": ["gzip, deflate"],
            "Connection": ["keep-alive"],
            "User-Agent": [USER_AGENT],
            SCHEMATHESIS_TEST_CASE_HEADER: [ANY],
        },
    }
    assert failure.response.status_code == 500
    assert failure.response.message == "Internal Server Error"
    assert failure.response.headers["content-type"] == ["text/plain; charset=utf-8"]
    assert failure.response.headers["content-length"] == ["26"]
    # success
    interactions = list(stream.find(events.ScenarioFinished, status=Status.SUCCESS).recorder.interactions.values())
    assert len(interactions) == 1
    success = interactions[0]
    assert asdict(success.request) == {
        "uri": f"{openapi3_base_url}/success",
        "method": "GET",
        "body": None,
        "body_size": None,
        "headers": {
            "Accept": ["*/*"],
            "Accept-Encoding": ["gzip, deflate"],
            "Connection": ["keep-alive"],
            "User-Agent": [USER_AGENT],
            SCHEMATHESIS_TEST_CASE_HEADER: [ANY],
        },
    }
    assert success.response.status_code == 200
    assert success.response.message == "OK"
    assert success.response.json() == {"success": True}
    assert success.response.encoding == "utf-8"
    assert success.response.headers["content-type"] == ["application/json; charset=utf-8"]


@pytest.mark.operations("root")
def test_asgi_interactions(fastapi_app):
    schema = schemathesis.openapi.from_asgi("/openapi.json", fastapi_app)
    stream = EventStream(schema).execute()
    event = stream.find(events.ScenarioFinished)
    interaction = list(event.recorder.interactions.values())[0]
    assert interaction.request.uri == "http://localhost/users"


@pytest.mark.operations("empty")
def test_empty_response_interaction(real_app_schema):
    # When there is a GET request and a response that doesn't return content (e.g. 204)
    stream = EventStream(real_app_schema).execute()
    interactions = list(stream.find(events.ScenarioFinished).recorder.interactions.values())
    for interaction in interactions:  # There could be multiple calls
        # Then the stored request has no body
        assert interaction.request.body is None
        # And response encoding is missing
        assert interaction.response.encoding is None


@pytest.mark.openapi_version("3.0")
@pytest.mark.operations("empty_string")
def test_empty_string_response_interaction(real_app_schema):
    # When there is a response that returns payload of length 0
    stream = EventStream(real_app_schema).execute()
    interactions = stream.find(events.ScenarioFinished).recorder.interactions.values()
    for interaction in interactions:  # There could be multiple calls
        # Then the stored response body should be an empty string
        assert interaction.response.content == b""
        assert interaction.response.encoding == "utf-8"


def test_auth(app, real_app_schema):
    # When auth is specified as a tuple of 2 strings
    execute(real_app_schema, network=NetworkConfig(auth=("test", "test")))

    # Then each request should contain corresponding basic auth header
    assert_incoming_requests_num(app, 2)
    headers = {"Authorization": "Basic dGVzdDp0ZXN0"}
    assert_request(app, 0, "GET", "/api/failure", headers)
    assert_request(app, 1, "GET", "/api/success", headers)


@pytest.mark.parametrize("converter", [lambda x: x, lambda x: x + "/"])
def test_base_url(openapi3_base_url, schema_url, app, converter):
    base_url = converter(openapi3_base_url)
    # When `base_url` is specified explicitly with or without trailing slash
    schema = schemathesis.openapi.from_url(schema_url).configure(base_url=base_url)
    execute(schema)

    # Then each request should reach the app in both cases
    assert_incoming_requests_num(app, 2)
    assert_request(app, 0, "GET", "/api/failure")
    assert_request(app, 1, "GET", "/api/success")


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


def test_execute_with_headers(app, real_app_schema):
    # When headers are specified for the `execute` call
    headers = {"Authorization": "Bearer 123"}
    execute(real_app_schema, network=NetworkConfig(headers=headers))

    # Then each request should contain these headers
    assert_incoming_requests_num(app, 2)
    assert_request(app, 0, "GET", "/api/failure", headers)
    assert_request(app, 1, "GET", "/api/success", headers)


def test_execute_filter_endpoint(app, schema_url):
    schema = schemathesis.openapi.from_url(schema_url).include(path_regex="success")
    # When `endpoint` is passed in the `execute` call
    execute(schema)

    # Then the engine will make calls only to the specified path
    assert_incoming_requests_num(app, 1)
    assert_request(app, 0, "GET", "/api/success")
    assert_not_request(app, "GET", "/api/failure")


def test_execute_filter_method(app, schema_url):
    schema = schemathesis.openapi.from_url(schema_url).include(method="POST")
    # When `method` corresponds to a method that is not defined in the app schema
    execute(schema)
    # Then engine will not make any requests
    assert_incoming_requests_num(app, 0)


@pytest.mark.operations("slow")
def test_hypothesis_deadline(app, real_app_schema):
    # When `hypothesis_deadline` is passed in the `execute` call
    execute(real_app_schema, hypothesis_settings=hypothesis.settings(deadline=500))
    assert_incoming_requests_num(app, 1)
    assert_request(app, 0, "GET", "/api/slow")


@pytest.mark.operations("multipart")
def test_form_data(app, real_app_schema):
    def is_ok(ctx, response, case):
        assert response.status_code == 200

    def check_content(ctx, response, case):
        data = response.json()
        assert isinstance(data["key"], str)
        assert data["value"].lstrip("-").isdigit()

    # When API operation specifies parameters with `in=formData`
    # Then responses should have 200 status, and not 415 (unsupported media type)
    stream = execute(
        real_app_schema,
        checks=(is_ok, check_content),
        hypothesis_settings=hypothesis.settings(max_examples=3, deadline=None),
    )
    # And there should be no errors or failures
    stream.assert_no_errors()
    stream.assert_no_failures()
    # And the application should receive 3 requests as specified in `max_examples`
    assert_incoming_requests_num(app, 3)
    # And the Content-Type of incoming requests should be `multipart/form-data`
    incoming_requests = app["incoming_requests"]
    assert incoming_requests[0].headers["Content-Type"].startswith("multipart/form-data")


@pytest.mark.operations("headers")
def test_headers_override(real_app_schema):
    def check_headers(ctx, response, case):
        data = response.json()
        assert data["X-Token"] == "test"

    stream = EventStream(
        real_app_schema,
        checks=(check_headers,),
        network=NetworkConfig(headers={"X-Token": "test"}),
        hypothesis_settings=hypothesis.settings(max_examples=1, deadline=None),
    ).execute()
    stream.assert_no_failures()
    stream.assert_no_errors()


@pytest.mark.operations("teapot")
def test_unknown_response_code(real_app_schema):
    # When API operation returns a status code, that is not listed in "responses"
    # And "status_code_conformance" is specified
    stream = EventStream(
        real_app_schema,
        checks=(status_code_conformance,),
        hypothesis_settings=hypothesis.settings(max_examples=1, deadline=None),
    ).execute()

    # Then there should be a failure
    assert stream.failures_count == 1
    check = list(stream.find(events.ScenarioFinished).recorder.checks.values())[0][0]
    assert check.name == "status_code_conformance"
    assert check.status == Status.FAILURE
    assert check.failure_info.failure.status_code == 418
    assert check.failure_info.failure.allowed_status_codes == [200]
    assert check.failure_info.failure.defined_status_codes == ["200"]


@pytest.mark.operations("failure")
def test_unknown_response_code_with_default(real_app_schema):
    # When API operation returns a status code, that is not listed in "responses", but there is a "default" response
    # And "status_code_conformance" is specified
    stream = EventStream(
        real_app_schema,
        checks=(status_code_conformance,),
        hypothesis_settings=hypothesis.settings(max_examples=1, deadline=None),
    ).execute()
    # Then there should be no failure
    stream.assert_no_failures()
    check = list(stream.find(events.ScenarioFinished).recorder.checks.values())[0][0]
    assert check.name == "status_code_conformance"
    assert check.status == Status.SUCCESS


@pytest.mark.operations("text")
def test_unknown_content_type(real_app_schema):
    # When API operation returns a response with content type, not specified in "produces"
    # And "content_type_conformance" is specified
    stream = EventStream(
        real_app_schema,
        checks=(content_type_conformance,),
        hypothesis_settings=hypothesis.settings(max_examples=1, deadline=None),
    ).execute()
    # Then there should be a failure
    assert stream.failures_count == 1
    check = list(stream.find(events.ScenarioFinished).recorder.checks.values())[0][0]
    assert check.name == "content_type_conformance"
    assert check.status == Status.FAILURE
    assert check.failure_info.failure.content_type == "text/plain"
    assert check.failure_info.failure.defined_content_types == ["application/json"]


@pytest.mark.operations("success")
def test_known_content_type(real_app_schema):
    # When API operation returns a response with a proper content type
    # And "content_type_conformance" is specified
    stream = execute(
        real_app_schema,
        checks=(content_type_conformance,),
        hypothesis_settings=hypothesis.settings(max_examples=1, deadline=None),
    )
    # Then there should be no failures
    stream.assert_no_failures()


@pytest.mark.operations("invalid_response")
def test_response_conformance_invalid(real_app_schema):
    # When API operation returns a response that doesn't conform to the schema
    # And "response_schema_conformance" is specified
    stream = EventStream(
        real_app_schema,
        checks=(response_schema_conformance,),
        hypothesis_settings=hypothesis.settings(max_examples=1, deadline=None),
    ).execute()
    # Then there should be a failure
    assert stream.failures_count == 1
    check = list(stream.find(events.ScenarioFinished).recorder.checks.values())[-1][-1]
    assert check.failure_info.failure.title == "Response violates schema"
    assert (
        check.failure_info.failure.message
        == """'success' is a required property

Schema:

    {
        "properties": {
            "success": {
                "type": "boolean"
            }
        },
        "required": [
            "success"
        ],
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
        "properties": {"success": {"type": "boolean"}},
        "required": ["success"],
        "type": "object",
    }
    assert check.failure_info.failure.schema_path == ["required"]
    assert check.failure_info.failure.validation_message == "'success' is a required property"


@pytest.mark.operations("success")
def test_response_conformance_valid(real_app_schema):
    # When API operation returns a response that conforms to the schema
    # And "response_schema_conformance" is specified
    stream = execute(
        real_app_schema,
        checks=(response_schema_conformance,),
        hypothesis_settings=hypothesis.settings(max_examples=1, deadline=None),
    )
    # Then there should be no failures or errors
    stream.assert_no_failures()
    stream.assert_no_errors()


@pytest.mark.operations("recursive")
def test_response_conformance_recursive_valid(real_app_schema):
    # When API operation contains a response that have recursive references
    # And "response_schema_conformance" is specified
    stream = execute(
        real_app_schema,
        checks=(response_schema_conformance,),
        hypothesis_settings=hypothesis.settings(max_examples=1, deadline=None),
    )
    # Then there should be no failures or errors
    stream.assert_no_failures()
    stream.assert_no_errors()


@pytest.mark.operations("text")
def test_response_conformance_text(real_app_schema):
    # When API operation returns a response that is not JSON
    # And "response_schema_conformance" is specified
    stream = execute(
        real_app_schema,
        checks=(response_schema_conformance,),
        hypothesis_settings=hypothesis.settings(max_examples=1, deadline=None),
    )
    # Then the check should be ignored if the response headers are not application/json
    stream.assert_no_failures()
    stream.assert_no_errors()


@pytest.mark.operations("malformed_json")
def test_response_conformance_malformed_json(real_app_schema):
    # When API operation returns a response that contains a malformed JSON, but has a valid content type header
    # And "response_schema_conformance" is specified
    stream = EventStream(
        real_app_schema,
        checks=(response_schema_conformance,),
        hypothesis_settings=hypothesis.settings(max_examples=1, deadline=None),
    ).execute()
    # Then there should be a failure
    assert stream.failures_count == 1
    stream.assert_no_errors()
    check = list(stream.find(events.ScenarioFinished).recorder.checks.values())[-1][-1]
    assert check.failure_info.failure.title == "JSON deserialization error"
    assert check.failure_info.failure.validation_message == "Expecting property name enclosed in double quotes"
    assert check.failure_info.failure.position == 1


@pytest.fixture
def filter_path_parameters():
    # ".." and "." strings are treated specially, but this behavior is outside the test's scope
    # "" shouldn't be allowed as a valid path parameter

    def before_generate_path_parameters(context, strategy):
        return strategy.filter(
            lambda x: x["key"] not in ("..", ".", "", "/") and not (isinstance(x["key"], str) and "/" in x["key"])
        )

    schemathesis.hook(before_generate_path_parameters)
    return


@pytest.mark.operations("path_variable")
@pytest.mark.usefixtures("filter_path_parameters")
def test_path_parameters_encoding(real_app_schema):
    # NOTE. WSGI and ASGI applications decode %2F as / and returns 404
    # When API operation has a path parameter
    stream = execute(
        real_app_schema,
        checks=(status_code_conformance,),
        hypothesis_settings=hypothesis.settings(derandomize=True, deadline=None),
    )
    # Then there should be no failures
    # since all path parameters are quoted
    stream.assert_no_errors()
    stream.assert_no_failures()


@pytest.mark.parametrize(
    ("configuration", "from_schema_options"),
    [
        ({"base_url": "http://127.0.0.1:1/"}, {}),
        ({}, {"hypothesis_settings": hypothesis.settings(deadline=1)}),
    ],
)
@pytest.mark.operations("slow")
def test_exceptions(schema_url, configuration, from_schema_options):
    schema = schemathesis.openapi.from_url(schema_url).configure(**configuration)
    stream = execute(schema, **from_schema_options)
    assert any(event.status == Status.ERROR for event in stream.find_all(events.ScenarioFinished))


@pytest.mark.operations("multipart")
def test_internal_exceptions(real_app_schema, mocker):
    # GH: #236
    # When there is an exception during the test
    # And Hypothesis consider this test as a flaky one
    mocker.patch("schemathesis.Case.call", side_effect=ValueError)
    stream = execute(real_app_schema, hypothesis_settings=hypothesis.settings(max_examples=3, deadline=None))
    # Then the execution result should indicate errors
    stream.assert_errors()
    # And an error from the buggy code should be collected
    exceptions = [error.value.__class__.__name__ for error in stream.find_all(events.NonFatalError)]
    assert "ValueError" in exceptions
    assert len(exceptions) == 1


@pytest.mark.operations("payload")
async def test_payload_explicit_example(app, real_app_schema):
    # When API operation has an example specified
    stream = execute(real_app_schema, hypothesis_settings=hypothesis.settings(phases=[Phase.explicit], deadline=None))
    # Then run should be successful
    stream.assert_no_errors()
    stream.assert_no_failures()
    incoming_requests = app["incoming_requests"]

    body = await incoming_requests[0].json()
    # And this example should be sent to the app
    assert body == {"name": "John"}


def test_explicit_examples_from_response(ctx, openapi3_base_url):
    schema = ctx.openapi.build_schema(
        {
            "/items/{itemId}/": {
                "get": {
                    "parameters": [{"name": "itemId", "in": "path", "schema": {"type": "string"}, "required": True}],
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
    schema = schemathesis.openapi.from_dict(schema).configure(base_url=openapi3_base_url)
    stream = EventStream(
        schema,
        hypothesis_settings=hypothesis.settings(max_examples=1, deadline=None, phases=[Phase.explicit]),
    ).execute()
    assert [case.value.path_parameters for case in stream.find(events.ScenarioFinished).recorder.cases.values()] == [
        {"itemId": "456789"},
        {"itemId": "123456"},
    ]


@pytest.mark.operations("payload")
async def test_explicit_example_disable(app, real_app_schema, mocker):
    # When API operation has an example specified
    # And the `explicit` phase is excluded
    spy = mocker.patch("schemathesis.generation.hypothesis.builder.add_examples", wraps=add_examples)
    stream = execute(
        real_app_schema, hypothesis_settings=hypothesis.settings(max_examples=1, phases=[Phase.generate], deadline=None)
    )
    # Then run should be successful
    stream.assert_no_errors()
    stream.assert_no_failures()
    incoming_requests = app["incoming_requests"]
    assert len(incoming_requests) == 1

    body = await incoming_requests[0].json()
    # And this example should NOT be used
    assert body != {"name": "John"}
    # And examples are not evaluated at all
    assert not spy.called


@pytest.mark.operations("plain_text_body")
def test_plain_text_body(app, real_app_schema):
    # When the expected payload is text/plain
    # Then the payload is not encoded as JSON
    def check_content(ctx, response, case):
        data = response.content
        assert case.body.encode("utf8") == data

    stream = execute(
        real_app_schema, checks=(check_content,), hypothesis_settings=hypothesis.settings(max_examples=3, deadline=None)
    )
    stream.assert_no_errors()
    stream.assert_no_failures()


@pytest.mark.operations("invalid_path_parameter")
def test_invalid_path_parameter(schema_url):
    # When a path parameter is marked as not required
    # And schema validation is disabled
    schema = schemathesis.openapi.from_url(schema_url)
    stream = execute(schema, hypothesis_settings=hypothesis.settings(max_examples=3, deadline=None))
    # Then Schemathesis enforces all path parameters to be required
    # And there should be no errors
    stream.assert_no_errors()


@pytest.mark.operations("missing_path_parameter")
def test_missing_path_parameter(real_app_schema):
    # When a path parameter is missing
    stream = EventStream(
        real_app_schema, hypothesis_settings=hypothesis.settings(max_examples=3, deadline=None)
    ).execute()
    # Then it leads to an error
    stream.assert_errors()
    assert "Path parameter 'id' is not defined" in str(stream.find(events.NonFatalError).info)


@pytest.mark.operations("failure", "multiple_failures", "unsatisfiable")
def test_max_failures(real_app_schema):
    # When `max_failures` is specified
    stream = execute(real_app_schema, max_failures=2)
    # Then the total numbers of failures and errors should not exceed this number
    assert stream.failures_count == 2
    errors = stream.find_all(events.NonFatalError)
    assert stream.failures_count + len(errors) == 2


@pytest.mark.skipif(platform.system() == "Windows", reason="Fails on Windows due to recursion")
def test_skip_operations_with_recursive_references(schema_with_recursive_references):
    # When the test schema contains recursive references
    schema = schemathesis.openapi.from_dict(schema_with_recursive_references)
    stream = EventStream(schema).execute()
    # Then it causes an error with a proper error message
    stream.assert_after_execution_status(Status.ERROR)
    assert RECURSIVE_REFERENCE_ERROR_MESSAGE in str(stream.find(events.NonFatalError).info)


@pytest.mark.parametrize(
    ("phases", "expected", "total_errors"),
    [
        ([Phase.explicit, Phase.generate], "Failed to generate test cases for this API operation", 2),
        ([Phase.explicit], "Failed to generate test cases from examples for this API operation", 1),
    ],
)
def test_unsatisfiable_example(ctx, phases, expected, total_errors):
    # See GH-904
    # When filling missing properties during examples generation leads to unsatisfiable schemas
    schema = ctx.openapi.build_schema(
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
    schema = schemathesis.openapi.from_dict(schema)
    stream = EventStream(
        schema, hypothesis_settings=hypothesis.settings(max_examples=1, deadline=None, phases=phases)
    ).execute()
    # And the tests are failing because of the unsatisfiable schema
    stream.assert_errors()
    errors = stream.find_all(events.NonFatalError)
    assert expected in [str(err.value) for err in errors]
    assert len(errors) == total_errors


@pytest.mark.parametrize(
    ("phases", "expected"),
    [
        ([Phase.explicit, Phase.generate], "Schemathesis can't serialize data to any of the defined media types"),
        (
            [Phase.explicit],
            (
                "Failed to generate test cases from examples for this API operation because of "
                "unsupported payload media types"
            ),
        ),
    ],
)
def test_non_serializable_example(ctx, phases, expected):
    # When filling missing request body during examples generation leads to serialization error
    schema = ctx.openapi.build_schema(
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
    schema = schemathesis.openapi.from_dict(schema)
    stream = EventStream(
        schema, hypothesis_settings=hypothesis.settings(max_examples=1, deadline=None, phases=phases)
    ).execute()
    # And the tests are failing because of the serialization error
    stream.assert_errors()
    errors = stream.find_all(events.NonFatalError)
    assert len(errors) == 1
    assert expected in str(errors[0].info)


@pytest.mark.parametrize(
    ("phases", "expected"),
    [
        (
            [Phase.explicit, Phase.generate],
            "Failed to generate test cases for this API operation because of "
            r"unsupported regular expression `^[\w\s\-\/\pL,.#;:()']+$`",
        ),
        (
            [Phase.explicit],
            (
                "Failed to generate test cases from examples for this API operation because of "
                r"unsupported regular expression `^[\w\s\-\/\pL,.#;:()']+$`"
            ),
        ),
    ],
)
def test_invalid_regex_example(ctx, phases, expected):
    # When filling missing properties during examples generation contains invalid regex
    schema = ctx.openapi.build_schema(
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
                                            "pattern": "^[\\w\\s\\-\\/\\pL,.#;:()']+$",
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
    # Then the testing process should not raise an internal error
    schema = schemathesis.openapi.from_dict(schema)
    stream = EventStream(
        schema,
        hypothesis_settings=hypothesis.settings(max_examples=1, deadline=None, phases=phases),
    ).execute()
    # And the tests are failing because of the invalid regex error
    stream.assert_errors()
    errors = stream.find_all(events.NonFatalError)
    assert len(errors) == 1
    assert expected in str(errors[0].info)


def test_invalid_header_in_example(ctx, openapi3_base_url):
    schema = ctx.openapi.build_schema(
        {
            "/success": {
                "post": {
                    "parameters": [
                        {
                            "name": "SESSION",
                            "in": "header",
                            "required": True,
                            "schema": {"type": "integer"},
                            "example": "test\ntest",
                        }
                    ],
                    "responses": {"200": {"description": "OK"}},
                }
            }
        }
    )
    # Then the testing process should not raise an internal error
    schema = schemathesis.openapi.from_dict(schema).configure(base_url=openapi3_base_url)
    stream = EventStream(schema, hypothesis_settings=hypothesis.settings(max_examples=1, deadline=None)).execute()
    # And the tests are failing
    stream.assert_errors()
    expected = (
        "Failed to generate test cases from examples for this API operation because of some header examples are invalid"
    )
    errors = stream.find_all(events.NonFatalError)
    assert len(errors) == 1
    assert expected in str(errors[0].info)


def test_connection_error(ctx):
    schema = ctx.openapi.build_schema({"/success": {"post": {"responses": {"200": {"description": "OK"}}}}})
    schema = schemathesis.openapi.from_dict(schema).configure(base_url="http://127.0.0.1:1")
    stream = EventStream(
        schema,
        hypothesis_settings=hypothesis.settings(max_examples=1, deadline=None),
    ).execute()
    # And the tests are failing
    stream.assert_errors()
    expected = "Max retries exceeded with url"
    errors = stream.find_all(events.NonFatalError)
    assert len(errors) == 1
    assert expected in str(errors[0].info)


@pytest.mark.operations("reserved")
def test_reserved_characters_in_operation_name(real_app_schema):
    # See GH-992

    def check(ctx, response, case):
        assert response.status_code == 200

    # When there is `:` in the API operation path
    stream = execute(real_app_schema, checks=(check,))
    # Then it should be reachable
    stream.assert_no_errors()
    stream.assert_no_failures()


def test_hypothesis_errors_propagation(ctx, openapi3_base_url):
    # See: GH-1046
    # When the operation contains a media type, that Schemathesis can't serialize
    # And there is still a supported media type
    schema = ctx.openapi.build_schema(
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
    schema = schemathesis.openapi.from_dict(schema).configure(base_url=openapi3_base_url)
    stream = EventStream(
        schema,
        hypothesis_settings=hypothesis.settings(max_examples=max_examples, deadline=None),
        checks=[not_a_server_error],
    ).execute()
    # Then the test outcomes should not contain errors
    after = stream.find(events.ScenarioFinished)
    assert after.status == Status.SUCCESS
    # And there should be requested amount of test examples
    assert sum(len(checks) for checks in after.recorder.checks.values()) == max_examples
    stream.assert_no_failures()
    stream.assert_no_errors()


def test_encoding_octet_stream(ctx, openapi3_base_url):
    # See: GH-1134
    # When the operation contains the `application/octet-stream` media type
    # And has no `format: binary` in its schema
    schema = ctx.openapi.build_schema(
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
    schema = schemathesis.openapi.from_dict(schema).configure(base_url=openapi3_base_url)
    stream = EventStream(
        schema,
        checks=[not_a_server_error],
    ).execute()
    # Then the test outcomes should not contain errors
    # And it should not lead to encoding errors
    stream.assert_after_execution_status(Status.SUCCESS)
    stream.assert_no_failures()
    stream.assert_no_errors()


def test_graphql(graphql_url):
    schema = schemathesis.graphql.from_url(graphql_url)
    stream = EventStream(schema, hypothesis_settings=hypothesis.settings(max_examples=5, deadline=None)).execute()
    for event, expected in zip(stream.find_all(events.ScenarioFinished), ["Query.getBooks", "Query.getAuthors"]):
        assert event.recorder.label == expected
        for case in event.recorder.cases.values():
            assert case.value.operation.label == expected


@pytest.mark.operations("success")
def test_interrupted_in_test(openapi3_schema):
    # When an interrupt happens within a test body (check is called within a test body)
    def check(ctx, response, case):
        raise KeyboardInterrupt

    interrupted = EventStream(openapi3_schema, checks=(check,)).execute().find(events.Interrupted)
    # Then the `Interrupted` event should be emitted
    assert interrupted is not None


@pytest.mark.operations("success")
def test_interrupted_outside_test(mocker, openapi3_schema):
    # See GH-1325
    # When an interrupt happens outside a test body
    mocker.patch("schemathesis.engine.events.ScenarioFinished.__init__", side_effect=KeyboardInterrupt)

    stream = EventStream(openapi3_schema).execute()
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
    return from_schema(swagger_20, config=EngineConfig(execution=ExecutionConfig(workers_num=workers_num)))


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


def test_stop_event_stream_after_second_event(event_stream):
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


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("/foo}/", "Single '}' encountered in format string"),
        ("/{.format}/", "Replacement index 0 out of range for positional args tuple"),
    ],
)
def test_malformed_path_template(ctx, path, expected):
    # When schema contains a malformed path template
    schema = ctx.openapi.build_schema({path: {"get": {"responses": {"200": {"description": "OK"}}}}})
    schema = schemathesis.openapi.from_dict(schema)
    # Then it should not cause a fatal error
    stream = EventStream(schema).execute()
    stream.assert_after_execution_status(Status.ERROR)
    # And should produce the proper error message
    assert str(stream.find(events.NonFatalError).value) == f"Malformed path template: `{path}`\n\n  {expected}"


@pytest.mark.parametrize(
    ("parameters", "expected"),
    [
        ([{"in": "query", "name": "key", "required": True, "schema": {"type": "integer"}}], Status.SUCCESS),
        ([], Status.SKIP),
    ],
)
def test_explicit_header_negative(ctx, parameters, expected, openapi3_base_url):
    schema = ctx.openapi.build_schema(
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
    schema = schemathesis.openapi.from_dict(schema).configure(
        generation=GenerationConfig(modes=[GenerationMode.NEGATIVE]), base_url=openapi3_base_url
    )
    stream = EventStream(
        schema,
        network=NetworkConfig(headers={"Authorization": "TEST"}),
        hypothesis_settings=hypothesis.settings(max_examples=1),
    ).execute()

    # There should not be unsatisfiable
    stream.assert_no_errors()
    stream.assert_after_execution_status(expected)


def test_skip_non_negated_headers(ctx):
    schema = ctx.openapi.build_schema(
        {
            "/test": {
                "get": {
                    "parameters": [{"in": "header", "name": "If-Modified-Since", "schema": {"type": "string"}}],
                    "responses": {"200": {"description": ""}},
                }
            }
        }
    )
    schema = schemathesis.openapi.from_dict(schema).configure(
        generation=GenerationConfig(modes=[GenerationMode.NEGATIVE])
    )
    stream = EventStream(
        schema,
        hypothesis_settings=hypothesis.settings(max_examples=1),
    ).execute()
    # There should not be unsatisfiable
    stream.assert_no_errors()
    stream.assert_after_execution_status(Status.SKIP)


STATEFUL_KWARGS = {
    "hypothesis_settings": hypothesis.settings(max_examples=1, deadline=None, stateful_step_count=2),
}


@pytest.mark.openapi_version("3.0")
@pytest.mark.operations("get_user", "create_user", "update_user")
def test_stateful_auth(real_app_schema):
    stream = EventStream(
        real_app_schema,
        phases=[PhaseName.STATEFUL_TESTING],
        network=NetworkConfig(auth=("admin", "password")),
        **STATEFUL_KWARGS,
    ).execute()
    interactions = list(stream.find(events.ScenarioFinished).recorder.interactions.values())
    assert len(interactions) > 0
    for interaction in interactions:
        assert interaction.request.headers["Authorization"] == ["Basic YWRtaW46cGFzc3dvcmQ="]


@pytest.mark.openapi_version("3.0")
@pytest.mark.operations("get_user", "create_user", "update_user")
def test_stateful_all_generation_modes(real_app_schema):
    method = GenerationMode.NEGATIVE
    real_app_schema.generation_config.modes = [method]
    stream = EventStream(real_app_schema, phases=[PhaseName.STATEFUL_TESTING], **STATEFUL_KWARGS).execute()
    cases = list(stream.find(events.ScenarioFinished).recorder.cases.values())
    assert len(cases) > 0
    for case in cases:
        assert case.value.meta.generation.mode == method


@pytest.mark.openapi_version("3.0")
@pytest.mark.operations("get_user", "create_user", "update_user")
def test_stateful_seed(real_app_schema):
    requests = []
    for _ in range(3):
        stream = EventStream(real_app_schema, phases=[PhaseName.STATEFUL_TESTING], seed=42, **STATEFUL_KWARGS).execute()
        current = []
        interactions = stream.find(events.ScenarioFinished).recorder.interactions
        for interaction in interactions.values():
            data = {key: getattr(interaction.request, key) for key in Request.__slots__}
            del data["headers"][SCHEMATHESIS_TEST_CASE_HEADER]
            current.append(data)
        requests.append(current)
    assert requests[0][0] == requests[1][0] == requests[2][0]


@pytest.mark.openapi_version("3.0")
@pytest.mark.operations("get_user", "create_user", "update_user")
def test_stateful_override(real_app_schema):
    stream = EventStream(
        real_app_schema,
        phases=[PhaseName.STATEFUL_TESTING],
        override=Override(path_parameters={"user_id": "42"}, headers={}, query={}, cookies={}),
        hypothesis_settings=hypothesis.settings(max_examples=40, deadline=None, stateful_step_count=2),
    ).execute()
    interactions = stream.find(events.ScenarioFinished).recorder.interactions.values()
    assert len(interactions) > 0
    get_requests = [i.request for i in interactions if i.request.method == "GET"]
    assert len(get_requests) > 0
    for request in get_requests:
        assert "/api/users/42?" in request.uri


def test_generation_config_in_explicit_examples(ctx, openapi2_base_url):
    schema = ctx.openapi.build_schema(
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
    schema = schemathesis.openapi.from_dict(schema).configure(
        base_url=openapi2_base_url,
        generation=GenerationConfig(
            with_security_parameters=False,
            headers=HeaderConfig(strategy=st.text(alphabet=st.characters(whitelist_characters="a", categories=()))),
        ),
    )
    stream = EventStream(
        schema,
        hypothesis_settings=settings(max_examples=10),
    ).execute()
    for event in stream.events:
        if isinstance(event, events.ScenarioFinished):
            for case in event.recorder.cases.values():
                for header in case.value.headers.values():
                    if header:
                        assert set(header) == {"a"}
            break
