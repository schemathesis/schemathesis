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


@pytest.mark.parametrize("mode", [m.value for m in list(GenerationMode)] + ["all"])
@pytest.mark.parametrize("args", [(), ("--report-preserve-bytes",)], ids=("plain", "base64"))
@pytest.mark.operations("success", "upload_file")
def test_store_cassette(cli, schema_url, cassette_path, hypothesis_max_examples, args, mode):
    hypothesis_max_examples = hypothesis_max_examples or 2
    cli.run_and_assert(
        schema_url,
        f"--report-vcr-path={cassette_path}",
        f"--max-examples={hypothesis_max_examples}",
        f"--mode={mode}",
        "--seed=1",
        "--checks=not_a_server_error",
        *args,
    )
    cassette = load_cassette(cassette_path)
    interactions = cassette["http_interactions"]
    assert len(interactions) >= 2
    assert cassette["seed"] == 1

    # Basic validation on all interactions
    assert all("checks" in interaction for interaction in interactions)
    for interaction in interactions:
        assert len(interaction["checks"]) >= 1
        assert float(interaction["response"]["elapsed"]) >= 0

    # In positive mode, verify we have the /success response
    if mode == "positive":
        # Find the /success interaction (operation order may vary)
        success_idx = None
        for idx in range(len(interactions)):
            body = load_response_body(cassette, idx)
            if '{"success": true}' in body:
                success_idx = idx
                break
        assert success_idx is not None, "Could not find /success interaction in positive mode"
        success_interaction = interactions[success_idx]
        assert success_interaction["status"] == "SUCCESS"
        assert success_interaction["generation"]["mode"] == mode
        assert success_interaction["phase"]["name"] in ("explicit", "coverage", "generate")
        assert len(success_interaction["checks"]) == 1
        assert success_interaction["checks"][0] == {
            "name": "not_a_server_error",
            "status": "SUCCESS",
            "message": None,
        }
    else:
        # In other modes, just verify first interaction has expected properties
        first_interaction = interactions[0]
        assert first_interaction["status"] == "SUCCESS"
        if mode == "all":
            assert first_interaction["generation"]["mode"] in ["positive", "negative"]
        else:
            assert first_interaction["generation"]["mode"] == mode
        assert first_interaction["phase"]["name"] in ("explicit", "coverage", "generate")
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
    cli.run_and_assert(
        schema_url,
        f"--report-{format}-path={cassette_path}",
        "--max-examples=1",
        "--request-timeout=0.001",
        "--seed=1",
        "--mode=positive",
        exit_code=ExitCode.TESTS_FAILED,
    )
    if format == "vcr":
        cassette = load_cassette(cassette_path)
        assert cassette["http_interactions"][0]["status"] == "ERROR"
        assert cassette["seed"] == 1
        assert cassette["http_interactions"][0]["response"] is None
    else:
        with cassette_path.open(encoding="utf-8") as fd:
            data = json.load(fd)
            assert len(data["log"]["entries"]) == 3
            assert data["log"]["entries"][1]["response"]["bodySize"] == -1


@pytest.mark.operations("flaky")
def test_interaction_status(cli, openapi3_schema_url, hypothesis_max_examples, cassette_path):
    # See GH-695
    # When an API operation has responses with SUCCESS and FAILURE statuses
    cli.run_and_assert(
        openapi3_schema_url,
        f"--report-vcr-path={cassette_path}",
        f"--max-examples={hypothesis_max_examples or 5}",
        "--seed=1",
        exit_code=ExitCode.TESTS_FAILED,
    )
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
    result = cli.run_and_assert(
        str(schema_path),
        f"--url={openapi3_base_url}",
        f"--max-examples={hypothesis_max_examples or 1}",
        f"--report-vcr-path={cassette_path}",
        "--checks=not_a_server_error",
        "--mode=positive",
    )
    # And there should be no signs of encoding errors
    assert "UnicodeEncodeError" not in result.stdout
    # And the cassette should be correctly recorded
    cassette = load_cassette(cassette_path)
    assert len(cassette["http_interactions"]) == 2
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
    assert len(cassette["http_interactions"]) == 8
    command = f"st run --report-vcr-path={cassette_path} --max-examples={hypothesis_max_examples or 2} {schema_url}"
    assert cassette["command"] == command


@pytest.mark.operations("__all__")
@pytest.mark.parametrize("value", ["true", "false"])
@pytest.mark.parametrize("args", [(), ("--report-preserve-bytes",)], ids=("plain", "base64"))
def test_har_format(cli, schema_url, cassette_path, hypothesis_max_examples, args, value):
    cassette_path = cassette_path.with_suffix(".har")
    auth = "secret"
    result = cli.run_and_assert(
        schema_url,
        f"--report-har-path={cassette_path}",
        f"--max-examples={hypothesis_max_examples or 1}",
        "--seed=1",
        "--checks=all",
        f"-H Authorization: {auth}",
        f"--output-sanitize={value}",
        *args,
        exit_code=ExitCode.TESTS_FAILED,
    )
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
    cli.run_and_assert(
        openapi2_schema_url,
        f"--report-vcr-path={cassette_path}",
        f"--max-examples={hypothesis_max_examples or 5}",
        "--seed=1",
        f"-H Authorization: {auth}",
        f"--output-sanitize={value}",
        "--checks=not_a_server_error",
        "--mode=positive",
    )
    cassette = load_cassette(cassette_path)

    if value == "true":
        expected = "[Filtered]"
    else:
        expected = ANY
    interactions = cassette["http_interactions"]
    assert all(entry["request"]["headers"].get("X-Token") == [expected] for entry in interactions)
    assert all(entry["request"]["headers"].get("Authorization") == [expected] for entry in interactions)
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


@pytest.mark.parametrize("in_config", [True, False])
@pytest.mark.openapi_version("3.0")
def test_report_dir(cli, schema_url, tmp_path, in_config):
    # When report directory is specified with a report format
    report_dir = tmp_path / "reports"
    args = [
        "--max-examples=1",
    ]
    kwargs = {}
    if in_config:
        kwargs["config"] = {"reports": {"junit": {"enabled": True}, "directory": str(report_dir)}}
    else:
        args = ["--report=junit", f"--report-dir={report_dir}", *args]
    cli.run(schema_url, *args, **kwargs)
    # And the report should be created in the specified directory
    assert report_dir.exists()
    assert list(report_dir.glob("*.xml"))

    # When multiple report formats are specified
    args = [
        "--max-examples=1",
    ]
    kwargs = {}
    if in_config:
        kwargs["config"] = {
            "reports": {"vcr": {"enabled": True}, "har": {"enabled": True}, "directory": str(report_dir)}
        }
    else:
        args = [f"--report-dir={report_dir}", "--report=vcr,har", *args]
    cli.run(schema_url, *args, **kwargs)
    # Then all reports should be created in the specified directory
    assert list(report_dir.glob("*.yaml"))
    assert list(report_dir.glob("*.json"))


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
