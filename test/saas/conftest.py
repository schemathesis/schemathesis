import json
import uuid

import attr
import pytest
from flask import Response
from pytest_httpserver.pytest_plugin import PluginHTTPServer

# A token for a local SaaS instance
DEFAULT_SAAS_TOKEN = "25f8ee2357da497d8d0d07be62df62a1"


def pytest_addoption(parser):
    group = parser.getgroup("schemathesis")
    group.addoption(
        "--saas-token",
        action="store",
        default=DEFAULT_SAAS_TOKEN,
        help="A token to access the test SaaS instance.",
    )


@pytest.fixture(scope="session")
def saas_token(request):
    return request.config.getoption("--saas-token")


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
def job_id():
    return uuid.uuid4().hex


@pytest.fixture
def saas_setup(request, setup_server):
    for marker in request.node.iter_markers("saas"):
        data = (marker.kwargs["data"],)
        status = marker.kwargs["status"]
        method = marker.kwargs["method"]
        path = marker.kwargs["path"]
        setup_server(lambda h: h.respond_with_json(data, status=status), method, path)


@pytest.fixture
def create_event_url(setup_server, job_id):
    return setup_server(
        lambda h: h.respond_with_json({"message": "Event processed successfully"}, status=201),
        "POST",
        f"/jobs/{job_id}/events/",
    )


@pytest.fixture
def start_job_url(setup_server, job_id):
    return setup_server(lambda h: h.respond_with_json({"job_id": job_id}, status=201), "POST", "/jobs/")


@pytest.fixture
def finish_job_url(setup_server, job_id):
    return setup_server(lambda h: h.respond_with_response(Response(status=204)), "POST", f"/jobs/{job_id}/finish/")


@attr.s()
class SaaS:
    server = attr.ib()
    base_url = attr.ib()
    start_job_url = attr.ib()
    create_event_url = attr.ib()
    finish_job_url = attr.ib()

    def assert_call(self, idx, url, response_status, event_type=None):
        item = self.server.log[idx]
        assert item[0].url.endswith(url)
        if event_type is not None:
            assert json.loads(item[0].data)["event_type"] == event_type
        assert item[1].status_code == response_status


@pytest.fixture
def saas(httpserver, saas_setup, start_job_url, create_event_url, finish_job_url):
    return SaaS(
        server=httpserver,
        base_url=f"http://{httpserver.host}:{httpserver.port}",
        start_job_url=start_job_url,
        create_event_url=create_event_url,
        finish_job_url=finish_job_url,
    )
