import base64
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import pytest
import requests
import yaml
from _pytest.main import ExitCode
from urllib3._collections import HTTPHeaderDict

from schemathesis.cli.cassettes import filter_cassette, get_command_representation, get_prepared_request
from schemathesis.models import Request


@pytest.fixture
def cassette_path(tmp_path):
    return tmp_path / "output.yaml"


def load_cassette(path):
    with path.open() as fd:
        return yaml.safe_load(fd)


@pytest.mark.endpoints("success", "upload_file")
def test_store_cassette(cli, schema_url, cassette_path):
    result = cli.run(
        schema_url, f"--store-network-log={cassette_path}", "--hypothesis-max-examples=2", "--hypothesis-seed=1"
    )
    assert result.exit_code == ExitCode.OK, result.stdout
    cassette = load_cassette(cassette_path)
    assert len(cassette["http_interactions"]) == 3
    assert cassette["http_interactions"][0]["id"] == "1"
    assert cassette["http_interactions"][1]["id"] == "2"
    assert cassette["http_interactions"][0]["status"] == "SUCCESS"
    assert cassette["http_interactions"][0]["seed"] == "1"
    assert float(cassette["http_interactions"][0]["elapsed"]) >= 0
    data = base64.b64decode(cassette["http_interactions"][0]["response"]["body"]["base64_string"])
    assert data == b'{"success": true}'


def test_get_command_representation_unknown():
    assert get_command_representation() == "<unknown entrypoint>"


def test_get_command_representation(mocker):
    mocker.patch("schemathesis.cli.cassettes.sys.argv", ["schemathesis", "run", "http://example.com/schema.yaml"])
    assert get_command_representation() == "schemathesis run http://example.com/schema.yaml"


@pytest.mark.endpoints("success")
def test_run_subprocess(testdir, cassette_path, schema_url):
    result = testdir.run(
        "schemathesis", "run", f"--store-network-log={cassette_path}", "--hypothesis-max-examples=2", schema_url
    )
    assert result.ret == ExitCode.OK
    assert result.outlines[17] == f"Network log: {cassette_path}"
    cassette = load_cassette(cassette_path)
    assert len(cassette["http_interactions"]) == 1
    command = f"schemathesis run --store-network-log={cassette_path} --hypothesis-max-examples=2 {schema_url}"
    assert cassette["command"] == command


@pytest.mark.endpoints("invalid")
def test_main_process_error(cli, schema_url, cassette_path):
    # When there is an error in the main process before the background writer is finished
    # Here it is happening because the schema is not valid
    result = cli.run(
        schema_url, f"--store-network-log={cassette_path}", "--hypothesis-max-examples=1", "--hypothesis-seed=1"
    )
    assert result.exit_code == ExitCode.TESTS_FAILED, result.stdout
    # Then there should be no hanging threads
    # And no cassette
    cassette = load_cassette(cassette_path)
    assert cassette is None


@pytest.mark.endpoints("__all__")
async def test_replay(cli, schema_url, app, reset_app, cassette_path):
    # Record a cassette
    result = cli.run(
        schema_url,
        f"--store-network-log={cassette_path}",
        "--hypothesis-max-examples=1",
        "--hypothesis-seed=1",
        "--validate-schema=false",
    )
    assert result.exit_code == ExitCode.TESTS_FAILED, result.stdout
    # these requests are not needed
    reset_app()
    assert not app["incoming_requests"]
    # When a valid cassette is replayed
    result = cli.replay(str(cassette_path))
    assert result.exit_code == ExitCode.OK, result.stdout
    cassette = load_cassette(cassette_path)
    interactions = cassette["http_interactions"]
    # Then there should be the same number of requests made to the app as there are in the cassette
    assert len(app["incoming_requests"]) == len(interactions)
    for interaction, request in zip(interactions, app["incoming_requests"]):
        # And these requests should be equal
        serialized = interaction["request"]
        assert request.method == serialized["method"]
        parsed = urlparse(str(request.url))
        encoded_query = urlencode(parse_qsl(parsed.query, keep_blank_values=True))
        url = urlunparse((parsed.scheme, parsed.netloc, parsed.path, parsed.params, encoded_query, parsed.fragment))
        assert url == serialized["uri"]
        content = await request.read()
        assert content == base64.b64decode(serialized["body"]["base64_string"])
        compare_headers(request, serialized["headers"])


def test_multiple_cookies(base_url):
    response = requests.get(f"{base_url}/success", cookies={"foo": "bar", "baz": "spam"})
    request = Request.from_prepared_request(response.request)
    serialized = {
        "uri": request.uri,
        "method": request.method,
        "headers": request.headers,
        "body": {"encoding": "utf-8", "base64_string": request.body},
    }
    prepared = get_prepared_request(serialized)
    compare_headers(prepared, serialized["headers"])


def compare_headers(request, serialized):
    headers = HTTPHeaderDict()
    for name, value in serialized.items():
        for sub in value:
            headers.add(name, sub)
        assert request.headers[name] == headers[name]


@pytest.mark.parametrize(
    "filters, expected",
    (
        ({"id_": "1"}, ["1"]),
        ({"id_": "2"}, ["2"]),
        ({"status": "SUCCESS"}, ["1"]),
        ({"status": "success"}, ["1"]),
        ({"status": "ERROR"}, ["2"]),
        ({"uri": "succe.*"}, ["1"]),
        ({"method": "PO"}, ["2"]),
        ({"uri": "error|failure"}, ["2", "3"]),
        ({"uri": "error|failure", "method": "POST"}, ["2"]),
    ),
)
def test_filter_cassette(filters, expected):
    cassette = [
        {"id": "1", "status": "SUCCESS", "request": {"uri": "http://127.0.0.1/api/success", "method": "GET"}},
        {"id": "2", "status": "ERROR", "request": {"uri": "http://127.0.0.1/api/error", "method": "POST"}},
        {"id": "3", "status": "FAILURE", "request": {"uri": "http://127.0.0.1/api/failure", "method": "PUT"}},
    ]
    assert list(filter_cassette(cassette, **filters)) == [item for item in cassette if item["id"] in expected]
