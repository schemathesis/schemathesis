from typing import Dict, Optional

import pytest
from aiohttp import web

from schemathesis.constants import __version__
from schemathesis.models import Status
from schemathesis.runner import events, execute, get_base_url, prepare


def assert_request(
    app: web.Application, idx: int, method: str, path: str, headers: Optional[Dict[str, str]] = None
) -> None:
    request = app["incoming_requests"][idx]
    assert request.method == method
    assert request.path == path
    if headers:
        for key, value in headers.items():
            assert request.headers.get(key) == value


def assert_not_request(app: web.Application, method: str, path: str) -> None:
    for request in app["incoming_requests"]:
        assert not (request.path == path and request.method == method)


def test_execute_base_url_not_found(base_url, schema_url, app):
    # When base URL is pointing to an unknown location
    execute(schema_url, api_options={"base_url": f"{base_url}/404"})
    # Then the runner should use this base
    # And they will not reach the application
    assert len(app["incoming_requests"]) == 0


def test_execute_base_url_found(base_url, schema_url, app):
    # When base_url is specified
    execute(schema_url, api_options={"base_url": base_url})
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


@pytest.mark.parametrize(
    "options", ({"api_options": {"base_url": "http://127.0.0.1:1/"}}, {"hypothesis_options": {"deadline": 1}})
)
@pytest.mark.endpoints("slow")
def test_exceptions(schema_url, app, options):
    results = prepare(schema_url, **options)
    assert any([event.status == Status.error for event in results if isinstance(event, events.AfterExecution)])


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
