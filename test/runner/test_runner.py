from __future__ import annotations

import json
import platform
from dataclasses import asdict
from typing import TYPE_CHECKING
from unittest.mock import ANY

import hypothesis
import pytest
import requests
from aiohttp.streams import EmptyStreamReader
from fastapi import FastAPI
from hypothesis import Phase, settings
from hypothesis import strategies as st
from requests.auth import HTTPDigestAuth

import schemathesis
from schemathesis import experimental
from schemathesis._hypothesis._builder import add_examples
from schemathesis._override import CaseOverride
from schemathesis.checks import content_type_conformance, response_schema_conformance, status_code_conformance
from schemathesis.constants import RECURSIVE_REFERENCE_ERROR_MESSAGE, SCHEMATHESIS_TEST_CASE_HEADER
from schemathesis.core.transport import USER_AGENT
from schemathesis.generation import DataGenerationMethod, GenerationConfig, HeaderConfig
from schemathesis.runner import events, from_schema
from schemathesis.runner.config import NetworkConfig
from schemathesis.runner.models import Check, Status, TestResult
from schemathesis.runner.phases.unit._executor import has_too_many_responses_with_status
from schemathesis.specs.graphql import loaders as gql_loaders
from schemathesis.specs.openapi import loaders as oas_loaders
from schemathesis.stateful import Stateful
from schemathesis.transports.auth import get_requests_auth

if TYPE_CHECKING:
    from aiohttp import web


def execute(schema, **options) -> events.Finished:
    *_, last = from_schema(schema, **options).execute()
    return last


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
    schema = oas_loaders.from_uri(schema_url, base_url=f"{openapi3_base_url}/404/")
    execute(schema)
    # Then the runner should use this base
    # And they will not reach the application
    assert_incoming_requests_num(app, 0)


def test_execute(app, real_app_schema):
    # When the runner is executed against the default test app
    stats = execute(real_app_schema)

    # Then there are three executed cases
    # Two errors - the second one is a flakiness check
    headers = {"User-Agent": USER_AGENT}
    assert_schema_requests_num(app, 1)
    schema_requests = app["schema_requests"]
    assert schema_requests[0].headers.get("User-Agent") == headers["User-Agent"]
    assert_incoming_requests_num(app, 2)
    assert_request(app, 0, "GET", "/api/failure", headers)
    assert_request(app, 1, "GET", "/api/success", headers)

    # And statistic is showing the breakdown of cases types
    assert stats.results.total == {"not_a_server_error": {Status.success: 1, Status.failure: 1, "total": 2}}


@pytest.mark.parametrize("workers", [1, 2])
def test_interactions(openapi3_base_url, real_app_schema, workers):
    _, *others, _ = from_schema(real_app_schema, workers_num=workers).execute()

    # failure
    interactions = next(
        event for event in others if isinstance(event, events.AfterExecution) and event.status == Status.failure
    ).result.interactions
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
    assert failure.response.headers["Content-Type"] == ["text/plain; charset=utf-8"]
    assert failure.response.headers["Content-Length"] == ["26"]
    # success
    interactions = next(
        event for event in others if isinstance(event, events.AfterExecution) and event.status == Status.success
    ).result.interactions
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
    assert json.loads(success.response.body) == {"success": True}
    assert success.response.encoding == "utf-8"
    assert success.response.headers["Content-Type"] == ["application/json; charset=utf-8"]


@pytest.mark.operations("root")
def test_asgi_interactions(fastapi_app):
    schema = oas_loaders.from_asgi("/openapi.json", fastapi_app, force_schema_version="30")
    _, _, _, _, _, *ev, _ = from_schema(schema).execute()
    interaction = ev[1].result.interactions[0]
    assert interaction.status == Status.success
    assert interaction.request.uri == "http://localhost/users"


