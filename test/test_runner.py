from typing import Dict, Optional

import pytest
from aiohttp import web
from aiohttp.streams import EmptyStreamReader

from schemathesis.constants import __version__
from schemathesis.models import Status
from schemathesis.runner import (
    content_type_conformance,
    events,
    execute,
    get_base_url,
    prepare,
    status_code_conformance,
)


def assert_request(
    app: web.Application, idx: int, method: str, path: str, headers: Optional[Dict[str, str]] = None
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


def test_execute_base_url_not_found(base_url, schema_url, app):
    # When base URL is pointing to an unknown location
    execute(schema_url, loader_options={"base_url": f"{base_url}/404/"})
    # Then the runner should use this base
    # And they will not reach the application
    assert len(app["incoming_requests"]) == 0


def test_execute_base_url_found(base_url, schema_url, app):
    # When base_url is specified
    execute(schema_url, loader_options={"base_url": base_url})
    # Then it should be used by the runner
    assert len(app["incoming_requests"]) == 3


def test_execute(schema_url, app):
    # When the runner is executed against the default test app
    stats = execute(schema_url)

    # Then there are three executed cases
    # Two errors - the second one is a flakiness check
    headers = {"User-Agent": f"schemathesis/{__version__}"}
    assert len(app["schema_requests"]) == 1
    assert app["schema_requests"][0].headers.get("User-Agent") == headers["User-Agent"]
    assert len(app["incoming_requests"]) == 3
    assert_request(app, 0, "GET", "/api/failure", headers)
    assert_request(app, 1, "GET", "/api/failure", headers)
    assert_request(app, 2, "GET", "/api/success", headers)

    # And statistic is showing the breakdown of cases types
    assert stats.total == {"not_a_server_error": {Status.success: 1, Status.failure: 2, "total": 3}}


def test_auth(schema_url, app):
    # When auth is specified in `api_options` as a tuple of 2 strings
    execute(schema_url, api_options={"auth": ("test", "test")})

    # Then each request should contain corresponding basic auth header
    assert len(app["incoming_requests"]) == 3
    headers = {"Authorization": "Basic dGVzdDp0ZXN0"}
    assert_request(app, 0, "GET", "/api/failure", headers)
    assert_request(app, 1, "GET", "/api/failure", headers)
    assert_request(app, 2, "GET", "/api/success", headers)


@pytest.mark.parametrize("converter", (lambda x: x, lambda x: x + "/"))
def test_base_url(base_url, schema_url, app, converter):
    base_url = converter(base_url)
    # When `base_url` is specified explicitly with or without trailing slash
    execute(schema_url, loader_options={"base_url": base_url})

    # Then each request should reach the app in both cases
    assert len(app["incoming_requests"]) == 3
    assert_request(app, 0, "GET", "/api/failure")
    assert_request(app, 1, "GET", "/api/failure")
    assert_request(app, 2, "GET", "/api/success")


def test_execute_with_headers(schema_url, app):
    # When headers are specified for the `execute` call
    headers = {"Authorization": "Bearer 123"}
    execute(schema_url, api_options={"headers": headers})

    # Then each request should contain these headers
    assert len(app["incoming_requests"]) == 3
    assert_request(app, 0, "GET", "/api/failure", headers)
    assert_request(app, 1, "GET", "/api/failure", headers)
    assert_request(app, 2, "GET", "/api/success", headers)


def test_execute_filter_endpoint(schema_url, app):
    # When `endpoint` is passed in `loader_options` in the `execute` call
    execute(schema_url, loader_options={"endpoint": ["success"]})

    # Then the runner will make calls only to the specified endpoint
    assert len(app["incoming_requests"]) == 1
    assert_request(app, 0, "GET", "/api/success")
    assert_not_request(app, "GET", "/api/failure")


def test_execute_filter_method(schema_url, app):
    # When `method` passed in `loader_options` corresponds to a method that is not defined in the app schema
    execute(schema_url, loader_options={"method": ["POST"]})
    # Then runner will not make any requests
    assert len(app["incoming_requests"]) == 0


@pytest.mark.endpoints("slow")
def test_hypothesis_deadline(schema_url, app):
    # When `deadline` is passed in `hypothesis_options` in the `execute` call
    execute(schema_url, hypothesis_options={"deadline": 500})


@pytest.mark.endpoints("multipart")
def test_form_data(schema_url, app):
    def is_ok(response, result):
        assert response.status_code == 200

    def check_content(response, result):
        data = response.json()
        assert isinstance(data["key"], str)
        assert data["value"].lstrip("-").isdigit()

    # When endpoint specifies parameters with `in=formData`
    # Then responses should have 200 status, and not 415 (unsupported media type)
    results = execute(schema_url, checks=(is_ok, check_content), hypothesis_options={"max_examples": 3})
    # And there should be no errors or failures
    assert not results.has_errors
    assert not results.has_failures
    # And the application should receive 3 requests as specified in `max_examples`
    assert len(app["incoming_requests"]) == 3
    # And the Content-Type of incoming requests should be `multipart/form-data`
    assert app["incoming_requests"][0].headers["Content-Type"].startswith("multipart/form-data")


@pytest.mark.endpoints("teapot")
def test_unknown_response_code(schema_url, app):
    # When endpoint returns a status code, that is not listed in "responses"
    # And "status_code_conformance" is specified
    results = execute(schema_url, checks=(status_code_conformance,), hypothesis_options={"max_examples": 1})
    # Then there should be a failure
    assert results.has_failures
    check = results.results[0].checks[0]
    assert check.name == "status_code_conformance"
    assert check.value == Status.failure


@pytest.mark.endpoints("failure")
def test_unknown_response_code_with_default(schema_url, app):
    # When endpoint returns a status code, that is not listed in "responses", but there is a "default" response
    # And "status_code_conformance" is specified
    results = execute(schema_url, checks=(status_code_conformance,), hypothesis_options={"max_examples": 1})
    # Then there should be no failure
    assert not results.has_failures
    check = results.results[0].checks[0]
    assert check.name == "status_code_conformance"
    assert check.value == Status.success


@pytest.mark.endpoints("text")
def test_unknown_content_type(schema_url, app):
    # When endpoint returns a response with content type, not specified in "produces"
    # And "content_type_conformance" is specified
    results = execute(schema_url, checks=(content_type_conformance,), hypothesis_options={"max_examples": 1})
    # Then there should be a failure
    assert results.has_failures
    check = results.results[0].checks[0]
    assert check.name == "content_type_conformance"
    assert check.value == Status.failure


@pytest.mark.endpoints("success")
def test_known_content_type(schema_url, app):
    # When endpoint returns a response with a proper content type
    # And "content_type_conformance" is specified
    results = execute(schema_url, checks=(content_type_conformance,), hypothesis_options={"max_examples": 1})
    # Then there should be no a failures
    assert not results.has_failures


@pytest.mark.parametrize(
    "options", ({"loader_options": {"base_url": "http://127.0.0.1:1/"}}, {"hypothesis_options": {"deadline": 1}})
)
@pytest.mark.endpoints("slow")
def test_exceptions(schema_url, app, options):
    results = prepare(schema_url, **options)
    assert any([event.status == Status.error for event in results if isinstance(event, events.AfterExecution)])


@pytest.mark.endpoints("multipart")
def test_flaky_exceptions(schema_url, mocker):
    # GH: #236
    error_idx = 0

    def flaky(*args, **kwargs):
        nonlocal error_idx
        exception_class = [ValueError, TypeError, ZeroDivisionError, KeyError][error_idx % 4]
        error_idx += 1
        raise exception_class

    # When there are many different exceptions during the test
    # And Hypothesis consider this test as a flaky one
    mocker.patch("schemathesis.Case.call", side_effect=flaky)
    results = execute(schema_url, hypothesis_options={"max_examples": 3, "derandomize": True})
    # Then the execution result should indicate errors
    assert results.has_errors
    assert results.results[0].errors[0][0].args[0].startswith("Tests on this endpoint produce unreliable results:")


@pytest.mark.parametrize(
    "url, base_url",
    (
        ("http://127.0.0.1:8080/swagger.json", "http://127.0.0.1:8080"),
        ("https://example.com/get", "https://example.com"),
        ("https://example.com", "https://example.com"),
    ),
)
def test_get_base_url(url, base_url):
    assert get_base_url(url) == base_url
