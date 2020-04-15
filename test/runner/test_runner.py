import json
from typing import Dict, Optional

import pytest
from aiohttp import web
from aiohttp.streams import EmptyStreamReader
from flask import Flask
from hypothesis import Phase
from requests.auth import HTTPDigestAuth

import schemathesis
from schemathesis import loaders
from schemathesis.checks import (
    DEFAULT_CHECKS,
    content_type_conformance,
    response_schema_conformance,
    status_code_conformance,
)
from schemathesis.constants import __version__
from schemathesis.models import Status
from schemathesis.runner import events, get_base_url, get_requests_auth, prepare
from schemathesis.runner.impl.core import get_wsgi_auth


def execute(schema_uri, checks=DEFAULT_CHECKS, loader=loaders.from_uri, **options) -> events.Finished:
    generator = prepare(schema_uri=schema_uri, checks=checks, loader=loader, **options)
    all_events = list(generator)
    return all_events[-1]


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
def args(request, mocker):
    if request.param == "real":
        schema_url = request.getfixturevalue("schema_url")
        kwargs = {"schema_uri": schema_url}
        app = request.getfixturevalue("app")
    else:
        app = request.getfixturevalue("flask_app")
        app_path = request.getfixturevalue("loadable_flask_app")
        # To have simpler tests it is easier to reuse already imported application for inspection
        mocker.patch("schemathesis.runner.import_app", return_value=app)
        kwargs = {"schema_uri": "/swagger.yaml", "app": app_path, "loader": loaders.from_wsgi}
    return app, kwargs


def test_execute_base_url_not_found(base_url, schema_url, app):
    # When base URL is pointing to an unknown location
    execute(schema_url, base_url=f"{base_url}/404/")
    # Then the runner should use this base
    # And they will not reach the application
    assert_incoming_requests_num(app, 0)


def test_execute_base_url_found(base_url, schema_url, app):
    # When base_url is specified
    execute(schema_url, base_url=base_url)
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
    # When auth is specified as a tuple of 2 strings
    execute(**kwargs, auth=("test", "test"))

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
    execute(schema_url, base_url=base_url)

    # Then each request should reach the app in both cases
    assert_incoming_requests_num(app, 3)
    assert_request(app, 0, "GET", "/api/failure")
    assert_request(app, 1, "GET", "/api/failure")
    assert_request(app, 2, "GET", "/api/success")


def test_execute_with_headers(args):
    app, kwargs = args
    # When headers are specified for the `execute` call
    headers = {"Authorization": "Bearer 123"}
    execute(**kwargs, headers=headers)

    # Then each request should contain these headers
    assert_incoming_requests_num(app, 3)
    assert_request(app, 0, "GET", "/api/failure", headers)
    assert_request(app, 1, "GET", "/api/failure", headers)
    assert_request(app, 2, "GET", "/api/success", headers)


def test_execute_filter_endpoint(args):
    app, kwargs = args
    # When `endpoint` is passed in the `execute` call
    kwargs.setdefault("endpoint", ["success"])
    execute(**kwargs)

    # Then the runner will make calls only to the specified endpoint
    assert_incoming_requests_num(app, 1)
    assert_request(app, 0, "GET", "/api/success")
    assert_not_request(app, "GET", "/api/failure")


def test_execute_filter_method(args):
    app, kwargs = args
    # When `method` corresponds to a method that is not defined in the app schema
    kwargs.setdefault("method", "POST")
    execute(**kwargs)
    # Then runner will not make any requests
    assert_incoming_requests_num(app, 0)


@pytest.mark.endpoints("slow")
def test_hypothesis_deadline(args):
    app, kwargs = args
    # When `hypothesis_deadline` is passed in the `execute` call
    execute(**kwargs, hypothesis_deadline=500)
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
    results = execute(**kwargs, checks=(is_ok, check_content), hypothesis_max_examples=3)
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
    init, *others, finished = prepare(**kwargs, checks=(status_code_conformance,), hypothesis_max_examples=1)
    # Then there should be a failure
    assert finished.has_failures
    check = others[1].result.checks[0]
    assert check.name == "status_code_conformance"
    assert check.value == Status.failure


@pytest.mark.endpoints("failure")
def test_unknown_response_code_with_default(args):
    app, kwargs = args
    # When endpoint returns a status code, that is not listed in "responses", but there is a "default" response
    # And "status_code_conformance" is specified
    init, *others, finished = prepare(**kwargs, checks=(status_code_conformance,), hypothesis_max_examples=1)
    # Then there should be no failure
    assert not finished.has_failures
    check = others[1].result.checks[0]
    assert check.name == "status_code_conformance"
    assert check.value == Status.success