@pytest.mark.operations("empty")
def test_empty_response_interaction(real_app_schema):
    # When there is a GET request and a response that doesn't return content (e.g. 204)
    _, *others, _ = from_schema(real_app_schema).execute()
    interactions = next(event for event in others if isinstance(event, events.AfterExecution)).result.interactions
    for interaction in interactions:  # There could be multiple calls
        # Then the stored request has no body
        assert interaction.request.body is None
        # And its response as well
        assert interaction.response.body is None
        # And response encoding is missing
        assert interaction.response.encoding is None


@pytest.mark.openapi_version("3.0")
@pytest.mark.operations("empty_string")
def test_empty_string_response_interaction(real_app_schema):
    # When there is a response that returns payload of length 0
    _, *others, _ = from_schema(real_app_schema).execute()
    interactions = next(event for event in others if isinstance(event, events.AfterExecution)).result.interactions
    for interaction in interactions:  # There could be multiple calls
        # Then the stored response body should be an empty string
        assert interaction.response.body == b""
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
    schema = oas_loaders.from_uri(schema_url, base_url=base_url)
    execute(schema)

    # Then each request should reach the app in both cases
    assert_incoming_requests_num(app, 2)
    assert_request(app, 0, "GET", "/api/failure")
    assert_request(app, 1, "GET", "/api/success")


# @pytest.mark.openapi_version("3.0")
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

    schema = oas_loaders.from_asgi("/openapi.json", app=app, force_schema_version="30")
    finished = execute(schema, checks=(check,))
    assert not finished.results.has_failures


def test_execute_with_headers(app, real_app_schema):
    # When headers are specified for the `execute` call
    headers = {"Authorization": "Bearer 123"}
    execute(real_app_schema, network=NetworkConfig(headers=headers))

    # Then each request should contain these headers
    assert_incoming_requests_num(app, 2)
    assert_request(app, 0, "GET", "/api/failure", headers)
    assert_request(app, 1, "GET", "/api/success", headers)


def test_execute_filter_endpoint(app, schema_url):
    schema = oas_loaders.from_uri(schema_url).include(path_regex="success")
    # When `endpoint` is passed in the `execute` call
    execute(schema)

    # Then the runner will make calls only to the specified path
    assert_incoming_requests_num(app, 1)
    assert_request(app, 0, "GET", "/api/success")
    assert_not_request(app, "GET", "/api/failure")


def test_execute_filter_method(app, schema_url):
    schema = oas_loaders.from_uri(schema_url).include(method="POST")
    # When `method` corresponds to a method that is not defined in the app schema
    execute(schema)
    # Then runner will not make any requests
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
    finiished = execute(
        real_app_schema,
        checks=(is_ok, check_content),
        hypothesis_settings=hypothesis.settings(max_examples=3, deadline=None),
    )
    # And there should be no errors or failures
    assert not finiished.results.has_errors
    assert not finiished.results.has_failures
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

    *_, finished = from_schema(
        real_app_schema,
        checks=(check_headers,),
        network=NetworkConfig(headers={"X-Token": "test"}),
        hypothesis_settings=hypothesis.settings(max_examples=1, deadline=None),
    ).execute()
    assert not finished.results.has_failures
    assert not finished.results.has_errors


@pytest.mark.operations("teapot")
def test_unknown_response_code(real_app_schema):
    # When API operation returns a status code, that is not listed in "responses"
    # And "status_code_conformance" is specified
    _, _, _, _, _, *others, finished = from_schema(
        real_app_schema,
        checks=(status_code_conformance,),
        hypothesis_settings=hypothesis.settings(max_examples=1, deadline=None),
    ).execute()
    # Then there should be a failure
    assert finished.results.has_failures
    check = others[1].result.checks[0]
    assert check.name == "status_code_conformance"
    assert check.value == Status.failure
    assert check.failure.status_code == 418
    assert check.failure.allowed_status_codes == [200]
    assert check.failure.defined_status_codes == ["200"]


