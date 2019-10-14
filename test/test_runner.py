import pytest

from schemathesis import __version__
from schemathesis.runner import execute, get_base_url


def assert_request(app, idx, method, path, headers=None):
    request = app["incoming_requests"][idx]
    assert request.method == method
    assert request.path == path
    if headers:
        for key, value in headers.items():
            assert request.headers.get(key) == value


def assert_not_request(app, method, path):
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
    assert len(app["incoming_requests"]) == 3
    assert_request(app, 0, "GET", "/api/failure", headers)
    assert_request(app, 1, "GET", "/api/failure", headers)
    assert_request(app, 2, "GET", "/api/success", headers)

    # And statistic is showing the breakdown of cases types
    assert "not_a_server_error" in stats.data
    assert dict(stats.data["not_a_server_error"]) == {"total": 3, "ok": 1, "error": 2}


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

    # Then it should be passed to `hypothesis.settings`
    # And slow endpoint (250ms) should not be considered as breaking the deadline
    assert len(app["incoming_requests"]) == 1
    assert_request(app, 0, "GET", "/api/slow")


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
