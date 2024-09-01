import base64
import io
import json
import platform
import re
import threading
from unittest.mock import ANY
from urllib.parse import parse_qsl, quote_plus, unquote_plus, urlencode, urlparse, urlunparse
from uuid import UUID

import harfile
import pytest
import requests
import yaml
from _pytest.main import ExitCode
from hypothesis import example, given
from hypothesis import strategies as st
from urllib3._collections import HTTPHeaderDict

from schemathesis.cli import DEPRECATED_CASSETTE_PATH_OPTION_WARNING
from schemathesis.cli.cassettes import (
    CassetteFormat,
    _cookie_to_har,
    filter_cassette,
    get_command_representation,
    get_prepared_request,
    write_double_quoted,
)
from schemathesis.cli.reporting import TEST_CASE_ID_TITLE
from schemathesis.constants import SCHEMATHESIS_TEST_CASE_HEADER, USER_AGENT
from schemathesis.generation import DataGenerationMethod
from schemathesis.models import Request


@pytest.fixture
def cassette_path(tmp_path):
    return tmp_path / "output.yaml"


def load_cassette(path):
    with path.open(encoding="utf-8") as fd:
        return yaml.safe_load(fd)


def load_response_body(cassette, idx):
    body = cassette["http_interactions"][idx]["response"]["body"]
    if "base64_string" in body:
        return base64.b64decode(body["base64_string"]).decode()
    return body["string"]


@pytest.mark.parametrize("data_generation_method", [m.value for m in DataGenerationMethod.all()] + ["all"])
@pytest.mark.parametrize("args", ((), ("--cassette-preserve-exact-body-bytes",)), ids=("plain", "base64"))
@pytest.mark.operations("success", "upload_file")
def test_store_cassette(cli, schema_url, cassette_path, hypothesis_max_examples, args, data_generation_method):
    hypothesis_max_examples = hypothesis_max_examples or 2
    result = cli.run(
        schema_url,
        f"--cassette-path={cassette_path}",
        f"--hypothesis-max-examples={hypothesis_max_examples}",
        f"--data-generation-method={data_generation_method}",
        "--experimental=coverage-phase",
        "--show-trace",
        "--hypothesis-seed=1",
        *args,
    )
    assert result.exit_code == ExitCode.OK, result.stdout
    cassette = load_cassette(cassette_path)
    assert cassette["http_interactions"][0]["id"] == "1"
    assert cassette["http_interactions"][1]["id"] == "2"
    assert cassette["http_interactions"][0]["status"] == "SUCCESS"
    assert cassette["http_interactions"][0]["seed"] == "1"
    if data_generation_method == "all":
        assert cassette["http_interactions"][0]["data_generation_method"] in ["positive", "negative"]
    else:
        assert cassette["http_interactions"][0]["data_generation_method"] == data_generation_method
    assert cassette["http_interactions"][0]["phase"] in ("explicit", "coverage", "generate")
    assert cassette["http_interactions"][0]["thread_id"] == threading.get_ident()
    correlation_id = cassette["http_interactions"][0]["correlation_id"]
    UUID(correlation_id)
    assert float(cassette["http_interactions"][0]["elapsed"]) >= 0
    if data_generation_method == "positive":
        assert load_response_body(cassette, 0) == '{"success": true}'
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
        f"--cassette-path={cassette_path}",
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
    assert load_response_body(cassette, 0) == "500: Internal Server Error"
    assert cassette["http_interactions"][1]["status"] == "SUCCESS"
    assert load_response_body(cassette, 1) == '{"result": "flaky!"}'
    assert cassette["http_interactions"][2]["status"] == "SUCCESS"
    assert load_response_body(cassette, 2) == '{"result": "flaky!"}'


