import base64
import io
import json
import platform
import re
from unittest.mock import ANY
from urllib.parse import parse_qsl, quote_plus, unquote_plus, urlencode, urlparse, urlunparse

import harfile
import pytest
import requests
import yaml
from _pytest.main import ExitCode
from hypothesis import example, given
from hypothesis import strategies as st
from urllib3._collections import HTTPHeaderDict

from schemathesis.cli.cassettes import (
    CassetteFormat,
    _cookie_to_har,
    filter_cassette,
    get_command_representation,
    get_prepared_request,
    write_double_quoted,
)
from schemathesis.core import SCHEMATHESIS_TEST_CASE_HEADER
from schemathesis.core.transport import USER_AGENT
from schemathesis.generation import GenerationMode
from schemathesis.runner.models import Request


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


@pytest.mark.parametrize("mode", [m.value for m in GenerationMode.all()] + ["all"])
@pytest.mark.parametrize("args", [(), ("--cassette-preserve-exact-body-bytes",)], ids=("plain", "base64"))
@pytest.mark.operations("success", "upload_file")
def test_store_cassette(cli, schema_url, cassette_path, hypothesis_max_examples, args, mode):
    hypothesis_max_examples = hypothesis_max_examples or 2
    result = cli.run(
        schema_url,
        f"--cassette-path={cassette_path}",
        f"--hypothesis-max-examples={hypothesis_max_examples}",
        f"--generation-mode={mode}",
        "--experimental=coverage-phase",
        "--hypothesis-seed=1",
        *args,
    )
    assert result.exit_code == ExitCode.OK, result.stdout
    cassette = load_cassette(cassette_path)
    interactions = cassette["http_interactions"]
    assert interactions[0]["status"] == "SUCCESS"
    assert cassette["seed"] == 1
    if mode == "all":
        assert interactions[0]["generation"]["mode"] in ["positive", "negative"]
    else:
        assert interactions[0]["generation"]["mode"] == mode
    assert interactions[0]["phase"]["name"] in ("explicit", "coverage", "generate")
    assert float(interactions[0]["response"]["elapsed"]) >= 0
    if mode == "positive":
        assert load_response_body(cassette, 0) == '{"success": true}'
    assert all("checks" in interaction for interaction in interactions)
    assert len(interactions[0]["checks"]) == 2
    assert interactions[0]["checks"][0] == {
        "name": "not_a_server_error",
        "status": "SUCCESS",
        "message": None,
    }
    assert len(interactions[1]["checks"]) == 2
    for interaction in interactions:
        if interaction["phase"]["name"] == "coverage":
            if interaction["generation"]["mode"] == "negative" and not interaction["phase"]["data"][
                "description"
            ].startswith("Unspecified"):
                assert interaction["phase"]["data"]["location"] is not None
                assert interaction["phase"]["data"]["parameter"] is not None
                assert interaction["phase"]["data"]["parameter_location"] is not None


@pytest.mark.operations("success", "upload_file")
def test_dry_run(cli, schema_url, cassette_path, hypothesis_max_examples):
    hypothesis_max_examples = hypothesis_max_examples or 2
    result = cli.run(
        schema_url,
        f"--cassette-path={cassette_path}",
        f"--hypothesis-max-examples={hypothesis_max_examples}",
        "--experimental=coverage-phase",
        "--hypothesis-seed=1",
        "--dry-run",
    )
    assert result.exit_code == ExitCode.OK, result.stdout
    cassette = load_cassette(cassette_path)
    assert cassette["http_interactions"][0]["status"] == "SKIP"
    assert cassette["seed"] == 1
    assert cassette["http_interactions"][0]["phase"]["name"] in ("explicit", "coverage", "generate")
    assert all("checks" in interaction for interaction in cassette["http_interactions"])
    assert cassette["http_interactions"][0]["response"] is None
    assert len(cassette["http_interactions"][0]["checks"]) == 0
    assert len(cassette["http_interactions"][1]["checks"]) == 0


@pytest.mark.operations("slow")
@pytest.mark.openapi_version("3.0")
def test_store_timeout(cli, schema_url, cassette_path):
    result = cli.run(
        schema_url,
        f"--cassette-path={cassette_path}",
        "--hypothesis-max-examples=1",
        "--request-timeout=0.001",
        "--hypothesis-seed=1",
    )
    assert result.exit_code == ExitCode.TESTS_FAILED, result.stdout
    cassette = load_cassette(cassette_path)
    assert cassette["http_interactions"][0]["status"] == "FAILURE"
    assert cassette["seed"] == 1
    assert cassette["http_interactions"][0]["response"] is None


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
    assert len(cassette["http_interactions"]) >= 1
    # Then their statuses should be reflected in the "status" field
    # And it should not be overridden by the overall test status
    assert cassette["http_interactions"][0]["status"] == "FAILURE"
    assert load_response_body(cassette, 0) == "500: Internal Server Error"


