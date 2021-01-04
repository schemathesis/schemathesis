import base64
from urllib.parse import parse_qsl, quote_plus, unquote_plus, urlencode, urlparse, urlunparse

import pytest
import requests
import yaml
from _pytest.main import ExitCode
from urllib3._collections import HTTPHeaderDict

from schemathesis.cli.cassettes import filter_cassette, get_command_representation, get_prepared_request
from schemathesis.constants import USER_AGENT
from schemathesis.models import Request


@pytest.fixture
def cassette_path(tmp_path):
    return tmp_path / "output.yaml"


def load_cassette(path):
    with path.open(encoding="utf-8") as fd:
        return yaml.safe_load(fd)


def load_response_body(cassette, idx):
    return base64.b64decode(cassette["http_interactions"][idx]["response"]["body"]["base64_string"])


@pytest.mark.operations("success", "upload_file")
def test_store_cassette(cli, schema_url, cassette_path, hypothesis_max_examples):
    hypothesis_max_examples = hypothesis_max_examples or 2
    result = cli.run(
        schema_url,
        f"--store-network-log={cassette_path}",
        f"--hypothesis-max-examples={hypothesis_max_examples}",
        "--hypothesis-seed=1",
    )
    assert result.exit_code == ExitCode.OK, result.stdout
    cassette = load_cassette(cassette_path)
    assert len(cassette["http_interactions"]) == 1 + hypothesis_max_examples
    assert cassette["http_interactions"][0]["id"] == "1"
    assert cassette["http_interactions"][1]["id"] == "2"
    assert cassette["http_interactions"][0]["status"] == "SUCCESS"
    assert cassette["http_interactions"][0]["seed"] == "1"
    assert float(cassette["http_interactions"][0]["elapsed"]) >= 0
    assert load_response_body(cassette, 0) == b'{"success": true}'
    assert all("checks" in interaction for interaction in cassette["http_interactions"])
    assert len(cassette["http_interactions"][0]["checks"]) == 1
    assert cassette["http_interactions"][0]["checks"][0] == {
        "name": "not_a_server_error",
        "status": "SUCCESS",
        "message": None,
    }
    assert len(cassette["http_interactions"][1]["checks"]) == 1


@pytest.mark.operations("flaky")
def test_interaction_status(cli, openapi3_schema_url, hypothesis_max_examples, cassette_path):
    # See GH-695
    # When an API operation has responses with SUCCESS and FAILURE statuses
    result = cli.run(
        openapi3_schema_url,
        f"--store-network-log={cassette_path}",
        f"--hypothesis-max-examples={hypothesis_max_examples or 5}",
        "--hypothesis-seed=1",
    )
    assert result.exit_code == ExitCode.TESTS_FAILED, result.stdout
    cassette = load_cassette(cassette_path)
    # Note. There could be more than 3 calls, depends on Hypothesis internals
    assert len(cassette["http_interactions"]) >= 3
    # Then their statuses should be reflected in the "status" field
    # And it should not be overridden by the overall test status
    assert cassette["http_interactions"][0]["status"] == "FAILURE"
    assert load_response_body(cassette, 0) == b"500: Internal Server Error"
    assert cassette["http_interactions"][1]["status"] == "SUCCESS"
    assert load_response_body(cassette, 1) == b'{"result": "flaky!"}'
    assert cassette["http_interactions"][2]["status"] == "SUCCESS"
    assert load_response_body(cassette, 2) == b'{"result": "flaky!"}'


def test_encoding_error(testdir, cli, cassette_path, hypothesis_max_examples, openapi3_base_url):
    # See GH-708
    # When the schema expects an input that is not ascii and represented as UTF-8
    # And is not representable in CP1251. E.g. "àààà"
    # And these interactions are recorded to a cassette
    fixed_header = "àààà"
    raw_schema = {
        "openapi": "3.0.2",
        "info": {"title": "Test", "description": "Test", "version": "0.1.0"},
        "paths": {
            "/users": {
                "post": {
                    "parameters": [
                        {
                            "name": "X-id",
                            "in": "header",
                            "required": True,
                            "schema": {"type": "string", "enum": [fixed_header]},
                        }
                    ],
                    "responses": {
                        "200": {"description": "OK", "content": {"application/json": {"schema": {"type": "object"}}}}
                    },
                }
            }
        },
    }
    schema_file = testdir.makefile(".yaml", schema=yaml.dump(raw_schema))
    result = cli.run(
        str(schema_file),
        f"--base-url={openapi3_base_url}",
        f"--hypothesis-max-examples={hypothesis_max_examples or 1}",
        f"--store-network-log={cassette_path}",
    )
    # Then the test run should be successful
    assert result.exit_code == ExitCode.OK, result.stdout
    # And there should be no signs of encoding errors
    assert "UnicodeEncodeError" not in result.stdout
    # And the cassette should be correctly recorded
    cassette = load_cassette(cassette_path)
    assert len(cassette["http_interactions"]) == 1
    assert cassette["http_interactions"][0]["request"]["headers"]["X-id"] == [fixed_header]