@pytest.mark.operations("failure")
def test_unknown_response_code_with_default(real_app_schema):
    # When API operation returns a status code, that is not listed in "responses", but there is a "default" response
    # And "status_code_conformance" is specified
    _, _, _, _, _, *others, finished = from_schema(
        real_app_schema,
        checks=(status_code_conformance,),
        hypothesis_settings=hypothesis.settings(max_examples=1, deadline=None),
    ).execute()
    # Then there should be no failure
    assert not finished.results.has_failures
    check = others[1].result.checks[0]
    assert check.name == "status_code_conformance"
    assert check.value == Status.success


@pytest.mark.operations("text")
def test_unknown_content_type(real_app_schema):
    # When API operation returns a response with content type, not specified in "produces"
    # And "content_type_conformance" is specified
    _, _, _, _, _, *others, finished = from_schema(
        real_app_schema,
        checks=(content_type_conformance,),
        hypothesis_settings=hypothesis.settings(max_examples=1, deadline=None),
    ).execute()
    # Then there should be a failure
    assert finished.results.has_failures
    check = others[1].result.checks[0]
    assert check.name == "content_type_conformance"
    assert check.value == Status.failure
    assert check.failure.content_type == "text/plain"
    assert check.failure.defined_content_types == ["application/json"]


@pytest.mark.operations("success")
def test_known_content_type(real_app_schema):
    # When API operation returns a response with a proper content type
    # And "content_type_conformance" is specified
    *_, finished = from_schema(
        real_app_schema,
        checks=(content_type_conformance,),
        hypothesis_settings=hypothesis.settings(max_examples=1, deadline=None),
    ).execute()
    # Then there should be no failures
    assert not finished.results.has_failures