@pytest.mark.endpoints("text")
def test_unknown_content_type(args):
    app, kwargs = args
    # When endpoint returns a response with content type, not specified in "produces"
    # And "content_type_conformance" is specified
    init, *others, finished = prepare(**kwargs, checks=(content_type_conformance,), hypothesis_max_examples=1)
    # Then there should be a failure
    assert finished.has_failures
    check = others[1].result.checks[0]
    assert check.name == "content_type_conformance"
    assert check.value == Status.failure


@pytest.mark.endpoints("success")
def test_known_content_type(args):
    app, kwargs = args
    # When endpoint returns a response with a proper content type
    # And "content_type_conformance" is specified
    *_, finished = prepare(**kwargs, checks=(content_type_conformance,), hypothesis_max_examples=1)
    # Then there should be no a failures
    assert not finished.has_failures


@pytest.mark.endpoints("invalid_response")
def test_response_conformance_invalid(args):
    app, kwargs = args
    # When endpoint returns a response that doesn't conform to the schema
    # And "response_schema_conformance" is specified
    init, *others, finished = prepare(**kwargs, checks=(response_schema_conformance,), hypothesis_max_examples=1)
    # Then there should be a failure
    assert finished.has_failures
    lines = others[1].result.checks[-1].message.split("\n")
    assert lines[0] == "The received response does not conform to the defined schema!"
    assert lines[2] == "Details: "
    assert lines[4] == "'success' is a required property"


@pytest.mark.endpoints("success")
def test_response_conformance_valid(args):
    app, kwargs = args
    # When endpoint returns a response that conforms to the schema
    # And "response_schema_conformance" is specified
    results = execute(**kwargs, checks=(response_schema_conformance,), hypothesis_max_examples=1)
    # Then there should be no failures or errors
    assert not results.has_failures
    assert not results.has_errors


@pytest.mark.endpoints("recursive")
def test_response_conformance_recursive_valid(mocker, schema_url):
    mocker.patch("schemathesis.schemas.RECURSION_DEPTH_LIMIT", 1)
    # When endpoint contains a response that have recursive references
    # And "response_schema_conformance" is specified
    results = execute(schema_url, checks=(response_schema_conformance,), hypothesis_max_examples=1)
    # Then there should be no failures or errors
    assert not results.has_failures
    assert not results.has_errors


@pytest.mark.endpoints("text")
def test_response_conformance_text(args):
    app, kwargs = args
    # When endpoint returns a response that is not JSON
    # And "response_schema_conformance" is specified
    results = execute(**kwargs, checks=(response_schema_conformance,), hypothesis_max_examples=1)
    # Then the check should be ignored if the response headers are not application/json
    assert not results.has_failures
    assert not results.has_errors


@pytest.mark.endpoints("malformed_json")
def test_response_conformance_malformed_json(args):
    app, kwargs = args
    # When endpoint returns a response that contains a malformed JSON, but has a valid content type header
    # And "response_schema_conformance" is specified
    init, *others, finished = prepare(**kwargs, checks=(response_schema_conformance,), hypothesis_max_examples=1)
    # Then there should be a failure
    assert finished.has_errors
    error = others[1].result.errors[-1].exception
    assert "Expecting property name enclosed in double quotes" in error


@pytest.fixture()
def filter_path_parameters():
    # ".." and "." strings are treated specially, but this behavior is outside of the test's scope
    # "" shouldn't be allowed as a valid path parameter

    def schema_filter(strategy):
        return strategy.filter(
            lambda x: x["key"] not in ("..", ".", "", "/") and not (isinstance(x["key"], str) and "/" in x["key"])
        )

    schemathesis.hooks.register("path_parameters", schema_filter)
    yield
    schemathesis.hooks.unregister_all()


@pytest.mark.endpoints("path_variable")
@pytest.mark.usefixtures("filter_path_parameters")
def test_path_parameters_encoding(schema_url):
    # NOTE. WSGI and ASGI applications decodes %2F as / and returns 404
    # When endpoint has a path parameter
    results = execute(schema_url, checks=(status_code_conformance,), hypothesis_derandomize=True)
    # Then there should be no failures
    # since all path parameters are quoted
    assert not results.has_errors
    assert not results.has_failures


