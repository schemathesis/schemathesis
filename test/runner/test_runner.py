from __future__ import annotations

import base64
import json
import platform
from dataclasses import asdict
from unittest.mock import ANY

import hypothesis
import pytest
import requests
from aiohttp import web
from aiohttp.streams import EmptyStreamReader
from fastapi import FastAPI
from flask import Flask
from hypothesis import Phase, settings
from hypothesis import strategies as st
from requests.auth import HTTPDigestAuth

import schemathesis
from schemathesis import experimental
from schemathesis._hypothesis import add_examples
from schemathesis._override import CaseOverride
from schemathesis.checks import content_type_conformance, response_schema_conformance, status_code_conformance
from schemathesis.constants import RECURSIVE_REFERENCE_ERROR_MESSAGE, SCHEMATHESIS_TEST_CASE_HEADER, USER_AGENT
from schemathesis.generation import DataGenerationMethod, GenerationConfig, HeaderConfig
from schemathesis.models import Check, Status, TestResult
from schemathesis.runner import events, from_schema
from schemathesis.runner.impl import threadpool
from schemathesis.runner.impl.core import deduplicate_errors, has_too_many_responses_with_status
from schemathesis.specs.graphql import loaders as gql_loaders
from schemathesis.specs.openapi import loaders as oas_loaders
from schemathesis.stateful import Stateful
from schemathesis.transports.auth import get_requests_auth, get_wsgi_auth


def execute(schema, **options) -> events.Finished:
    *_, last = from_schema(schema, **options).execute()
    return last


def assert_request(
    app: web.Application, idx: int, method: str, path: str, headers: dict[str, str] | None = None
) -> None:
    request = get_incoming_requests(app)[idx]
    assert request.method == method
    if request.method == "GET":
        # Ref: #200
        # GET requests should not contain bodies
        if not isinstance(app, Flask):
            if not isinstance(request.content, EmptyStreamReader):
                assert request.content._read_nowait(-1) != b"{}"
        else:
            assert request.data == b""
    assert request.path == path
    if headers:
        for key, value in headers.items():
            assert request.headers.get(key) == value


def assert_not_request(app: web.Application, method: str, path: str) -> None:
    for request in get_incoming_requests(app):
        assert not (request.path == path and request.method == method)


def get_incoming_requests(app):
    if isinstance(app, Flask):
        return app.config["incoming_requests"]
    return app["incoming_requests"]


def get_schema_requests(app):
    if isinstance(app, Flask):
        return app.config["schema_requests"]
    return app["schema_requests"]


def assert_incoming_requests_num(app, number):
    assert len(get_incoming_requests(app)) == number


def assert_schema_requests_num(app, number):
    assert len(get_schema_requests(app)) == number


@pytest.fixture
def any_app(request, any_app_schema):
    return any_app_schema.app if any_app_schema.app is not None else request.getfixturevalue("app")


def test_execute_base_url_not_found(openapi3_base_url, schema_url, app):
    # When base URL is pointing to an unknown location
    schema = oas_loaders.from_uri(schema_url, base_url=f"{openapi3_base_url}/404/")
    execute(schema)
    # Then the runner should use this base
    # And they will not reach the application
    assert_incoming_requests_num(app, 0)


def test_execute(any_app, any_app_schema):
    # When the runner is executed against the default test app
    stats = execute(any_app_schema)

    # Then there are three executed cases
    # Two errors - the second one is a flakiness check
    headers = {"User-Agent": USER_AGENT}
    assert_schema_requests_num(any_app, 1)
    schema_requests = get_schema_requests(any_app)
    assert schema_requests[0].headers.get("User-Agent") == headers["User-Agent"]
    assert_incoming_requests_num(any_app, 3)
    assert_request(any_app, 0, "GET", "/api/failure", headers)
    assert_request(any_app, 1, "GET", "/api/failure", headers)
    assert_request(any_app, 2, "GET", "/api/success", headers)

    # And statistic is showing the breakdown of cases types
    assert stats.total == {"not_a_server_error": {Status.success: 1, Status.failure: 2, "total": 3}}