def test_get_command_representation_unknown():
    assert get_command_representation() == "<unknown entrypoint>"


def test_get_command_representation(mocker):
    mocker.patch("schemathesis.cli.cassettes.sys.argv", ["schemathesis", "run", "http://example.com/schema.yaml"])
    assert get_command_representation() == "schemathesis run http://example.com/schema.yaml"


@pytest.mark.operations("success")
def test_run_subprocess(testdir, cassette_path, hypothesis_max_examples, schema_url):
    result = testdir.run(
        "schemathesis",
        "run",
        f"--store-network-log={cassette_path}",
        f"--hypothesis-max-examples={hypothesis_max_examples or 2}",
        schema_url,
    )
    assert result.ret == ExitCode.OK
    assert result.outlines[17] == f"Network log: {cassette_path}"
    cassette = load_cassette(cassette_path)
    assert len(cassette["http_interactions"]) == 1
    command = (
        f"schemathesis run --store-network-log={cassette_path} "
        f"--hypothesis-max-examples={hypothesis_max_examples or 2} {schema_url}"
    )
    assert cassette["command"] == command


@pytest.mark.operations("invalid")
def test_main_process_error(cli, schema_url, hypothesis_max_examples, cassette_path):
    # When there is an error in the main process before the background writer is finished
    # Here it is happening because the schema is not valid
    result = cli.run(
        schema_url,
        f"--store-network-log={cassette_path}",
        f"--hypothesis-max-examples={hypothesis_max_examples or 1}",
        "--hypothesis-seed=1",
    )
    assert result.exit_code == ExitCode.TESTS_FAILED, result.stdout
    # Then there should be no hanging threads
    # And no cassette
    cassette = load_cassette(cassette_path)
    assert cassette is None


@pytest.mark.operations("__all__")
async def test_replay(openapi_version, cli, schema_url, app, reset_app, cassette_path, hypothesis_max_examples):
    # Record a cassette
    result = cli.run(
        schema_url,
        f"--store-network-log={cassette_path}",
        f"--hypothesis-max-examples={hypothesis_max_examples or 1}",
        "--hypothesis-seed=1",
        "--validate-schema=false",
        "--checks=all",
    )
    assert result.exit_code == ExitCode.TESTS_FAILED, result.stdout
    # these requests are not needed
    reset_app(openapi_version)
    assert not app["incoming_requests"]
    # When a valid cassette is replayed
    result = cli.replay(str(cassette_path))
    assert result.exit_code == ExitCode.OK, result.stdout
    cassette = load_cassette(cassette_path)
    interactions = cassette["http_interactions"]
    # Then there should be the same number or fewer of requests made to the app as there are in the cassette
    # Note. Some requests that Schemathesis can send aren't parsed by aiohttp, because of e.g. invalid characters in
    # headers
    assert len(app["incoming_requests"]) <= len(interactions)
    # And if there were no requests that aiohttp failed to parse, we can compare cassette & app records
    if len(app["incoming_requests"]) == len(interactions):
        for interaction, request in zip(interactions, app["incoming_requests"]):
            # And these requests should be equal
            serialized = interaction["request"]
            assert request.method == serialized["method"]
            parsed = urlparse(str(request.url))
            encoded_query = urlencode(parse_qsl(parsed.query, keep_blank_values=True))
            encoded_path = quote_plus(unquote_plus(parsed.path), "/")
            url = urlunparse(
                (parsed.scheme, parsed.netloc, encoded_path, parsed.params, encoded_query, parsed.fragment)
            )
            assert unquote_plus(url) == unquote_plus(serialized["uri"]), request.url
            content = await request.read()
            assert content == base64.b64decode(serialized["body"]["base64_string"])
            compare_headers(request, serialized["headers"])


@pytest.mark.operations("headers")
async def test_headers_serialization(cli, openapi2_schema_url, hypothesis_max_examples, cassette_path):
    # See GH-783
    # When headers contain control characters that are not directly representable in YAML
    result = cli.run(
        openapi2_schema_url,
        f"--store-network-log={cassette_path}",
        f"--hypothesis-max-examples={hypothesis_max_examples or 100}",
        "--hypothesis-seed=1",
        "--validate-schema=false",
    )
    # Then tests should pass
    assert result.exit_code == ExitCode.OK, result.stdout
    # And cassette can be replayed
    result = cli.replay(str(cassette_path))
    assert result.exit_code == ExitCode.OK, result.stdout
    # And should be loadable
    load_cassette(cassette_path)


def test_multiple_cookies(base_url):
    headers = {"User-Agent": USER_AGENT}
    response = requests.get(f"{base_url}/success", cookies={"foo": "bar", "baz": "spam"}, headers=headers)
    request = Request.from_prepared_request(response.request)
    serialized = {
        "uri": request.uri,
        "method": request.method,
        "headers": request.headers,
        "body": {"encoding": "utf-8", "base64_string": request.body},
    }
    assert USER_AGENT in serialized["headers"]["User-Agent"]
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