@pytest.mark.operations("invalid_response")
def test_response_conformance_invalid(real_app_schema):
    # When API operation returns a response that doesn't conform to the schema
    # And "response_schema_conformance" is specified
    _, _, _, _, _, *others, finished = from_schema(
        real_app_schema,
        checks=(response_schema_conformance,),
        hypothesis_settings=hypothesis.settings(max_examples=1, deadline=None),
    ).execute()
    # Then there should be a failure
    assert finished.results.has_failures
    check = others[1].result.checks[-1]
    assert check.failure.title == "Response violates schema"
    assert (
        check.failure.message
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
    assert check.failure.instance == {"random": "key"}
    assert check.failure.instance_path == []
    assert check.failure.schema == {
        "properties": {"success": {"type": "boolean"}},
        "required": ["success"],
        "type": "object",
    }
    assert check.failure.schema_path == ["required"]
    assert check.failure.validation_message == "'success' is a required property"


@pytest.mark.operations("success")
def test_response_conformance_valid(real_app_schema):
    # When API operation returns a response that conforms to the schema
    # And "response_schema_conformance" is specified
    finished = execute(
        real_app_schema,
        checks=(response_schema_conformance,),
        hypothesis_settings=hypothesis.settings(max_examples=1, deadline=None),
    )
    # Then there should be no failures or errors
    assert not finished.results.has_failures
    assert not finished.results.has_errors


@pytest.mark.operations("recursive")
def test_response_conformance_recursive_valid(real_app_schema):
    # When API operation contains a response that have recursive references
    # And "response_schema_conformance" is specified
    finished = execute(
        real_app_schema,
        checks=(response_schema_conformance,),
        hypothesis_settings=hypothesis.settings(max_examples=1, deadline=None),
    )
    # Then there should be no failures or errors
    assert not finished.results.has_failures
    assert not finished.results.has_errors


@pytest.mark.operations("text")
def test_response_conformance_text(real_app_schema):
    # When API operation returns a response that is not JSON
    # And "response_schema_conformance" is specified
    finished = execute(
        real_app_schema,
        checks=(response_schema_conformance,),
        hypothesis_settings=hypothesis.settings(max_examples=1, deadline=None),
    )
    # Then the check should be ignored if the response headers are not application/json
    assert not finished.results.has_failures
    assert not finished.results.has_errors


@pytest.mark.operations("malformed_json")
def test_response_conformance_malformed_json(real_app_schema):
    # When API operation returns a response that contains a malformed JSON, but has a valid content type header
    # And "response_schema_conformance" is specified
    _, _, _, _, _, *others, finished = from_schema(
        real_app_schema,
        checks=(response_schema_conformance,),
        hypothesis_settings=hypothesis.settings(max_examples=1, deadline=None),
    ).execute()
    # Then there should be a failure
    assert finished.results.has_failures
    assert not finished.results.has_errors
    check = others[1].result.checks[-1]
    assert check.failure.title == "JSON deserialization error"
    assert check.failure.validation_message == "Expecting property name enclosed in double quotes"
    assert check.failure.position == 1


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
    finished = execute(
        real_app_schema,
        checks=(status_code_conformance,),
        hypothesis_settings=hypothesis.settings(derandomize=True, deadline=None),
    )
    # Then there should be no failures
    # since all path parameters are quoted
    assert not finished.results.has_errors, finished
    assert not finished.results.has_failures, finished


@pytest.mark.parametrize(
    ("loader_options", "from_schema_options"),
    [
        ({"base_url": "http://127.0.0.1:1/"}, {}),
        ({}, {"hypothesis_settings": hypothesis.settings(deadline=1)}),
    ],
)
@pytest.mark.operations("slow")
def test_exceptions(schema_url, app, loader_options, from_schema_options):
    schema = oas_loaders.from_uri(schema_url, **loader_options)
    results = from_schema(schema, **from_schema_options).execute()
    assert any(event.status == Status.error for event in results if isinstance(event, events.AfterExecution))


@pytest.mark.operations("multipart")
def test_internal_exceptions(real_app_schema, mocker):
    # GH: #236
    # When there is an exception during the test
    # And Hypothesis consider this test as a flaky one
    mocker.patch("schemathesis.Case.call", side_effect=ValueError)
    _, _, _, _, _, *others, finished = from_schema(
        real_app_schema, hypothesis_settings=hypothesis.settings(max_examples=3, deadline=None)
    ).execute()
    # Then the execution result should indicate errors
    assert finished.results.has_errors
    # And an error from the buggy code should be collected
    exceptions = [str(error) for error in others[1].result.errors]
    assert "ValueError" in exceptions
    assert len(exceptions) == 1


@pytest.mark.operations("payload")
async def test_payload_explicit_example(app, real_app_schema):
    # When API operation has an example specified
    result = execute(real_app_schema, hypothesis_settings=hypothesis.settings(phases=[Phase.explicit], deadline=None))
    # Then run should be successful
    assert not result.results.has_errors
    assert not result.results.has_failures
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
    schema = oas_loaders.from_dict(schema, base_url=openapi3_base_url)
    *_, after, _ = from_schema(
        schema,
        hypothesis_settings=hypothesis.settings(max_examples=1, deadline=None, phases=[Phase.explicit]),
    ).execute()
    assert [check.case.path_parameters for check in after.result.checks] == [
        {"itemId": "456789"},
        {"itemId": "123456"},
    ]


@pytest.mark.operations("payload")
async def test_explicit_example_disable(app, real_app_schema, mocker):
    # When API operation has an example specified
    # And the `explicit` phase is excluded
    spy = mocker.patch("schemathesis._hypothesis._builder.add_examples", wraps=add_examples)
    result = execute(
        real_app_schema, hypothesis_settings=hypothesis.settings(max_examples=1, phases=[Phase.generate], deadline=None)
    )
    # Then run should be successful
    assert not result.results.has_errors
    assert not result.results.has_failures
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

    result = execute(
        real_app_schema, checks=(check_content,), hypothesis_settings=hypothesis.settings(max_examples=3, deadline=None)
    )
    assert not result.results.has_errors
    assert not result.results.has_failures


@pytest.mark.operations("invalid_path_parameter")
def test_invalid_path_parameter(schema_url):
    # When a path parameter is marked as not required
    # And schema validation is disabled
    schema = oas_loaders.from_uri(schema_url, validate_schema=False)
    *_, finished = from_schema(schema, hypothesis_settings=hypothesis.settings(max_examples=3, deadline=None)).execute()
    # Then Schemathesis enforces all path parameters to be required
    # And there should be no errors
    assert not finished.results.has_errors


@pytest.mark.operations("missing_path_parameter")
def test_missing_path_parameter(real_app_schema):
    # When a path parameter is missing
    _, _, _, _, _, *others, finished = from_schema(
        real_app_schema, hypothesis_settings=hypothesis.settings(max_examples=3, deadline=None)
    ).execute()
    # Then it leads to an error
    assert finished.results.has_errors
    assert "OperationSchemaError: Path parameter 'id' is not defined" in str(others[1].result.errors[0])


def test_get_requests_auth():
    assert isinstance(get_requests_auth(("test", "test"), "digest"), HTTPDigestAuth)


@pytest.mark.operations("failure", "multiple_failures", "unsatisfiable")
def test_max_failures(real_app_schema):
    # When `max_failures` is specified
    results = list(from_schema(real_app_schema, max_failures=2).execute())
    # Then the total numbers of failures and errors should not exceed this number
    result = results[-1]
    assert result.results.has_failures is True
    assert result.results.failed_count + result.results.errored_count == 2


@pytest.mark.parametrize("schema_path", ["petstore_v2.yaml", "petstore_v3.yaml"])
def test_url_joining(request, server, get_schema_path, schema_path):
    if schema_path == "petstore_v2.yaml":
        base_url = request.getfixturevalue("openapi2_base_url")
    else:
        base_url = request.getfixturevalue("openapi3_base_url")
    path = get_schema_path(schema_path)
    schema = oas_loaders.from_path(path, base_url=f"{base_url}/v3").include(path_regex="/pet/findByStatus")
    *_, after_execution, _ = from_schema(
        schema, hypothesis_settings=hypothesis.settings(max_examples=1, deadline=None)
    ).execute()
    assert after_execution.result.verbose_name == "GET /api/v3/pet/findByStatus"
    assert (
        after_execution.result.checks[0].case.get_full_url()
        == f"http://127.0.0.1:{server['port']}/api/v3/pet/findByStatus"
    )


@pytest.mark.skipif(platform.system() == "Windows", reason="Fails on Windows due to recursion")
def test_skip_operations_with_recursive_references(schema_with_recursive_references):
    # When the test schema contains recursive references
    schema = oas_loaders.from_dict(schema_with_recursive_references)
    *_, after, _ = from_schema(schema).execute()
    # Then it causes an error with a proper error message
    assert after.status == Status.error
    assert RECURSIVE_REFERENCE_ERROR_MESSAGE in str(after.result.errors[0])


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
    schema = oas_loaders.from_dict(schema)
    *_, after, finished = from_schema(
        schema, hypothesis_settings=hypothesis.settings(max_examples=1, deadline=None, phases=phases)
    ).execute()
    # And the tests are failing because of the unsatisfiable schema
    assert finished.results.has_errors
    assert expected in str(after.result.errors[0])
    assert len(after.result.errors) == total_errors


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
    schema = oas_loaders.from_dict(schema)
    *_, after, finished = from_schema(
        schema, hypothesis_settings=hypothesis.settings(max_examples=1, deadline=None, phases=phases)
    ).execute()
    # And the tests are failing because of the serialization error
    assert finished.results.has_errors
    assert expected in str(after.result.errors[0])
    assert len(after.result.errors) == 1


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
    schema = oas_loaders.from_dict(schema)
    *_, after, finished = from_schema(
        schema,
        hypothesis_settings=hypothesis.settings(max_examples=1, deadline=None, phases=phases),
    ).execute()
    # And the tests are failing because of the invalid regex error
    assert finished.results.has_errors
    assert expected in str(after.result.errors[0])
    assert len(after.result.errors) == 1


def test_invalid_header_in_example(ctx):
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
    schema = oas_loaders.from_dict(schema)
    *_, after, finished = from_schema(
        schema,
        hypothesis_settings=hypothesis.settings(max_examples=1, deadline=None),
        dry_run=True,
    ).execute()
    # And the tests are failing
    assert finished.results.has_errors
    assert (
        "Failed to generate test cases from examples for this API operation because of some header examples are invalid"
        in str(after.result.errors[0])
    )
    assert len(after.result.errors) == 1


@pytest.mark.operations("success")
def test_dry_run(real_app_schema):
    called = False

    def check(ctx, response, case):
        nonlocal called
        called = True

    # When the user passes `dry_run=True`
    execute(real_app_schema, checks=(check,), dry_run=True)
    # Then no requests should be sent & no responses checked
    assert not called


@pytest.mark.operations("root")
def test_dry_run_asgi(fastapi_app):
    called = False

    def check(ctx, response, case):
        nonlocal called
        called = True

    # When the user passes `dry_run=True`
    schema = oas_loaders.from_asgi("/openapi.json", fastapi_app, force_schema_version="30")
    execute(schema, checks=(check,), dry_run=True)
    # Then no requests should be sent & no responses checked
    assert not called


def test_connection_error(ctx):
    schema = ctx.openapi.build_schema({"/success": {"post": {"responses": {"200": {"description": "OK"}}}}})
    schema = oas_loaders.from_dict(schema, base_url="http://127.0.0.1:1")
    *_, after, finished = from_schema(
        schema,
        hypothesis_settings=hypothesis.settings(max_examples=1, deadline=None),
    ).execute()
    # And the tests are failing
    assert finished.results.has_errors
    assert "Max retries exceeded with url" in str(after.result.errors[0])
    assert len(after.result.errors) == 1


@pytest.mark.operations("reserved")
def test_reserved_characters_in_operation_name(real_app_schema):
    # See GH-992

    def check(ctx, response, case):
        assert response.status_code == 200

    # When there is `:` in the API operation path
    result = execute(real_app_schema, checks=(check,))
    # Then it should be reachable
    assert not result.results.has_errors
    assert not result.results.has_failures


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
    schema = oas_loaders.from_dict(schema, base_url=openapi3_base_url)
    *_, after, finished = from_schema(
        schema, hypothesis_settings=hypothesis.settings(max_examples=max_examples, deadline=None)
    ).execute()
    # Then the test outcomes should not contain errors
    assert after.status == Status.success
    # And there should be requested amount of test examples
    assert len(after.result.checks) == max_examples
    assert not finished.results.has_failures
    assert not finished.results.has_errors


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
    schema = oas_loaders.from_dict(schema, base_url=openapi3_base_url)
    *_, after, finished = from_schema(schema).execute()
    # Then the test outcomes should not contain errors
    # And it should not lead to encoding errors
    assert after.status == Status.success
    assert not finished.results.has_failures
    assert not finished.results.has_errors


def test_graphql(graphql_url):
    schema = gql_loaders.from_url(graphql_url)
    initialized, _, _, _, _, *other, finished = list(
        from_schema(schema, hypothesis_settings=hypothesis.settings(max_examples=5, deadline=None)).execute()
    )
    assert initialized.operations_count == 4
    assert finished.results.passed_count == 4
    for event, expected in zip(other, ["Query.getBooks", "Query.getBooks", "Query.getAuthors", "Query.getAuthors"]):
        if isinstance(event, events.AfterExecution):
            assert event.result.verbose_name == expected
            for check in event.result.checks:
                assert check.case.operation.verbose_name == expected
            else:
                assert event.result.verbose_name == expected


@pytest.mark.operations("success")
def test_interrupted_in_test(openapi3_schema):
    # When an interrupt happens within a test body (check is called within a test body)
    def check(ctx, response, case):
        raise KeyboardInterrupt

    *_, event, _ = from_schema(openapi3_schema, checks=(check,)).execute()
    # Then the `Interrupted` event should be emitted
    assert isinstance(event, events.Interrupted)


@pytest.mark.operations("success")
def test_interrupted_outside_test(mocker, openapi3_schema):
    # See GH-1325
    # When an interrupt happens outside a test body
    mocker.patch("schemathesis.runner.events.AfterExecution.from_result", side_effect=KeyboardInterrupt)

    try:
        *_, event, _ = from_schema(openapi3_schema).execute()
        # Then the `Interrupted` event should be emitted
        assert isinstance(event, events.Interrupted)
    except KeyboardInterrupt:
        pytest.fail("KeyboardInterrupt should be handled")


@pytest.fixture(params=[1, 2], ids=["single-worker", "multi-worker"])
def workers_num(request):
    return request.param


@pytest.fixture
def runner(workers_num, swagger_20):
    return from_schema(swagger_20, workers_num=workers_num)


@pytest.fixture
def event_stream(runner):
    return runner.execute()


def test_stop_event_stream(event_stream):
    assert isinstance(next(event_stream), events.Initialized)
    event_stream.stop()
    assert isinstance(next(event_stream), events.Finished)
    assert next(event_stream, None) is None


def test_stop_event_stream_immediately(event_stream):
    event_stream.stop()
    assert isinstance(next(event_stream), events.Finished)
    assert next(event_stream, None) is None


def test_stop_event_stream_after_second_event(event_stream):
    next(event_stream)
    next(event_stream)
    next(event_stream)
    next(event_stream)
    next(event_stream)
    assert isinstance(next(event_stream), events.BeforeExecution)
    event_stream.stop()
    assert isinstance(next(event_stream), events.Finished)
    assert next(event_stream, None) is None


def test_finish(event_stream):
    assert isinstance(next(event_stream), events.Initialized)
    event = event_stream.finish()
    assert isinstance(event, events.Finished)
    assert next(event_stream, None) is None


@pytest.mark.operations("success")
@pytest.mark.openapi_version("3.0")
def test_case_mutation(real_app_schema):
    # When two checks mutate the case

    def check1(ctx, response, case):
        case.headers = {"Foo": "BAR"}
        raise AssertionError("Bar!")

    def check2(ctx, response, case):
        case.headers = {"Foo": "BAZ"}
        raise AssertionError("Baz!")

    *_, event, _ = from_schema(real_app_schema, checks=[check1, check2]).execute()
    # Then these mutations should not interfere
    assert event.result.checks[0].case.headers["Foo"] == "BAR"
    assert event.result.checks[1].case.headers["Foo"] == "BAZ"


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
    schema = schemathesis.from_dict(schema)
    # Then it should not cause a fatal error
    *_, event, _ = list(from_schema(schema).execute())
    assert event.status == Status.error
    # And should produce the proper error message
    assert str(event.result.errors[0]) == f"OperationSchemaError: Malformed path template: `{path}`\n\n  {expected}"


@pytest.fixture
def result():
    return TestResult(verbose_name="POST /users/")


def make_check(status_code):
    response = requests.Response()
    response.status_code = status_code
    return Check(name="not_a_server_error", value=Status.success, request=None, response=response, case=None)


def test_authorization_warning_no_checks(result):
    # When there are no checks
    # Then the warning should not be added
    assert not has_too_many_responses_with_status(result, 401)


def test_authorization_warning_missing_threshold(result):
    # When there are not enough 401 responses to meet the threshold
    result.checks.extend(
        [
            make_check(201),
            make_check(401),
        ]
    )
    # Then the warning should not be added
    assert not has_too_many_responses_with_status(result, 401)


@pytest.mark.parametrize(
    ("parameters", "expected"),
    [
        ([{"in": "query", "name": "key", "required": True, "schema": {"type": "integer"}}], Status.success),
        ([], Status.skip),
    ],
)
def test_explicit_header_negative(ctx, parameters, expected):
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
    schema = schemathesis.from_dict(schema, data_generation_methods=DataGenerationMethod.negative)
    *_, event, finished = list(
        from_schema(
            schema,
            network=NetworkConfig(headers={"Authorization": "TEST"}),
            dry_run=True,
            hypothesis_settings=hypothesis.settings(max_examples=1),
        ).execute()
    )
    # There should not be unsatisfiable
    assert finished.results.errored_count == 0
    assert event.status == expected


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
    schema = schemathesis.from_dict(schema, data_generation_methods=DataGenerationMethod.negative)
    *_, event, finished = list(
        from_schema(
            schema,
            dry_run=True,
            hypothesis_settings=hypothesis.settings(max_examples=1),
        ).execute()
    )
    # There should not be unsatisfiable
    assert finished.results.errored_count == 0
    assert event.status == Status.skip


STATEFUL_KWARGS = {
    "stateful": Stateful.links,
    "hypothesis_settings": hypothesis.settings(max_examples=1, deadline=None, stateful_step_count=2),
}


@pytest.mark.openapi_version("3.0")
@pytest.mark.operations("get_user", "create_user", "update_user")
def test_stateful_auth(real_app_schema):
    experimental.STATEFUL_ONLY.enable()
    _, *_, after_execution, _ = from_schema(
        real_app_schema, network=NetworkConfig(auth=("admin", "password")), **STATEFUL_KWARGS
    ).execute()
    interactions = after_execution.result.interactions
    assert len(interactions) > 0
    for interaction in interactions:
        assert interaction.request.headers["Authorization"] == ["Basic YWRtaW46cGFzc3dvcmQ="]


@pytest.mark.openapi_version("3.0")
@pytest.mark.operations("get_user", "create_user", "update_user")
def test_stateful_all_generation_methods(real_app_schema):
    experimental.STATEFUL_ONLY.enable()
    method = DataGenerationMethod.negative
    real_app_schema.data_generation_methods = [method]
    _, *_, after_execution, _ = from_schema(real_app_schema, **STATEFUL_KWARGS).execute()
    interactions = after_execution.result.interactions
    assert len(interactions) > 0
    for interaction in interactions:
        for check in interaction.checks:
            assert check.case.data_generation_method == method


@pytest.mark.openapi_version("3.0")
@pytest.mark.operations("get_user", "create_user", "update_user")
def test_stateful_seed(real_app_schema):
    experimental.STATEFUL_ONLY.enable()
    requests = []
    for _ in range(3):
        _, *_, after_execution, _ = from_schema(real_app_schema, seed=42, **STATEFUL_KWARGS).execute()
        current = []
        for interaction in after_execution.result.interactions:
            data = interaction.request.__dict__
            del data["headers"][SCHEMATHESIS_TEST_CASE_HEADER]
            current.append(data)
        requests.append(current)
    assert requests[0][0] == requests[1][0] == requests[2][0]


@pytest.mark.openapi_version("3.0")
@pytest.mark.operations("get_user", "create_user", "update_user")
def test_stateful_override(real_app_schema):
    experimental.STATEFUL_ONLY.enable()
    _, *_, after_execution, _ = from_schema(
        real_app_schema,
        override=CaseOverride(path_parameters={"user_id": "42"}, headers={}, query={}, cookies={}),
        hypothesis_settings=hypothesis.settings(max_examples=40, deadline=None, stateful_step_count=2),
        stateful=Stateful.links,
    ).execute()
    interactions = after_execution.result.interactions
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
    schema = schemathesis.from_dict(schema, base_url=openapi2_base_url)
    runner = schemathesis.runner.from_schema(
        schema,
        hypothesis_settings=settings(max_examples=10),
        generation_config=GenerationConfig(
            with_security_parameters=False,
            headers=HeaderConfig(strategy=st.text(alphabet=st.characters(whitelist_characters="a", categories=()))),
        ),
    )
    for event in runner.execute():
        if isinstance(event, events.AfterExecution):
            for check in event.result.checks:
                for header in check.case.headers.values():
                    if header:
                        assert set(header) == {"a"}
            break