@pytest.mark.parametrize("workers", (1, 2))
def test_interactions(request, any_app_schema, workers):
    _, *others, _ = from_schema(any_app_schema, workers_num=workers, store_interactions=True).execute()
    base_url = (
        "http://localhost/api"
        if isinstance(any_app_schema.app, Flask)
        else request.getfixturevalue("openapi3_base_url")
    )

    # failure
    interactions = [
        event for event in others if isinstance(event, events.AfterExecution) and event.status == Status.failure
    ][0].result.interactions
    assert len(interactions) == 2
    failure = interactions[0]
    assert asdict(failure.request) == {
        "uri": f"{base_url}/failure",
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
    if isinstance(any_app_schema.app, Flask):
        assert failure.response.headers == {
            "Content-Type": ["text/html; charset=utf-8"],
            "Content-Length": ["265"],
        }
    else:
        assert failure.response.headers["Content-Type"] == ["text/plain; charset=utf-8"]
        assert failure.response.headers["Content-Length"] == ["26"]
    # success
    interactions = [
        event for event in others if isinstance(event, events.AfterExecution) and event.status == Status.success
    ][0].result.interactions
    assert len(interactions) == 1
    success = interactions[0]
    assert asdict(success.request) == {
        "uri": f"{base_url}/success",
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
    assert json.loads(base64.b64decode(success.response.body)) == {"success": True}
    assert success.response.encoding == "utf-8"
    if isinstance(any_app_schema.app, Flask):
        assert success.response.headers == {"Content-Type": ["application/json"], "Content-Length": ["17"]}
    else:
        assert success.response.headers["Content-Type"] == ["application/json; charset=utf-8"]


@pytest.mark.operations("root")
def test_asgi_interactions(fastapi_app):
    schema = oas_loaders.from_asgi("/openapi.json", fastapi_app, force_schema_version="30")
    _, _, _, _, _, *ev, _ = from_schema(schema, store_interactions=True).execute()
    interaction = ev[1].result.interactions[0]
    assert interaction.status == Status.success
    assert interaction.request.uri == "http://localhost/users"


@pytest.mark.operations("empty")
def test_empty_response_interaction(any_app_schema):
    # When there is a GET request and a response that doesn't return content (e.g. 204)
    _, *others, _ = from_schema(any_app_schema, store_interactions=True).execute()
    interactions = [event for event in others if isinstance(event, events.AfterExecution)][0].result.interactions
    for interaction in interactions:  # There could be multiple calls
        # Then the stored request has no body
        assert interaction.request.body is None
        # And its response as well
        assert interaction.response.body is None
        # And response encoding is missing
        assert interaction.response.encoding is None


@pytest.mark.openapi_version("3.0")
@pytest.mark.operations("empty_string")
def test_empty_string_response_interaction(any_app_schema):
    # When there is a response that returns payload of length 0
    _, *others, _ = from_schema(any_app_schema, store_interactions=True).execute()
    interactions = [event for event in others if isinstance(event, events.AfterExecution)][0].result.interactions
    for interaction in interactions:  # There could be multiple calls
        # Then the stored response body should be an empty string
        assert interaction.response.body == ""
        assert interaction.response.encoding == "utf-8"


def test_auth(any_app, any_app_schema):
    # When auth is specified as a tuple of 2 strings
    execute(any_app_schema, auth=("test", "test"))

    # Then each request should contain corresponding basic auth header
    assert_incoming_requests_num(any_app, 3)
    headers = {"Authorization": "Basic dGVzdDp0ZXN0"}
    assert_request(any_app, 0, "GET", "/api/failure", headers)
    assert_request(any_app, 1, "GET", "/api/failure", headers)
    assert_request(any_app, 2, "GET", "/api/success", headers)


@pytest.mark.parametrize("converter", (lambda x: x, lambda x: x + "/"))
def test_base_url(openapi3_base_url, schema_url, app, converter):
    base_url = converter(openapi3_base_url)
    # When `base_url` is specified explicitly with or without trailing slash
    schema = oas_loaders.from_uri(schema_url, base_url=base_url)
    execute(schema)

    # Then each request should reach the app in both cases
    assert_incoming_requests_num(app, 3)
    assert_request(app, 0, "GET", "/api/failure")
    assert_request(app, 1, "GET", "/api/failure")
    assert_request(app, 2, "GET", "/api/success")


# @pytest.mark.openapi_version("3.0")
def test_root_url():
    app = FastAPI(
        title="Silly",
        version="1.0.0",
    )

    @app.get("/")
    def empty():
        return {}

    def check(response, case):
        assert case.as_transport_kwargs()["url"] == "/"
        assert case.as_requests_kwargs()["url"] == "/"
        assert response.status_code == 200

    schema = oas_loaders.from_asgi("/openapi.json", app=app, force_schema_version="30")
    finished = execute(schema, checks=(check,))
    assert not finished.has_failures


def test_execute_with_headers(any_app, any_app_schema):
    # When headers are specified for the `execute` call
    headers = {"Authorization": "Bearer 123"}
    execute(any_app_schema, headers=headers)

    # Then each request should contain these headers
    assert_incoming_requests_num(any_app, 3)
    assert_request(any_app, 0, "GET", "/api/failure", headers)
    assert_request(any_app, 1, "GET", "/api/failure", headers)
    assert_request(any_app, 2, "GET", "/api/success", headers)


def test_execute_filter_endpoint(app, schema_url):
    schema = oas_loaders.from_uri(schema_url, endpoint=["success"])
    # When `endpoint` is passed in the `execute` call
    execute(schema)

    # Then the runner will make calls only to the specified path
    assert_incoming_requests_num(app, 1)
    assert_request(app, 0, "GET", "/api/success")
    assert_not_request(app, "GET", "/api/failure")


def test_execute_filter_method(app, schema_url):
    schema = oas_loaders.from_uri(schema_url, method="POST")
    # When `method` corresponds to a method that is not defined in the app schema
    execute(schema)
    # Then runner will not make any requests
    assert_incoming_requests_num(app, 0)


@pytest.mark.operations("slow")
def test_hypothesis_deadline(any_app, any_app_schema):
    # When `hypothesis_deadline` is passed in the `execute` call
    execute(any_app_schema, hypothesis_settings=hypothesis.settings(deadline=500))
    assert_incoming_requests_num(any_app, 1)
    assert_request(any_app, 0, "GET", "/api/slow")


@pytest.mark.operations("path_variable")
@pytest.mark.openapi_version("3.0")
def test_hypothesis_deadline_always_an_error(wsgi_app_schema, flask_app):
    flask_app.config["random_delay"] = 0.05
    # When the app responses are randomly slow
    *_, after, _ = list(from_schema(wsgi_app_schema, hypothesis_settings=hypothesis.settings(deadline=20)).execute())
    # Then it should always be marked as an error, not a flaky failure
    assert not after.result.is_flaky
    assert after.result.errors
    assert after.result.errors[0].exception.startswith("DeadlineExceeded: Test running time is too slow!")


@pytest.mark.operations("multipart")
def test_form_data(any_app, any_app_schema):
    def is_ok(response, case):
        assert response.status_code == 200

    def check_content(response, case):
        if isinstance(any_app, Flask):
            data = response.json
        else:
            data = response.json()
        assert isinstance(data["key"], str)
        assert data["value"].lstrip("-").isdigit()

    # When API operation specifies parameters with `in=formData`
    # Then responses should have 200 status, and not 415 (unsupported media type)
    results = execute(
        any_app_schema,
        checks=(is_ok, check_content),
        hypothesis_settings=hypothesis.settings(max_examples=3, deadline=None),
    )
    # And there should be no errors or failures
    assert not results.has_errors
    assert not results.has_failures
    # And the application should receive 3 requests as specified in `max_examples`
    assert_incoming_requests_num(any_app, 3)
    # And the Content-Type of incoming requests should be `multipart/form-data`
    incoming_requests = get_incoming_requests(any_app)
    assert incoming_requests[0].headers["Content-Type"].startswith("multipart/form-data")


@pytest.mark.operations("headers")
def test_headers_override(any_app_schema):
    def check_headers(response, case):
        if isinstance(any_app_schema.app, Flask):
            data = response.json
        else:
            data = response.json()
        assert data["X-Token"] == "test"

    *_, finished = from_schema(
        any_app_schema,
        checks=(check_headers,),
        headers={"X-Token": "test"},
        hypothesis_settings=hypothesis.settings(max_examples=1, deadline=None),
    ).execute()
    assert not finished.has_failures
    assert not finished.has_errors


@pytest.mark.operations("teapot")
def test_unknown_response_code(any_app_schema):
    # When API operation returns a status code, that is not listed in "responses"
    # And "status_code_conformance" is specified
    _, _, _, _, _, *others, finished = from_schema(
        any_app_schema,
        checks=(status_code_conformance,),
        hypothesis_settings=hypothesis.settings(max_examples=1, deadline=None),
    ).execute()
    # Then there should be a failure
    assert finished.has_failures
    check = others[1].result.checks[0]
    assert check.name == "status_code_conformance"
    assert check.value == Status.failure
    assert check.context.status_code == 418
    assert check.context.allowed_status_codes == [200]
    assert check.context.defined_status_codes == ["200"]


@pytest.mark.operations("failure")
def test_unknown_response_code_with_default(any_app_schema):
    # When API operation returns a status code, that is not listed in "responses", but there is a "default" response
    # And "status_code_conformance" is specified
    _, _, _, _, _, *others, finished = from_schema(
        any_app_schema,
        checks=(status_code_conformance,),
        hypothesis_settings=hypothesis.settings(max_examples=1, deadline=None),
    ).execute()
    # Then there should be no failure
    assert not finished.has_failures
    check = others[1].result.checks[0]
    assert check.name == "status_code_conformance"
    assert check.value == Status.success


@pytest.mark.operations("text")
def test_unknown_content_type(any_app_schema):
    # When API operation returns a response with content type, not specified in "produces"
    # And "content_type_conformance" is specified
    _, _, _, _, _, *others, finished = from_schema(
        any_app_schema,
        checks=(content_type_conformance,),
        hypothesis_settings=hypothesis.settings(max_examples=1, deadline=None),
    ).execute()
    # Then there should be a failure
    assert finished.has_failures
    check = others[1].result.checks[0]
    assert check.name == "content_type_conformance"
    assert check.value == Status.failure
    assert check.context.content_type == "text/plain"
    assert check.context.defined_content_types == ["application/json"]


@pytest.mark.operations("success")
def test_known_content_type(any_app_schema):
    # When API operation returns a response with a proper content type
    # And "content_type_conformance" is specified
    *_, finished = from_schema(
        any_app_schema,
        checks=(content_type_conformance,),
        hypothesis_settings=hypothesis.settings(max_examples=1, deadline=None),
    ).execute()
    # Then there should be no failures
    assert not finished.has_failures


@pytest.mark.operations("invalid_response")
def test_response_conformance_invalid(any_app_schema):
    # When API operation returns a response that doesn't conform to the schema
    # And "response_schema_conformance" is specified
    _, _, _, _, _, *others, finished = from_schema(
        any_app_schema,
        checks=(response_schema_conformance,),
        hypothesis_settings=hypothesis.settings(max_examples=1, deadline=None),
    ).execute()
    # Then there should be a failure
    assert finished.has_failures
    check = others[1].result.checks[-1]
    assert check.message == "Response violates schema"
    assert (
        check.context.message
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
    assert check.context.instance == {"random": "key"}
    assert check.context.instance_path == []
    assert check.context.schema == {
        "properties": {"success": {"type": "boolean"}},
        "required": ["success"],
        "type": "object",
    }
    assert check.context.schema_path == ["required"]
    assert check.context.validation_message == "'success' is a required property"


@pytest.mark.operations("success")
def test_response_conformance_valid(any_app_schema):
    # When API operation returns a response that conforms to the schema
    # And "response_schema_conformance" is specified
    results = execute(
        any_app_schema,
        checks=(response_schema_conformance,),
        hypothesis_settings=hypothesis.settings(max_examples=1, deadline=None),
    )
    # Then there should be no failures or errors
    assert not results.has_failures
    assert not results.has_errors


@pytest.mark.operations("recursive")
def test_response_conformance_recursive_valid(any_app_schema):
    # When API operation contains a response that have recursive references
    # And "response_schema_conformance" is specified
    results = execute(
        any_app_schema,
        checks=(response_schema_conformance,),
        hypothesis_settings=hypothesis.settings(max_examples=1, deadline=None),
    )
    # Then there should be no failures or errors
    assert not results.has_failures
    assert not results.has_errors


@pytest.mark.operations("text")
def test_response_conformance_text(any_app_schema):
    # When API operation returns a response that is not JSON
    # And "response_schema_conformance" is specified
    results = execute(
        any_app_schema,
        checks=(response_schema_conformance,),
        hypothesis_settings=hypothesis.settings(max_examples=1, deadline=None),
    )
    # Then the check should be ignored if the response headers are not application/json
    assert not results.has_failures
    assert not results.has_errors


@pytest.mark.operations("malformed_json")
def test_response_conformance_malformed_json(any_app_schema):
    # When API operation returns a response that contains a malformed JSON, but has a valid content type header
    # And "response_schema_conformance" is specified
    _, _, _, _, _, *others, finished = from_schema(
        any_app_schema,
        checks=(response_schema_conformance,),
        hypothesis_settings=hypothesis.settings(max_examples=1, deadline=None),
    ).execute()
    # Then there should be a failure
    assert finished.has_failures
    assert not finished.has_errors
    check = others[1].result.checks[-1]
    assert check.message == "JSON deserialization error"
    assert check.context.validation_message == "Expecting property name enclosed in double quotes"
    assert check.context.position == 1


@pytest.fixture()
def filter_path_parameters():
    # ".." and "." strings are treated specially, but this behavior is outside the test's scope
    # "" shouldn't be allowed as a valid path parameter

    def before_generate_path_parameters(context, strategy):
        return strategy.filter(
            lambda x: x["key"] not in ("..", ".", "", "/") and not (isinstance(x["key"], str) and "/" in x["key"])
        )

    schemathesis.hook(before_generate_path_parameters)
    yield


@pytest.mark.operations("path_variable")
@pytest.mark.usefixtures("filter_path_parameters")
def test_path_parameters_encoding(real_app_schema):
    # NOTE. WSGI and ASGI applications decodes %2F as / and returns 404
    # When API operation has a path parameter
    results = execute(
        real_app_schema,
        checks=(status_code_conformance,),
        hypothesis_settings=hypothesis.settings(derandomize=True, deadline=None),
    )
    # Then there should be no failures
    # since all path parameters are quoted
    assert not results.has_errors
    assert not results.has_failures


@pytest.mark.parametrize(
    "loader_options, from_schema_options",
    (
        ({"base_url": "http://127.0.0.1:1/"}, {}),
        ({}, {"hypothesis_settings": hypothesis.settings(deadline=1)}),
    ),
)
@pytest.mark.operations("slow")
def test_exceptions(schema_url, app, loader_options, from_schema_options):
    schema = oas_loaders.from_uri(schema_url, **loader_options)
    results = from_schema(schema, **from_schema_options).execute()
    assert any(event.status == Status.error for event in results if isinstance(event, events.AfterExecution))


@pytest.mark.operations("multipart")
def test_internal_exceptions(any_app_schema, mocker):
    # GH: #236
    # When there is an exception during the test
    # And Hypothesis consider this test as a flaky one
    mocker.patch("schemathesis.Case.call", side_effect=ValueError)
    _, _, _, _, _, *others, finished = from_schema(
        any_app_schema, hypothesis_settings=hypothesis.settings(max_examples=3, deadline=None)
    ).execute()
    # Then the execution result should indicate errors
    assert finished.has_errors
    # And an error from the buggy code should be collected
    exceptions = [i.exception.strip() for i in others[1].result.errors]
    assert "ValueError" in exceptions
    assert len(exceptions) == 1


@pytest.mark.operations("payload")
async def test_payload_explicit_example(any_app, any_app_schema):
    # When API operation has an example specified
    result = execute(any_app_schema, hypothesis_settings=hypothesis.settings(phases=[Phase.explicit], deadline=None))
    # Then run should be successful
    assert not result.has_errors
    assert not result.has_failures
    incoming_requests = get_incoming_requests(any_app)

    if isinstance(any_app, Flask):
        body = incoming_requests[0].json
    else:
        body = await incoming_requests[0].json()
    # And this example should be sent to the app
    assert body == {"name": "John"}


@pytest.mark.operations("payload")
async def test_explicit_example_disable(any_app, any_app_schema, mocker):
    # When API operation has an example specified
    # And the `explicit` phase is excluded
    spy = mocker.patch("schemathesis._hypothesis.add_examples", wraps=add_examples)
    result = execute(
        any_app_schema, hypothesis_settings=hypothesis.settings(max_examples=1, phases=[Phase.generate], deadline=None)
    )
    # Then run should be successful
    assert not result.has_errors
    assert not result.has_failures
    incoming_requests = get_incoming_requests(any_app)
    assert len(incoming_requests) == 1

    if isinstance(any_app, Flask):
        body = incoming_requests[0].json
    else:
        body = await incoming_requests[0].json()
    # And this example should NOT be used
    assert body != {"name": "John"}
    # And examples are not evaluated at all
    assert not spy.called


@pytest.mark.operations("plain_text_body")
def test_plain_text_body(any_app, any_app_schema):
    # When the expected payload is text/plain
    # Then the payload is not encoded as JSON
    def check_content(response, case):
        if isinstance(any_app, Flask):
            data = response.get_data()
        else:
            data = response.content
        assert case.body.encode("utf8") == data

    result = execute(
        any_app_schema, checks=(check_content,), hypothesis_settings=hypothesis.settings(max_examples=3, deadline=None)
    )
    assert not result.has_errors
    assert not result.has_failures


@pytest.mark.operations("invalid_path_parameter")
def test_invalid_path_parameter(schema_url):
    # When a path parameter is marked as not required
    # And schema validation is disabled
    schema = oas_loaders.from_uri(schema_url, validate_schema=False)
    *_, finished = from_schema(schema, hypothesis_settings=hypothesis.settings(max_examples=3, deadline=None)).execute()
    # Then Schemathesis enforces all path parameters to be required
    # And there should be no errors
    assert not finished.has_errors


@pytest.mark.operations("missing_path_parameter")
def test_missing_path_parameter(any_app_schema):
    # When a path parameter is missing
    _, _, _, _, _, *others, finished = from_schema(
        any_app_schema, hypothesis_settings=hypothesis.settings(max_examples=3, deadline=None)
    ).execute()
    # Then it leads to an error
    assert finished.has_errors
    assert "OperationSchemaError: Path parameter 'id' is not defined" in others[1].result.errors[0].exception


def test_get_requests_auth():
    assert isinstance(get_requests_auth(("test", "test"), "digest"), HTTPDigestAuth)


def test_get_wsgi_auth():
    with pytest.raises(ValueError, match="Digest auth is not supported for WSGI apps"):
        get_wsgi_auth(("test", "test"), "digest")


@pytest.mark.operations("failure", "multiple_failures")
def test_exit_first(any_app_schema):
    results = list(from_schema(any_app_schema, exit_first=True).execute())
    assert results[-1].has_failures is True
    assert results[-1].failed_count == 1


@pytest.mark.operations("failure", "multiple_failures", "unsatisfiable")
def test_max_failures(any_app_schema):
    # When `max_failures` is specified
    results = list(from_schema(any_app_schema, max_failures=2).execute())
    # Then the total numbers of failures and errors should not exceed this number
    result = results[-1]
    assert result.has_failures is True
    assert result.failed_count + result.errored_count == 2


@pytest.mark.operations("success")
def test_workers_num_regression(mocker, real_app_schema):
    # GH: 579
    spy = mocker.patch("schemathesis.runner.impl.ThreadPoolRunner", wraps=threadpool.ThreadPoolRunner)
    execute(real_app_schema, workers_num=5)
    assert spy.call_args[1]["workers_num"] == 5


@pytest.mark.parametrize("schema_path", ("petstore_v2.yaml", "petstore_v3.yaml"))
def test_url_joining(request, server, get_schema_path, schema_path):
    if schema_path == "petstore_v2.yaml":
        base_url = request.getfixturevalue("openapi2_base_url")
    else:
        base_url = request.getfixturevalue("openapi3_base_url")
    path = get_schema_path(schema_path)
    schema = oas_loaders.from_path(path, base_url=f"{base_url}/v3", endpoint="/pet/findByStatus")
    *_, after_execution, _ = from_schema(
        schema, hypothesis_settings=hypothesis.settings(max_examples=1, deadline=None)
    ).execute()
    assert after_execution.result.path == "/api/v3/pet/findByStatus"
    assert after_execution.result.checks[0].example.url == f"http://127.0.0.1:{server['port']}/api/v3/pet/findByStatus"


@pytest.mark.skipif(platform.system() == "Windows", reason="Fails on Windows due to recursion")
def test_skip_operations_with_recursive_references(schema_with_recursive_references):
    # When the test schema contains recursive references
    schema = oas_loaders.from_dict(schema_with_recursive_references)
    *_, after, _ = from_schema(schema).execute()
    # Then it causes an error with a proper error message
    assert after.status == Status.error
    assert RECURSIVE_REFERENCE_ERROR_MESSAGE in after.result.errors[0].exception


@pytest.mark.parametrize(
    "phases, expected, total_errors",
    (
        ([Phase.explicit, Phase.generate], "Failed to generate test cases for this API operation", 2),
        ([Phase.explicit], "Failed to generate test cases from examples for this API operation", 1),
    ),
)
def test_unsatisfiable_example(empty_open_api_3_schema, phases, expected, total_errors):
    # See GH-904
    # When filling missing properties during examples generation leads to unsatisfiable schemas
    empty_open_api_3_schema["paths"] = {
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
    # Then the testing process should not raise an internal error
    schema = oas_loaders.from_dict(empty_open_api_3_schema)
    *_, after, finished = from_schema(
        schema, hypothesis_settings=hypothesis.settings(max_examples=1, deadline=None, phases=phases)
    ).execute()
    # And the tests are failing because of the unsatisfiable schema
    assert finished.has_errors
    assert expected in after.result.errors[0].exception
    assert len(after.result.errors) == total_errors


@pytest.mark.parametrize(
    "phases, expected",
    (
        ([Phase.explicit, Phase.generate], "Schemathesis can't serialize data to any of the defined media types"),
        (
            [Phase.explicit],
            (
                "Failed to generate test cases from examples for this API operation because of "
                "unsupported payload media types"
            ),
        ),
    ),
)
def test_non_serializable_example(empty_open_api_3_schema, phases, expected):
    # When filling missing request body during examples generation leads to serialization error
    empty_open_api_3_schema["paths"] = {
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
    # Then the testing process should not raise an internal error
    schema = oas_loaders.from_dict(empty_open_api_3_schema)
    *_, after, finished = from_schema(
        schema, hypothesis_settings=hypothesis.settings(max_examples=1, deadline=None, phases=phases)
    ).execute()
    # And the tests are failing because of the serialization error
    assert finished.has_errors
    assert expected in after.result.errors[0].exception
    assert len(after.result.errors) == 1


@pytest.mark.parametrize(
    "phases, expected",
    (
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
    ),
)
def test_invalid_regex_example(empty_open_api_3_schema, phases, expected):
    # When filling missing properties during examples generation contains invalid regex
    empty_open_api_3_schema["paths"] = {
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
    # Then the testing process should not raise an internal error
    schema = oas_loaders.from_dict(empty_open_api_3_schema)
    *_, after, finished = from_schema(
        schema,
        hypothesis_settings=hypothesis.settings(max_examples=1, deadline=None, phases=phases),
    ).execute()
    # And the tests are failing because of the invalid regex error
    assert finished.has_errors
    assert expected in after.result.errors[0].exception
    assert len(after.result.errors) == 1


def test_invalid_header_in_example(empty_open_api_3_schema):
    empty_open_api_3_schema["paths"] = {
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
    # Then the testing process should not raise an internal error
    schema = oas_loaders.from_dict(empty_open_api_3_schema)
    *_, after, finished = from_schema(
        schema,
        hypothesis_settings=hypothesis.settings(max_examples=1, deadline=None),
        dry_run=True,
    ).execute()
    # And the tests are failing
    assert finished.has_errors
    assert (
        "Failed to generate test cases from examples for this API operation because of some header examples are invalid"
        in after.result.errors[0].exception
    )
    assert len(after.result.errors) == 1


@pytest.mark.operations("success")
def test_dry_run(any_app_schema):
    called = False

    def check(response, case):
        nonlocal called
        called = True

    # When the user passes `dry_run=True`
    execute(any_app_schema, checks=(check,), dry_run=True)
    # Then no requests should be sent & no responses checked
    assert not called


@pytest.mark.operations("root")
def test_dry_run_asgi(fastapi_app):
    called = False

    def check(response, case):
        nonlocal called
        called = True

    # When the user passes `dry_run=True`
    schema = oas_loaders.from_asgi("/openapi.json", fastapi_app, force_schema_version="30")
    execute(schema, checks=(check,), dry_run=True)
    # Then no requests should be sent & no responses checked
    assert not called


def test_connection_error(empty_open_api_3_schema):
    empty_open_api_3_schema["paths"] = {"/success": {"post": {"responses": {"200": {"description": "OK"}}}}}
    schema = oas_loaders.from_dict(empty_open_api_3_schema, base_url="http://127.0.0.1:1")
    *_, after, finished = from_schema(
        schema,
        hypothesis_settings=hypothesis.settings(max_examples=1, deadline=None),
    ).execute()
    # And the tests are failing
    assert finished.has_errors
    assert "Max retries exceeded with url" in after.result.errors[0].exception
    assert len(after.result.errors) == 1


@pytest.mark.operations("reserved")
def test_reserved_characters_in_operation_name(any_app_schema):
    # See GH-992

    def check(response, case):
        assert response.status_code == 200

    # When there is `:` in the API operation path
    result = execute(any_app_schema, checks=(check,))
    # Then it should be reachable
    assert not result.has_errors
    assert not result.has_failures


def test_count_operations(real_app_schema):
    # When `count_operations` is set to `False`
    event = next(from_schema(real_app_schema, count_operations=False).execute())
    # Then the total number of operations is not calculated in the `Initialized` event
    assert event.operations_count is None


def test_count_links(real_app_schema):
    # When `count_links` is set to `False`
    event = next(from_schema(real_app_schema, count_links=False).execute())
    # Then the total number of links is not calculated in the `Initialized` event
    assert event.links_count is None


def test_hypothesis_errors_propagation(empty_open_api_3_schema, openapi3_base_url):
    # See: GH-1046
    # When the operation contains a media type, that Schemathesis can't serialize
    # And there is still a supported media type
    empty_open_api_3_schema["paths"] = {
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

    max_examples = 10
    schema = oas_loaders.from_dict(empty_open_api_3_schema, base_url=openapi3_base_url)
    *_, after, finished = from_schema(
        schema, hypothesis_settings=hypothesis.settings(max_examples=max_examples, deadline=None)
    ).execute()
    # Then the test outcomes should not contain errors
    assert after.status == Status.success
    # And there should be requested amount of test examples
    assert len(after.result.checks) == max_examples
    assert not finished.has_failures
    assert not finished.has_errors


def test_encoding_octet_stream(empty_open_api_3_schema, openapi3_base_url):
    # See: GH-1134
    # When the operation contains the `application/octet-stream` media type
    # And has no `format: binary` in its schema
    empty_open_api_3_schema["paths"] = {
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
    schema = oas_loaders.from_dict(empty_open_api_3_schema, base_url=openapi3_base_url)
    *_, after, finished = from_schema(schema).execute()
    # Then the test outcomes should not contain errors
    # And it should not lead to encoding errors
    assert after.status == Status.success
    assert not finished.has_failures
    assert not finished.has_errors


def test_graphql(graphql_url):
    schema = gql_loaders.from_url(graphql_url)
    initialized, _, _, _, _, *other, finished = list(
        from_schema(schema, hypothesis_settings=hypothesis.settings(max_examples=5, deadline=None)).execute()
    )
    assert initialized.operations_count == 4
    assert finished.passed_count == 4
    for event, expected in zip(other, ["Query.getBooks", "Query.getBooks", "Query.getAuthors", "Query.getAuthors"]):
        assert event.verbose_name == expected
        if isinstance(event, events.AfterExecution):
            for check in event.result.checks:
                assert check.example.verbose_name == expected


@pytest.mark.operations("success")
def test_interrupted_in_test(openapi3_schema):
    # When an interrupt happens within a test body (check is called within a test body)
    def check(response, case):
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
def stop_worker(mocker):
    return mocker.spy(threadpool, "stop_worker")


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


def test_stop_event_stream_after_second_event(event_stream, workers_num, stop_worker):
    next(event_stream)
    next(event_stream)
    next(event_stream)
    next(event_stream)
    next(event_stream)
    assert isinstance(next(event_stream), events.BeforeExecution)
    event_stream.stop()
    assert isinstance(next(event_stream), events.Finished)
    assert next(event_stream, None) is None
    if workers_num > 1:
        stop_worker.assert_called()


def test_finish(event_stream):
    assert isinstance(next(event_stream), events.Initialized)
    event = event_stream.finish()
    assert isinstance(event, events.Finished)
    assert next(event_stream, None) is None


@pytest.mark.operations("success")
@pytest.mark.openapi_version("3.0")
def test_case_mutation(real_app_schema):
    # When two checks mutate the case

    def check1(response, case):
        case.headers = {"Foo": "BAR"}
        raise AssertionError("Bar!")

    def check2(response, case):
        case.headers = {"Foo": "BAZ"}
        raise AssertionError("Baz!")

    *_, event, _ = from_schema(real_app_schema, checks=[check1, check2]).execute()
    # Then these mutations should not interfere
    assert event.result.checks[0].example.headers["Foo"] == "BAR"
    assert event.result.checks[1].example.headers["Foo"] == "BAZ"


@pytest.mark.parametrize(
    "path, expected",
    (
        ("/foo}/", "Single '}' encountered in format string"),
        ("/{.format}/", "Replacement index 0 out of range for positional args tuple"),
    ),
)
def test_malformed_path_template(empty_open_api_3_schema, path, expected):
    # When schema contains a malformed path template
    empty_open_api_3_schema["paths"] = {path: {"get": {"responses": {"200": {"description": "OK"}}}}}
    schema = schemathesis.from_dict(empty_open_api_3_schema)
    # Then it should not cause a fatal error
    *_, event, _ = list(from_schema(schema).execute())
    assert event.status == Status.error
    # And should produce the proper error message
    assert (
        event.result.errors[0].exception == f"OperationSchemaError: Malformed path template: `{path}`\n\n  {expected}"
    )


@pytest.fixture
def result():
    return TestResult(
        method="POST",
        path="/users/",
        verbose_name="POST /users/",
        data_generation_method=DataGenerationMethod.positive,
    )


def make_check(status_code):
    response = requests.Response()
    response.status_code = status_code
    return Check(name="not_a_server_error", value=Status.success, response=response, elapsed=0.1, example=None)


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
    "parameters, expected",
    (
        ([{"in": "query", "name": "key", "required": True, "schema": {"type": "integer"}}], Status.success),
        ([], Status.skip),
    ),
)
def test_explicit_header_negative(empty_open_api_3_schema, parameters, expected):
    empty_open_api_3_schema["paths"] = {
        "/test": {
            "get": {
                "parameters": parameters,
                "security": [{"basicAuth": []}],
                "responses": {"200": {"description": ""}},
            }
        }
    }
    empty_open_api_3_schema["components"] = {"securitySchemes": {"basicAuth": {"type": "http", "scheme": "basic"}}}
    schema = schemathesis.from_dict(empty_open_api_3_schema, data_generation_methods=DataGenerationMethod.negative)
    *_, event, finished = list(
        from_schema(
            schema,
            headers={"Authorization": "TEST"},
            dry_run=True,
            hypothesis_settings=hypothesis.settings(max_examples=1),
        ).execute()
    )
    # There should not be unsatisfiable
    assert finished.errored_count == 0
    assert event.status == expected


def test_skip_non_negated_headers(empty_open_api_3_schema):
    empty_open_api_3_schema["paths"] = {
        "/test": {
            "get": {
                "parameters": [{"in": "header", "name": "If-Modified-Since", "schema": {"type": "string"}}],
                "responses": {"200": {"description": ""}},
            }
        }
    }
    schema = schemathesis.from_dict(empty_open_api_3_schema, data_generation_methods=DataGenerationMethod.negative)
    *_, event, finished = list(
        from_schema(
            schema,
            dry_run=True,
            hypothesis_settings=hypothesis.settings(max_examples=1),
        ).execute()
    )
    # There should not be unsatisfiable
    assert finished.errored_count == 0
    assert event.status == Status.skip


@pytest.mark.parametrize("derandomize", (True, False))
def test_use_the_same_seed(empty_open_api_3_schema, derandomize):
    definition = {"get": {"responses": {"200": {"description": ""}}}}
    empty_open_api_3_schema["paths"] = {"/first": definition, "/second": definition}
    schema = schemathesis.from_dict(empty_open_api_3_schema)
    after_execution = [
        event
        for event in from_schema(
            schema, dry_run=True, hypothesis_settings=hypothesis.settings(derandomize=derandomize)
        ).execute()
        if isinstance(event, events.AfterExecution)
    ]
    seed = after_execution[0].result.seed
    assert all(event.result.seed == seed for event in after_execution)


def test_deduplicate_errors():
    errors = [
        requests.exceptions.ConnectionError(
            "HTTPConnectionPool(host='127.0.0.1', port=808): Max retries exceeded with url: /snapshots/uploads/%5Dw2y%C3%9D (Caused by NewConnectionError('<urllib3.connection.HTTPConnection object at 0x795a23db4ce0>: Failed to establish a new connection: [Errno 111] Connection refused'))"
        ),
        requests.exceptions.ConnectionError(
            "HTTPConnectionPool(host='127.0.0.1', port=808): Max retries exceeded with url: /snapshots/uploads/%C3%8BEK (Caused by NewConnectionError('<urllib3.connection.HTTPConnection object at 0x795a23e2a6c0>: Failed to establish a new connection: [Errno 111] Connection refused'))"
        ),
    ]
    assert len(list(deduplicate_errors(errors))) == 1


STATEFUL_KWARGS = {
    "store_interactions": True,
    "stateful": Stateful.links,
    "hypothesis_settings": hypothesis.settings(max_examples=1, deadline=None, stateful_step_count=2),
}


@pytest.mark.openapi_version("3.0")
@pytest.mark.operations("get_user", "create_user", "update_user")
def test_stateful_auth(any_app_schema):
    experimental.STATEFUL_TEST_RUNNER.enable()
    experimental.STATEFUL_ONLY.enable()
    _, *_, after_execution, _ = from_schema(any_app_schema, auth=("admin", "password"), **STATEFUL_KWARGS).execute()
    interactions = after_execution.result.interactions
    assert len(interactions) > 0
    for interaction in interactions:
        assert interaction.request.headers["Authorization"] == ["Basic YWRtaW46cGFzc3dvcmQ="]


@pytest.mark.openapi_version("3.0")
@pytest.mark.operations("get_user", "create_user", "update_user")
def test_stateful_all_generation_methods(real_app_schema):
    experimental.STATEFUL_TEST_RUNNER.enable()
    experimental.STATEFUL_ONLY.enable()
    method = DataGenerationMethod.negative
    real_app_schema.data_generation_methods = [method]
    _, *_, after_execution, _ = from_schema(real_app_schema, **STATEFUL_KWARGS).execute()
    interactions = after_execution.result.interactions
    assert len(interactions) > 0
    for interaction in interactions:
        for check in interaction.checks:
            assert check.example.data_generation_method == method.as_short_name()


@pytest.mark.openapi_version("3.0")
@pytest.mark.operations("get_user", "create_user", "update_user")
def test_stateful_seed(real_app_schema):
    experimental.STATEFUL_TEST_RUNNER.enable()
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
    experimental.STATEFUL_TEST_RUNNER.enable()
    experimental.STATEFUL_ONLY.enable()
    _, *_, after_execution, _ = from_schema(
        real_app_schema,
        override=CaseOverride(path_parameters={"user_id": "42"}, headers={}, query={}, cookies={}),
        hypothesis_settings=hypothesis.settings(max_examples=40, deadline=None, stateful_step_count=2),
        store_interactions=True,
        stateful=Stateful.links,
    ).execute()
    interactions = after_execution.result.interactions
    assert len(interactions) > 0
    get_requests = [i.request for i in interactions if i.request.method == "GET"]
    assert len(get_requests) > 0
    for request in get_requests:
        assert "/api/users/42?" in request.uri


@pytest.mark.openapi_version("3.0")
@pytest.mark.operations("failure", "get_user", "create_user", "update_user")
def test_stateful_exit_first(real_app_schema):
    experimental.STATEFUL_TEST_RUNNER.enable()
    _, *ev, _ = from_schema(real_app_schema, exit_first=True, **STATEFUL_KWARGS).execute()
    assert not any(isinstance(event, events.StatefulEvent) for event in ev)


def test_generation_config_in_explicit_examples(empty_open_api_2_schema, openapi2_base_url):
    empty_open_api_2_schema["paths"] = {
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
    }
    schema = schemathesis.from_dict(empty_open_api_2_schema, base_url=openapi2_base_url)
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
                for header in check.example.headers.values():
                    if header:
                        assert set(header) == {"a"}
            break