@pytest.mark.parametrize("options", ({"base_url": "http://127.0.0.1:1/"}, {"hypothesis_deadline": 1}))
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
    init, *others, finished = prepare(**kwargs, hypothesis_max_examples=3, hypothesis_derandomize=True)
    # Then the execution result should indicate errors
    assert finished.has_errors
    assert "Tests on this endpoint produce unreliable results:" in others[1].result.errors[0].exception


@pytest.mark.endpoints("payload")
async def test_payload_explicit_example(args):
    # When endpoint has an example specified
    app, kwargs = args
    kwargs.setdefault("hypothesis_phases", [Phase.explicit])
    result = execute(**kwargs)
    # Then run should be successful
    assert not result.has_errors
    assert not result.has_failures
    incoming_requests = get_incoming_requests(app)

    if isinstance(app, Flask):
        body = incoming_requests[0].json
    else:
        body = await incoming_requests[0].json()
    # And this example should be sent to the app
    assert body == {"name": "John"}


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
    init, *others, finished = prepare(validate_schema=False, **kwargs)
    assert finished.has_errors
    assert "schemathesis.exceptions.InvalidSchema: Missing required property" in others[1].result.errors[0].exception


def test_get_requests_auth():
    assert isinstance(get_requests_auth(("test", "test"), "digest"), HTTPDigestAuth)


def test_get_wsgi_auth():
    with pytest.raises(ValueError, match="Digest auth is not supported for WSGI apps"):
        get_wsgi_auth(("test", "test"), "digest")


@pytest.mark.endpoints("failure", "multiple_failures")
def test_exit_first(args):
    app, kwargs = args
    results = prepare(**kwargs, exit_first=True)
    results = list(results)
    assert results[-1].has_failures is True
    assert results[-1].failed_count == 1


def test_auth_loader_options(base_url, schema_url, app):
    execute(schema_url, base_url=base_url, auth=("test", "test"), auth_type="basic")
    schema_request = get_schema_requests(app)
    assert schema_request[0].headers["Authorization"] == "Basic dGVzdDp0ZXN0"


@pytest.fixture()
def raw_schema(app):
    return app["config"]["schema_data"]


@pytest.fixture()
def json_string(raw_schema):
    return json.dumps(raw_schema)


@pytest.fixture()
def schema_path(json_string, tmp_path):
    path = tmp_path / "schema.json"
    path.write_text(json_string)
    return str(path)


@pytest.fixture()
def relative_schema_url():
    return "/swagger.yaml"


@pytest.mark.parametrize(
    "loader, fixture",
    (
        (loaders.from_dict, "raw_schema"),
        (loaders.from_file, "json_string"),
        (loaders.from_path, "schema_path"),
        (loaders.from_wsgi, "relative_schema_url"),
        (loaders.from_aiohttp, "relative_schema_url"),
    ),
)
@pytest.mark.endpoints("success")
def test_non_default_loader(request, loader, fixture):
    schema = request.getfixturevalue(fixture)
    kwargs = {}
    if loader is loaders.from_wsgi:
        kwargs["app"] = request.getfixturevalue("loadable_flask_app")
    else:
        if loader is loaders.from_aiohttp:
            kwargs["app"] = request.getfixturevalue("loadable_aiohttp_app")
        kwargs["base_url"] = request.getfixturevalue("base_url")
    init, *others, finished = prepare(schema, loader=loader, headers={"TEST": "foo"}, **kwargs)
    assert not finished.has_errors
    assert not finished.has_failures


FROM_DICT_ERROR_MESSAGE = "Dictionary as a schema is allowed only with `from_dict` loader"


@pytest.mark.parametrize(
    "loader, schema, message",
    (
        (loaders.from_uri, {}, FROM_DICT_ERROR_MESSAGE),
        (loaders.from_dict, "", "Schema should be a dictionary for `from_dict` loader"),
        (loaders.from_wsgi, {}, FROM_DICT_ERROR_MESSAGE),
        (loaders.from_file, {}, FROM_DICT_ERROR_MESSAGE),
        (loaders.from_path, {}, FROM_DICT_ERROR_MESSAGE),
    ),
)
def test_validation(loader, schema, message):
    with pytest.raises(ValueError, match=message):
        list(prepare(schema, loader=loader))


def test_custom_loader(swagger_20, base_url):
    swagger_20.base_url = base_url
    *others, finished = list(prepare({}, loader=lambda *args, **kwargs: swagger_20))
    assert not finished.has_errors
    assert not finished.has_failures
