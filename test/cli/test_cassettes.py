import base64
import io
import json
import platform
from unittest.mock import ANY

import harfile
import pytest
import yaml
from _pytest.main import ExitCode
from hypothesis import example, given
from hypothesis import strategies as st

from schemathesis.cli.commands.run.handlers.cassettes import _cookie_to_har, write_double_quoted
from schemathesis.generation import GenerationMode


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
@pytest.mark.parametrize("args", [(), ("--report-preserve-bytes",)], ids=("plain", "base64"))
@pytest.mark.operations("success", "upload_file")
def test_store_cassette(cli, schema_url, cassette_path, hypothesis_max_examples, args, mode):
    hypothesis_max_examples = hypothesis_max_examples or 2
    result = cli.run(
        schema_url,
        f"--report-vcr-path={cassette_path}",
        f"--max-examples={hypothesis_max_examples}",
        f"--mode={mode}",
        "--experimental=coverage-phase",
        "--seed=1",
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


@pytest.mark.parametrize("format", ["vcr", "har"])
@pytest.mark.operations("slow")
@pytest.mark.openapi_version("3.0")
def test_store_timeout(cli, schema_url, cassette_path, format):
    result = cli.run(
        schema_url,
        f"--report-{format}-path={cassette_path}",
        "--max-examples=1",
        "--request-timeout=0.001",
        "--seed=1",
    )
    assert result.exit_code == ExitCode.TESTS_FAILED, result.stdout
    if format == "vcr":
        cassette = load_cassette(cassette_path)
        assert cassette["http_interactions"][0]["status"] == "ERROR"
        assert cassette["seed"] == 1
        assert cassette["http_interactions"][0]["response"] is None
    else:
        with cassette_path.open(encoding="utf-8") as fd:
            data = json.load(fd)
            assert len(data["log"]["entries"]) == 2
            assert data["log"]["entries"][1]["response"]["bodySize"] == -1


@pytest.mark.operations("flaky")
def test_interaction_status(cli, openapi3_schema_url, hypothesis_max_examples, cassette_path):
    # See GH-695
    # When an API operation has responses with SUCCESS and FAILURE statuses
    result = cli.run(
        openapi3_schema_url,
        f"--report-vcr-path={cassette_path}",
        f"--max-examples={hypothesis_max_examples or 5}",
        "--seed=1",
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
        f"--url={openapi3_base_url}",
        f"--max-examples={hypothesis_max_examples or 1}",
        f"--report-vcr-path={cassette_path}",
    )
    # Then the test run should be successful
    assert result.exit_code == ExitCode.OK, result.stdout
    # And there should be no signs of encoding errors
    assert "UnicodeEncodeError" not in result.stdout
    # And the cassette should be correctly recorded
    cassette = load_cassette(cassette_path)
    assert len(cassette["http_interactions"]) == 1
    assert cassette["http_interactions"][0]["request"]["headers"][header_name] == [fixed_header]


@pytest.mark.skipif(platform.system() == "Windows", reason="Simpler to setup on Linux")
@pytest.mark.operations("success")
def test_run_subprocess(testdir, cassette_path, hypothesis_max_examples, schema_url, snapshot_cli):
    result = testdir.run(
        "schemathesis",
        "run",
        f"--report-vcr-path={cassette_path}",
        f"--max-examples={hypothesis_max_examples or 2}",
        schema_url,
    )
    assert result == snapshot_cli
    cassette = load_cassette(cassette_path)
    assert len(cassette["http_interactions"]) == 1
    command = f"st run --report-vcr-path={cassette_path} --max-examples={hypothesis_max_examples or 2} {schema_url}"
    assert cassette["command"] == command


@pytest.mark.operations("__all__")
@pytest.mark.parametrize("value", ["true", "false"])
@pytest.mark.parametrize("args", [(), ("--report-preserve-bytes",)], ids=("plain", "base64"))
def test_har_format(cli, schema_url, cassette_path, hypothesis_max_examples, args, value):
    cassette_path = cassette_path.with_suffix(".har")
    auth = "secret"
    result = cli.run(
        schema_url,
        f"--report-har-path={cassette_path}",
        f"--max-examples={hypothesis_max_examples or 1}",
        "--seed=1",
        "--checks=all",
        f"-H Authorization: {auth}",
        f"--output-sanitize={value}",
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
        return ["--tls-verify=false"], "verify", False, ExitCode.OK
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
        return ["--proxy=http://127.0.0.1"], "proxies", {"all": "http://127.0.0.1"}, exit_code


@pytest.mark.parametrize("value", ["true", "false"])
@pytest.mark.operations("headers")
def test_output_sanitization(cli, openapi2_schema_url, hypothesis_max_examples, cassette_path, value):
    auth = "secret-auth"
    result = cli.run(
        openapi2_schema_url,
        f"--report-vcr-path={cassette_path}",
        f"--max-examples={hypothesis_max_examples or 5}",
        "--seed=1",
        f"-H Authorization: {auth}",
        f"--output-sanitize={value}",
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


@pytest.mark.openapi_version("3.0")
def test_forbid_preserve_bytes_without_cassette_path(cli, schema_url, snapshot_cli):
    # When `--report-preserve-bytes` is specified without `--report-vcr-path` or `--report=vcr`
    # Then it is an error
    assert cli.run(schema_url, "--report-preserve-bytes") == snapshot_cli


@pytest.mark.openapi_version("3.0")
def test_report_dir(cli, schema_url, tmp_path):
    # When report directory is specified with a report format
    report_dir = tmp_path / "reports"
    cli.run(
        schema_url,
        f"--report-dir={report_dir}",
        "--report=junit",
        "--max-examples=1",
    )
    # And the report should be created in the specified directory
    assert report_dir.exists()
    assert (report_dir / "junit.xml").exists()

    # When multiple report formats are specified
    cli.run(
        schema_url,
        f"--report-dir={report_dir}",
        "--report=vcr,har",
        "--max-examples=1",
    )
    # Then all reports should be created in the specified directory
    assert (report_dir / "vcr.yaml").exists()
    assert (report_dir / "har.json").exists()


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
