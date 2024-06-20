import re
import tarfile
from contextlib import contextmanager
from dataclasses import dataclass
from io import BytesIO
from queue import Queue

import click
import pytest
from pytest_httpserver.pytest_plugin import PluginHTTPServer

from schemathesis.internal.datetime import current_datetime
from schemathesis.service import FileReportHandler, ServiceReportHandler
from schemathesis.service.client import ServiceClient
from schemathesis.service.hosts import HostData

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
    server = PluginHTTPServer(host="127.0.0.1", port=0)
    server.start()
    yield server
    if server.is_running():
        server.stop()


@pytest.fixture
def setup_server(httpserver: PluginHTTPServer):
    def inner(callback, method, uri):
        callback(httpserver.expect_request(method=method, uri=uri))

    return inner


@pytest.fixture
def service_setup(request, setup_server):
    for marker in request.node.iter_markers("service"):
        data = marker.kwargs["data"]
        status = marker.kwargs["status"]
        method = marker.kwargs["method"]
        path = marker.kwargs["path"]
        # Use default args to bind variables early
        setup_server(lambda h, d=data, s=status: h.respond_with_json(d, status=s), method, path)


@pytest.fixture
def get_project_details(setup_server, openapi3_base_url, openapi3_schema):
    return setup_server(
        lambda h: h.respond_with_json(
            {
                "specification": {
                    "schema": openapi3_schema.raw_schema,
                    "type": "openapi",
                    "version": openapi3_schema.spec_version,
                    "operations_count": openapi3_schema.operations_count,
                },
                "environments": [{"name": "Default", "description": "", "is_default": True, "url": openapi3_base_url}],
            },
            status=200,
        ),
        "GET",
        re.compile("/cli/projects/.*/"),
    )


@pytest.fixture
def next_url():
    return "http://127.0.0.1/r/next/"


@pytest.fixture
def correlation_id():
    return "6a72c3e5-1236-46e9-9d40-a78758a25e48"


@pytest.fixture
def upload_message():
    return "Hi!"


@pytest.fixture
def report_upload(setup_server, next_url, upload_message, correlation_id):
    return setup_server(
        lambda h: h.respond_with_json(
            {"message": upload_message, "next": next_url, "correlation_id": correlation_id},
            status=202,
        ),
        "POST",
        "/reports/upload/",
    )


@pytest.fixture
def analyze_schema(request, setup_server):
    marker = request.node.get_closest_marker("analyze_schema")
    _extensions = None
    _payload = None
    _status = None
    if marker:
        _extensions = marker.kwargs.get("extensions")
        _payload = marker.kwargs.get("payload")
        _status = marker.kwargs.get("status", 200)

    def _analyze_schema(payload=None, status=None, extensions=None):
        payload = payload or _payload
        status = status or _status or 200
        if payload is not None:
            return setup_server(lambda h: h.respond_with_data(payload, status=status), "POST", "/cli/analysis/")
        extensions = extensions or _extensions or []
        return setup_server(
            lambda h: h.respond_with_json(
                {"id": "42", "message": "Success", "elapsed": 1.42, "extensions": extensions},
                status=status,
            ),
            "POST",
            "/cli/analysis/",
        )

    return _analyze_schema


@dataclass
class Service:
    server: PluginHTTPServer
    hostname: str
    token: str

    @property
    def base_url(self) -> str:
        return f"http://{self.hostname}"

    def assert_call(self, idx, url, response_status):
        item = self.server.log[idx]
        assert item[0].url.endswith(url)
        assert item[1].status_code == response_status


@pytest.fixture
def hostname(httpserver):
    return f"{httpserver.host}:{httpserver.port}"


@pytest.fixture
def service(
    request, httpserver, hostname, service_setup, get_project_details, report_upload, analyze_schema, service_token
):
    marker = request.node.get_closest_marker("analyze_schema")
    enabled = marker.kwargs.get("autouse", True) if marker else True
    if enabled:
        analyze_schema()
    return Service(server=httpserver, hostname=hostname, token=service_token)


@pytest.fixture
def hosts_file(tmp_path):
    return tmp_path / "hosts.toml"


@pytest.fixture
def service_client(service, service_token):
    return ServiceClient(base_url=service.base_url, token=service_token)


@pytest.fixture
def service_report_handler(service_client, hostname, hosts_file, openapi3_schema_url):
    handler = ServiceReportHandler(
        service_client,
        host_data=HostData(hostname, hosts_file),
        api_name="test",
        location=openapi3_schema_url,
        base_url=None,
        started_at=current_datetime(),
        telemetry=False,
        out_queue=Queue(),
        in_queue=Queue(),
    )
    yield handler
    handler.shutdown()


@pytest.fixture
def file_report_handler(service_client, hostname, hosts_file, openapi3_schema_url, tmp_path):
    report_file = tmp_path / "report.tar.gz"
    handler = FileReportHandler(
        file_handle=click.utils.LazyFile(str(report_file), mode="wb"),
        api_name=None,
        location=openapi3_schema_url,
        base_url=None,
        started_at=current_datetime(),
        telemetry=False,
        in_queue=Queue(),
        out_queue=Queue(),
    )
    yield handler
    handler.shutdown()


@pytest.fixture
def read_report():
    @contextmanager
    def reader(data):
        buffer = BytesIO()
        buffer.write(data)
        buffer.seek(0)
        with tarfile.open(mode="r:gz", fileobj=buffer) as tar:
            yield tar

    return reader