def test_bad_yaml_headers(testdir, cli, cassette_path, hypothesis_max_examples, openapi3_base_url):
    # See GH-708
    # When the schema expects an input that is not ascii and represented as UTF-8
    # And is not representable in CP1251. E.g. "àààà"
    # And these interactions are recorded to a cassette
    fixed_header = "àààà"
    header_name = "*lh"
    raw_schema = {
        "openapi": "3.0.2",
        "info": {"title": "Test", "description": "Test", "version": "0.1.0"},
        "paths": {
            "/users": {
                "post": {
                    "parameters": [
                        {
                            "name": header_name,
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
        f"--cassette-path={cassette_path}",
    )
    # Then the test run should be successful
    assert result.exit_code == ExitCode.OK, result.stdout
    # And there should be no signs of encoding errors
    assert "UnicodeEncodeError" not in result.stdout
    # And the cassette should be correctly recorded
    cassette = load_cassette(cassette_path)
    assert len(cassette["http_interactions"]) == 1
    assert cassette["http_interactions"][0]["request"]["headers"][header_name] == [fixed_header]


def test_get_command_representation_unknown():
    assert get_command_representation() == "<unknown entrypoint>"


def test_get_command_representation(mocker):
    mocker.patch("schemathesis.cli.cassettes.sys.argv", ["schemathesis", "run", "http://example.com/schema.yaml"])
    assert get_command_representation() == "st run http://example.com/schema.yaml"


@pytest.mark.operations("success")
def test_run_subprocess(testdir, cassette_path, hypothesis_max_examples, schema_url):
    result = testdir.run(
        "schemathesis",
        "run",
        f"--cassette-path={cassette_path}",
        f"--hypothesis-max-examples={hypothesis_max_examples or 2}",
        schema_url,
    )
    assert result.ret == ExitCode.OK
    expected = f"Network log: {cassette_path}"
    assert result.outlines[20] == expected or result.outlines[21]
    cassette = load_cassette(cassette_path)
    assert len(cassette["http_interactions"]) == 1
    command = (
        f"st run --cassette-path={cassette_path} "
        f"--hypothesis-max-examples={hypothesis_max_examples or 2} {schema_url}"
    )
    assert cassette["command"] == command


@pytest.mark.operations("invalid")
def test_main_process_error(cli, schema_url, hypothesis_max_examples, cassette_path):
    # When there is an error in the main process before the background writer is finished
    # Here it is happening because the schema is not valid
    result = cli.run(
        schema_url,
        f"--cassette-path={cassette_path}",
        f"--hypothesis-max-examples={hypothesis_max_examples or 1}",
        "--hypothesis-seed=1",
        "--validate-schema=true",
    )
    assert result.exit_code == ExitCode.TESTS_FAILED, result.stdout
    # Then there should be no hanging threads
    # And no cassette
    cassette = load_cassette(cassette_path)
    assert cassette is None


@pytest.mark.operations("__all__")
@pytest.mark.parametrize("verbose", (True, False))
@pytest.mark.parametrize("args", ((), ("--cassette-preserve-exact-body-bytes",)), ids=("plain", "base64"))
async def test_replay(
    openapi_version, cli, schema_url, app, reset_app, cassette_path, hypothesis_max_examples, verbose, args
):
    # Record a cassette
    result = cli.run(
        schema_url,
        f"--cassette-path={cassette_path}",
        f"--hypothesis-max-examples={hypothesis_max_examples or 1}",
        "--hypothesis-seed=1",
        "--validate-schema=false",
        "--checks=all",
        *args,
    )
    assert result.exit_code == ExitCode.TESTS_FAILED, result.stdout
    case_ids = re.findall(f"{TEST_CASE_ID_TITLE}: (\\w+)", result.stdout)
    # these requests are not needed
    reset_app(openapi_version)
    assert not app["incoming_requests"]
    # When a valid cassette is replayed
    replay_args = []
    if verbose:
        replay_args.append("-v")
    result = cli.replay(str(cassette_path), *replay_args)
    assert result.exit_code == ExitCode.OK, result.stdout
    if verbose:
        assert "Old payload : {" in result.stdout
        assert "New payload : {" in result.stdout
    cassette = load_cassette(cassette_path)
    interactions = cassette["http_interactions"]
    for case_id in case_ids:
        found = False
        existing_ids = []
        for interaction in interactions:
            current_case_id = interaction["request"]["headers"][SCHEMATHESIS_TEST_CASE_HEADER][0]
            existing_ids.append(current_case_id)
            if current_case_id == case_id:
                found = True
                break
        if not found:
            raise AssertionError(
                f"Test case with ID `{case_id}` is not found in the cassette. Existing IDs: {existing_ids}"
            )
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
            if "body" in serialized:
                if "base64_string" in serialized["body"]:
                    assert content == base64.b64decode(serialized["body"]["base64_string"])
                else:
                    stored_content = serialized["body"]["string"].encode()
                    assert content == stored_content or content == stored_content.strip()
                compare_headers(request, serialized["headers"])


@pytest.mark.operations("__all__")
@pytest.mark.parametrize("args", ((), ("--cassette-preserve-exact-body-bytes",)), ids=("plain", "base64"))
def test_har_format(cli, schema_url, cassette_path, hypothesis_max_examples, args):
    cassette_path = cassette_path.with_suffix(".har")
    result = cli.run(
        schema_url,
        f"--cassette-path={cassette_path}",
        "--cassette-format=har",
        f"--hypothesis-max-examples={hypothesis_max_examples or 1}",
        "--hypothesis-seed=1",
        "--validate-schema=false",
        "--checks=all",
        *args,
    )
    assert result.exit_code == ExitCode.TESTS_FAILED, result.stdout
    assert str(cassette_path) in result.stdout
    assert cassette_path.exists()
    with cassette_path.open(encoding="utf-8") as fd:
        data = json.load(fd)
    assert "log" in data
    assert "entries" in data["log"]
    assert len(data["log"]["entries"]) > 1


def test_invalid_format():
    with pytest.raises(ValueError, match="Invalid value for cassette format: invalid. Available formats: vcr, har"):
        CassetteFormat.from_str("invalid")


@pytest.mark.parametrize(
    "value, expected",
    (
        (
            "has_recent_activity=1; path=/; expires=Sat, 29 Jun 2024 18:22:49 GMT; secure; HttpOnly; SameSite=Lax",
            [
                harfile.Cookie(
                    name="has_recent_activity",
                    value="1",
                    path="/",
                    expires="Sat, 29 Jun 2024 18:22:49 GMT",
                    secure=True,
                    httpOnly=True,
                ),
            ],
        ),
        (
            "foo=bar; spam=baz;",
            [
                harfile.Cookie(name="foo", value="bar"),
                harfile.Cookie(name="spam", value="baz"),
            ],
        ),
    ),
)
def test_cookie_to_har(value, expected):
    assert list(_cookie_to_har(value)) == expected


@pytest.fixture(params=["tls-verify", "cert", "cert-and-key", "proxies"])
def request_args(request, tmp_path):
    if request.param == "tls-verify":
        return ["--request-tls-verify=false"], "verify", False, ExitCode.OK
    cert = tmp_path / "cert.tmp"
    cert.touch()
    if request.param == "cert":
        return [f"--request-cert={cert}"], "cert", str(cert), ExitCode.OK
    if request.param == "cert-and-key":
        key = tmp_path / "key.tmp"
        key.touch()
        return [f"--request-cert={cert}", f"--request-cert-key={key}"], "cert", (str(cert), str(key)), ExitCode.OK
    if request.param == "proxies":
        if platform.system() == "Windows":
            exit_code = ExitCode.OK
        else:
            exit_code = ExitCode.TESTS_FAILED
        return ["--request-proxy=http://127.0.0.1"], "proxies", {"all": "http://127.0.0.1"}, exit_code


@pytest.mark.openapi_version("3.0")
def test_replay_requests_options(cli, schema_url, cassette_path, request_args, mocker):
    # Record a cassette
    cli.run(
        schema_url,
        f"--cassette-path={cassette_path}",
        "--hypothesis-max-examples=1",
        "--hypothesis-seed=1",
        "--validate-schema=false",
        "--checks=all",
    )
    send = mocker.spy(requests.adapters.HTTPAdapter, "send")
    # When parameters for `requests` are passed via command line
    args, key, expected, exit_code = request_args
    result = cli.replay(str(cassette_path), *args)
    assert result.exit_code == exit_code, result.stdout
    # Then they should properly setup replayed requests
    for _, kwargs in send.call_args_list:
        assert kwargs[key] == expected


@pytest.mark.operations("headers")
def test_headers_serialization(cli, openapi2_schema_url, hypothesis_max_examples, cassette_path):
    # See GH-783
    # When headers contain control characters that are not directly representable in YAML
    result = cli.run(
        openapi2_schema_url,
        f"--cassette-path={cassette_path}",
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


@pytest.mark.parametrize("value", ("true", "false"))
@pytest.mark.operations("headers")
def test_output_sanitization(cli, openapi2_schema_url, hypothesis_max_examples, cassette_path, value):
    auth = "secret-auth"
    result = cli.run(
        openapi2_schema_url,
        f"--cassette-path={cassette_path}",
        f"--hypothesis-max-examples={hypothesis_max_examples or 5}",
        "--hypothesis-seed=1",
        "--validate-schema=false",
        f"-H Authorization: {auth}",
        f"--sanitize-output={value}",
    )
    assert result.exit_code == ExitCode.OK, result.stdout
    cassette = load_cassette(cassette_path)

    if value == "true":
        expected = "[Filtered]"
    else:
        expected = ANY
    interactions = cassette["http_interactions"]
    assert all(entry["request"]["headers"]["X-Token"] == [expected] for entry in interactions)
    assert all(entry["request"]["headers"]["Authorization"] == [expected] for entry in interactions)
    # The app can reject requests, so the error won't contain this header
    assert all(
        entry["response"]["headers"]["X-Token"] == [expected]
        for entry in interactions
        if "X-Token" in entry["response"]["headers"]
    )


def test_multiple_cookies(base_url):
    headers = {"User-Agent": USER_AGENT}
    response = requests.get(f"{base_url}/success", cookies={"foo": "bar", "baz": "spam"}, headers=headers, timeout=1)
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


def test_empty_body():
    # When `body` is an empty string
    request = get_prepared_request({"method": "POST", "uri": "http://127.0.0.1", "body": {"string": ""}, "headers": {}})
    # Then the resulting request will not have a body
    assert request.body is None


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


@pytest.mark.operations("success")
@pytest.mark.openapi_version("3.0")
def test_use_deprecation(cli, schema_url, cassette_path):
    result = cli.run(
        schema_url,
        f"--store-network-log={cassette_path}",
    )
    assert result.exit_code == ExitCode.OK, result.stdout
    assert result.stdout.splitlines()[0] == DEPRECATED_CASSETTE_PATH_OPTION_WARNING


@pytest.mark.operations("success")
@pytest.mark.openapi_version("3.0")
def test_forbid_simultaneous_use_of_deprecated_and_new_options(cli, schema_url, cassette_path, snapshot_cli):
    assert (
        cli.run(
            schema_url,
            f"--store-network-log={cassette_path}",
            f"--cassette-path={cassette_path}",
        )
        == snapshot_cli
    )


@pytest.mark.openapi_version("3.0")
def test_forbid_preserve_exact_bytes_without_cassette_path(cli, schema_url, snapshot_cli):
    # When `--cassette-preserve-exact-body-bytes` is specified without `--cassette-path`
    # Then it is an error
    assert cli.run(schema_url, "--cassette-preserve-exact-body-bytes") == snapshot_cli


@given(text=st.text())
@example("Test")
@example("\ufeff")
@example("\ue001")
@example("\xa1")
@example("\x21")
@example("\x07")
@example("🎉")
def test_write_double_quoted(text):
    stream = io.StringIO()
    write_double_quoted(stream, text)
    assert yaml.safe_load(stream.getvalue()) == text
