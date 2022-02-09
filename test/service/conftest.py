import json
import uuid

import attr
import pytest
from flask import Response
from pytest_httpserver.pytest_plugin import PluginHTTPServer

# A token for a testing Schemathesis.io instance
DEFAULT_SERVICE_TOKEN = "25f8ee2357da497d8d0d07be62df62a1"


def pytest_addoption(parser):
    group = parser.getgroup("schemathesis")
    group.addoption(
        "--schemathesis-io-token",
        action="store",
        default=DEFAULT_SERVICE_TOKEN,
        help="A token to access the test Schemathesis.io instance.",
    )


@pytest.fixture(scope="session")
def service_token(request):
    return request.config.getoption("--schemathesis-io-token")


@pytest.fixture
def httpserver():
    # The default implementation doesn't play nice with pytest-xdist
    server = PluginHTTPServer(host="localhost", port=0)
    server.start()
    yield server
    if server.is_running():
        server.stop()


@pytest.fixture
def setup_server(httpserver: PluginHTTPServer):
    def inner(callback, method, uri):
        callback(httpserver.expect_request(method=method, uri=uri))
        return httpserver.url_for(uri)

    return inner


@pytest.fixture
def run_id():
    return uuid.uuid4().hex


@pytest.fixture
def service_setup(request, setup_server):
    for marker in request.node.iter_markers("service"):
        data = marker.kwargs["data"]
        status = marker.kwargs["status"]
        method = marker.kwargs["method"]
        path = marker.kwargs["path"]
        setup_server(lambda h: h.respond_with_json(data, status=status), method, path)


@pytest.fixture
def create_event_url(setup_server, run_id):
    return setup_server(
        lambda h: h.respond_with_json({"message": "Event processed successfully"}, status=201),
        "POST",
        f"/runs/{run_id}/events/",
    )


@pytest.fixture
def start_run_url(setup_server, run_id):
    return setup_server(
        lambda h: h.respond_with_json({"run_id": run_id, "short_url": "http://127.0.0.1"}, status=201), "POST", "/runs/"
    )


@pytest.fixture
def finish_run_url(setup_server, run_id):
    return setup_server(lambda h: h.respond_with_response(Response(status=204)), "POST", f"/runs/{run_id}/finish/")


@attr.s()
class Service:
    server = attr.ib()
    base_url = attr.ib()
    start_run_url = attr.ib()
    create_event_url = attr.ib()
    finish_run_url = attr.ib()

    def assert_call(self, idx, url, response_status, event_type=None):
        item = self.server.log[idx]
        assert item[0].url.endswith(url)
        if event_type is not None:
            assert next(iter(json.loads(item[0].data).keys())) == event_type
        assert item[1].status_code == response_status


@pytest.fixture
def service(httpserver, service_setup, start_run_url, create_event_url, finish_run_url):
    return Service(
        server=httpserver,
        base_url=f"http://{httpserver.host}:{httpserver.port}",
        start_run_url=start_run_url,
        create_event_url=create_event_url,
        finish_run_url=finish_run_url,
    )
