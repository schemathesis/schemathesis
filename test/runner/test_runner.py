from typing import Dict, Optional

import pytest
from aiohttp import web
from aiohttp.streams import EmptyStreamReader
from flask import Flask
from requests.auth import HTTPDigestAuth

from schemathesis import from_wsgi
from schemathesis.constants import __version__
from schemathesis.exceptions import InvalidSchema
from schemathesis.models import Case, Status
from schemathesis.runner import events, execute, get_base_url, get_requests_auth, get_wsgi_auth, prepare
from schemathesis.runner.checks import content_type_conformance, response_schema_conformance, status_code_conformance


def assert_request(
    app: web.Application, idx: int, method: str, path: str, headers: Optional[Dict[str, str]] = None
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


def pytest_generate_tests(metafunc):
    if "args" in metafunc.fixturenames:
        metafunc.parametrize("args", ["wsgi", "real"], indirect=True)


@pytest.fixture
def args(request):
    if request.param == "real":
        schema_url = request.getfixturevalue("schema_url")
        kwargs = {"schema_uri": schema_url}
        app = request.getfixturevalue("app")
    else:
        app = request.getfixturevalue("flask_app")
        kwargs = {"schema_uri": "/swagger.yaml", "loader_options": {"app": app}, "loader": from_wsgi}
    return app, kwargs


def test_execute_base_url_not_found(base_url, schema_url, app):
    # When base URL is pointing to an unknown location
    execute(schema_url, loader_options={"base_url": f"{base_url}/404/"})
    # Then the runner should use this base
    # And they will not reach the application
    assert_incoming_requests_num(app, 0)


def test_execute_base_url_found(base_url, schema_url, app):
    # When base_url is specified
    execute(schema_url, loader_options={"base_url": base_url})
    # Then it should be used by the runner
    assert_incoming_requests_num(app, 3)


def test_execute(args):
    app, kwargs = args
    # When the runner is executed against the default test app
    stats = execute(**kwargs)

    # Then there are three executed cases
    # Two errors - the second one is a flakiness check
    headers = {"User-Agent": f"schemathesis/{__version__}"}
    assert_schema_requests_num(app, 1)
    schema_requests = get_schema_requests(app)
    assert schema_requests[0].headers.get("User-Agent") == headers["User-Agent"]
    assert_incoming_requests_num(app, 3)
    assert_request(app, 0, "GET", "/api/failure", headers)
    assert_request(app, 1, "GET", "/api/failure", headers)
    assert_request(app, 2, "GET", "/api/success", headers)

    # And statistic is showing the breakdown of cases types
    assert stats.total == {"not_a_server_error": {Status.success: 1, Status.failure: 2, "total": 3}}


def test_auth(args):
    app, kwargs = args
    # When auth is specified in `api_options` as a tuple of 2 strings
    execute(**kwargs, api_options={"auth": ("test", "test")})

    # Then each request should contain corresponding basic auth header
    assert_incoming_requests_num(app, 3)
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
    assert_incoming_requests_num(app, 3)
    assert_request(app, 0, "GET", "/api/failure")
    assert_request(app, 1, "GET", "/api/failure")
    assert_request(app, 2, "GET", "/api/success")


def test_execute_with_headers(args):
    app, kwargs = args
    # When headers are specified for the `execute` call
    headers = {"Authorization": "Bearer 123"}
    execute(**kwargs, api_options={"headers": headers})

    # Then each request should contain these headers
    assert_incoming_requests_num(app, 3)
    assert_request(app, 0, "GET", "/api/failure", headers)
    assert_request(app, 1, "GET", "/api/failure", headers)
    assert_request(app, 2, "GET", "/api/success", headers)


def test_execute_filter_endpoint(args):
    app, kwargs = args
    # When `endpoint` is passed in `loader_options` in the `execute` call
    kwargs.setdefault("loader_options", {})["endpoint"] = ["success"]
    execute(**kwargs)

    # Then the runner will make calls only to the specified endpoint
    assert_incoming_requests_num(app, 1)
    assert_request(app, 0, "GET", "/api/success")
    assert_not_request(app, "GET", "/api/failure")


def test_execute_filter_method(args):
    app, kwargs = args
    # When `method` passed in `loader_options` corresponds to a method that is not defined in the app schema
    kwargs.setdefault("loader_options", {})["method"] = ["POST"]
    execute(**kwargs)
    # Then runner will not make any requests
    assert_incoming_requests_num(app, 0)


@pytest.mark.endpoints("slow")
def test_hypothesis_deadline(args):
    app, kwargs = args
    # When `deadline` is passed in `hypothesis_options` in the `execute` call
    execute(**kwargs, hypothesis_options={"deadline": 500})
    assert_incoming_requests_num(app, 1)
    assert_request(app, 0, "GET", "/api/slow")


@pytest.mark.endpoints("multipart")
def test_form_data(args):
    app, kwargs = args

    def is_ok(response, result):
        assert response.status_code == 200

    def check_content(response, result):
        if isinstance(app, Flask):
            data = response.json
        else:
            data = response.json()
        assert isinstance(data["key"], str)
        assert data["value"].lstrip("-").isdigit()

    # When endpoint specifies parameters with `in=formData`
    # Then responses should have 200 status, and not 415 (unsupported media type)
    results = execute(**kwargs, checks=(is_ok, check_content), hypothesis_options={"max_examples": 3})
    # And there should be no errors or failures
    assert not results.has_errors
    assert not results.has_failures
    # And the application should receive 3 requests as specified in `max_examples`
    assert_incoming_requests_num(app, 3)
    # And the Content-Type of incoming requests should be `multipart/form-data`
    incoming_requests = get_incoming_requests(app)
    assert incoming_requests[0].headers["Content-Type"].startswith("multipart/form-data")


@pytest.mark.endpoints("teapot")
def test_unknown_response_code(args):
    app, kwargs = args
    # When endpoint returns a status code, that is not listed in "responses"
    # And "status_code_conformance" is specified
    results = execute(**kwargs, checks=(status_code_conformance,), hypothesis_options={"max_examples": 1})
    # Then there should be a failure
    assert results.has_failures
    check = results.results[0].checks[0]
    assert check.name == "status_code_conformance"
    assert check.value == Status.failure


@pytest.mark.endpoints("failure")
def test_unknown_response_code_with_default(args):
    app, kwargs = args
    # When endpoint returns a status code, that is not listed in "responses", but there is a "default" response
    # And "status_code_conformance" is specified
    results = execute(**kwargs, checks=(status_code_conformance,), hypothesis_options={"max_examples": 1})
    # Then there should be no failure
    assert not results.has_failures
    check = results.results[0].checks[0]
    assert check.name == "status_code_conformance"
    assert check.value == Status.success


@pytest.mark.endpoints("text")
def test_unknown_content_type(args):
    app, kwargs = args
    # When endpoint returns a response with content type, not specified in "produces"
    # And "content_type_conformance" is specified
    results = execute(**kwargs, checks=(content_type_conformance,), hypothesis_options={"max_examples": 1})
    # Then there should be a failure
    assert results.has_failures
    check = results.results[0].checks[0]
    assert check.name == "content_type_conformance"
    assert check.value == Status.failure


@pytest.mark.endpoints("success")
def test_known_content_type(args):
    app, kwargs = args
    # When endpoint returns a response with a proper content type
    # And "content_type_conformance" is specified
    results = execute(**kwargs, checks=(content_type_conformance,), hypothesis_options={"max_examples": 1})
    # Then there should be no a failures
    assert not results.has_failures


@pytest.mark.endpoints("invalid_response")
def test_response_conformance_invalid(args):
    app, kwargs = args
    # When endpoint returns a response that doesn't conform to the schema
    # And "response_schema_conformance" is specified
    results = execute(**kwargs, checks=(response_schema_conformance,), hypothesis_options={"max_examples": 1})
    # Then there should be a failure
    assert results.has_failures
    lines = results.results[0].checks[-1].message.split("\n")
    assert lines[0] == "The received response does not conform to the defined schema!"
    assert lines[2] == "Details: "
    assert lines[4] == "'success' is a required property"


@pytest.mark.endpoints("success")
def test_response_conformance_valid(args):
    app, kwargs = args
    # When endpoint returns a response that conforms to the schema
    # And "response_schema_conformance" is specified
    results = execute(**kwargs, checks=(response_schema_conformance,), hypothesis_options={"max_examples": 1})
    # Then there should be no failures or errors
    assert not results.has_failures
    assert not results.has_errors


@pytest.mark.endpoints("text")
def test_response_conformance_text(args):
    app, kwargs = args
    # When endpoint returns a response that is not JSON
    # And "response_schema_conformance" is specified
    results = execute(**kwargs, checks=(response_schema_conformance,), hypothesis_options={"max_examples": 1})
    # Then the check should be ignored if the response headers are not application/json
    assert not results.has_failures
    assert not results.has_errors


@pytest.mark.endpoints("malformed_json")
def test_response_conformance_malformed_json(args):
    app, kwargs = args
    # When endpoint returns a response that contains a malformed JSON, but has a valid content type header
    # And "response_schema_conformance" is specified
    results = execute(**kwargs, checks=(response_schema_conformance,), hypothesis_options={"max_examples": 1})
    # Then there should be a failure
    assert results.has_errors
    error = results.results[-1].errors[-1][0]
    assert "Expecting property name enclosed in double quotes" in str(error)


@pytest.mark.endpoints("path_variable")
def test_path_parameters_encoding(schema_url):
    # NOTE. Flask still decodes %2F as / and returns 404
    # When endpoint has a path parameter
    results = execute(schema_url, checks=(status_code_conformance,), hypothesis_options={"derandomize": True})
    # Then there should be no failures
    # since all path parameters are quoted
    assert not results.has_errors
    assert not results.has_failures


@pytest.mark.parametrize(
    "options", ({"loader_options": {"base_url": "http://127.0.0.1:1/"}}, {"hypothesis_options": {"deadline": 1}})
)
@pytest.mark.endpoints("slow")
def test_exceptions(schema_url, app, options):
    results = prepare(schema_url, **options)
    assert any([event.status == Status.error for event in results if isinstance(event, events.AfterExecution)])


@pytest.mark.endpoints("multipart")
def test_flaky_exceptions(args, mocker):
    app, kwargs = args
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
    mocker.patch("schemathesis.Case.call_wsgi", side_effect=flaky)
    results = execute(**kwargs, hypothesis_options={"max_examples": 3, "derandomize": True})
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


@pytest.mark.endpoints("invalid_path_parameter")
def test_invalid_path_parameter(args):
    app, kwargs = args
    results = execute(**kwargs)
    assert results.has_errors
    error, _ = results.results[0].errors[0]
    assert isinstance(error, InvalidSchema)
    assert str(error) == "Missing required property `required: true`"


def test_get_requests_auth():
    assert isinstance(get_requests_auth(("test", "test"), "digest"), HTTPDigestAuth)


def test_get_wsgi_auth():
    with pytest.raises(ValueError, match="Digest auth is not supported for WSGI apps"):
        get_wsgi_auth(("test", "test"), "digest")