def test_bad_yaml_headers(ctx, cli, cassette_path, hypothesis_max_examples, openapi3_base_url):
    # See GH-708
    # When the schema expects an input that is not ascii and represented as UTF-8
    # And is not representable in CP1251. E.g. "àààà"
    # And these interactions are recorded to a cassette
    fixed_header = "àààà"
    header_name = "*lh"
    schema_path = ctx.openapi.write_schema(
        {
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
        format="yaml",
    )
    result = cli.run(
        str(schema_path),
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


def test_get_command_representation(mocker):
    mocker.patch("schemathesis.cli.cassettes.sys.argv", ["schemathesis", "run", "http://example.com/schema.yaml"])
    assert get_command_representation() == "st run http://example.com/schema.yaml"


@pytest.mark.skipif(platform.system() == "Windows", reason="Simpler to setup on Linux")
@pytest.mark.operations("success")
def test_run_subprocess(testdir, cassette_path, hypothesis_max_examples, schema_url, snapshot_cli):
    result = testdir.run(
        "schemathesis",
        "run",
        f"--cassette-path={cassette_path}",
        f"--hypothesis-max-examples={hypothesis_max_examples or 2}",
        schema_url,
    )
    assert result == snapshot_cli
    cassette = load_cassette(cassette_path)
    assert len(cassette["http_interactions"]) == 1
    command = (
        f"st run --cassette-path={cassette_path} "
        f"--hypothesis-max-examples={hypothesis_max_examples or 2} {schema_url}"
    )
    assert cassette["command"] == command


@pytest.mark.operations("__all__")
@pytest.mark.parametrize("verbose", [True, False])
@pytest.mark.parametrize("args", [(), ("--cassette-preserve-exact-body-bytes",)], ids=("plain", "base64"))
async def test_replay(
    openapi_version, cli, schema_url, app, reset_app, cassette_path, hypothesis_max_examples, verbose, args
):
    # Record a cassette
    result = cli.run(
        schema_url,
        f"--cassette-path={cassette_path}",
        f"--hypothesis-max-examples={hypothesis_max_examples or 1}",
        "--hypothesis-seed=1",
        "--checks=all",
        *args,
    )
    assert result.exit_code == ExitCode.TESTS_FAILED, result.stdout
    case_ids = re.findall("Test Case ID: (\\w+)", result.stdout)
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
@pytest.mark.parametrize("value", ["true", "false"])
@pytest.mark.parametrize("args", [(), ("--cassette-preserve-exact-body-bytes",)], ids=("plain", "base64"))
def test_har_format(cli, schema_url, cassette_path, hypothesis_max_examples, args, value):
    cassette_path = cassette_path.with_suffix(".har")
    auth = "secret"
    result = cli.run(
        schema_url,
        f"--cassette-path={cassette_path}",
        "--cassette-format=har",
        f"--hypothesis-max-examples={hypothesis_max_examples or 1}",
        "--hypothesis-seed=1",
        "--checks=all",
        f"-H Authorization: {auth}",
        f"--sanitize-output={value}",
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
    for entry in data["log"]["entries"]:
        for header in entry["request"]["headers"]:
            if header["name"] == "Authorization":
                if value == "true":
                    assert header["value"] != auth
                else:
                    assert header["value"] == auth


@pytest.mark.operations("__all__")
def test_har_format_dry_run(cli, schema_url, cassette_path, hypothesis_max_examples):
    cassette_path = cassette_path.with_suffix(".har")
    result = cli.run(
        schema_url,
        f"--cassette-path={cassette_path}",
        "--cassette-format=har",
        f"--hypothesis-max-examples={hypothesis_max_examples or 1}",
        "--hypothesis-seed=1",
        "--checks=all",
        "--dry-run",
    )
    assert result.exit_code == ExitCode.TESTS_FAILED, result.stdout
    assert str(cassette_path) in result.stdout
    assert cassette_path.exists()
    with cassette_path.open(encoding="utf-8") as fd:
        data = json.load(fd)
    assert "log" in data
    assert "entries" in data["log"]
    assert len(data["log"]["entries"]) > 1
    assert data["log"]["entries"][0]["response"]["status"] == 0


def test_invalid_format():
    with pytest.raises(ValueError, match="Invalid value for cassette format: invalid. Available formats: vcr, har"):
        CassetteFormat.from_str("invalid")


@pytest.mark.parametrize(
    ("value", "expected"),
    [
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
    ],
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
    )
    # Then tests should pass
    assert result.exit_code == ExitCode.OK, result.stdout
    # And cassette can be replayed
    result = cli.replay(str(cassette_path))
    assert result.exit_code == ExitCode.OK, result.stdout
    # And should be loadable


@pytest.mark.parametrize("value", ["true", "false"])
@pytest.mark.operations("headers")
def test_output_sanitization(cli, openapi2_schema_url, hypothesis_max_examples, cassette_path, value):
    auth = "secret-auth"
    result = cli.run(
        openapi2_schema_url,
        f"--cassette-path={cassette_path}",
        f"--hypothesis-max-examples={hypothesis_max_examples or 5}",
        "--hypothesis-seed=1",
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
    ("filters", "expected"),
    [
        ({"id_": "1"}, ["1"]),
        ({"id_": "2"}, ["2"]),
        ({"status": "SUCCESS"}, ["1"]),
        ({"status": "success"}, ["1"]),
        ({"status": "ERROR"}, ["2"]),
        ({"uri": "succe.*"}, ["1"]),
        ({"method": "PO"}, ["2"]),
        ({"uri": "error|failure"}, ["2", "3"]),
        ({"uri": "error|failure", "method": "POST"}, ["2"]),
    ],
)
def test_filter_cassette(filters, expected):
    cassette = [
        {"id": "1", "status": "SUCCESS", "request": {"uri": "http://127.0.0.1/api/success", "method": "GET"}},
        {"id": "2", "status": "ERROR", "request": {"uri": "http://127.0.0.1/api/error", "method": "POST"}},
        {"id": "3", "status": "FAILURE", "request": {"uri": "http://127.0.0.1/api/failure", "method": "PUT"}},
    ]
    assert list(filter_cassette(cassette, **filters)) == [item for item in cassette if item["id"] in expected]


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
