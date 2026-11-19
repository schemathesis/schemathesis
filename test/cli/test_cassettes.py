import base64
import io
import json
import platform
import sys
import threading
from pathlib import Path
from unittest.mock import ANY
from urllib.parse import urlsplit

import flask
import harfile
import pytest
import yaml
from _pytest.main import ExitCode
from hypothesis import example, given
from hypothesis import strategies as st

from schemathesis.generation import GenerationMode
from schemathesis.reporting.har import _cookie_to_har
from schemathesis.reporting.vcr import write_double_quoted
from test.apps.catalog.openapi import basic
from test.apps.catalog.openapi.modifiers.stateful import SlowOperations
from test.utils import crash_cache_dir, load_json_or_fail, load_ndjson, load_yaml_or_fail


def load_cassette(path):
    return load_yaml_or_fail(path)


def load_response_body(cassette, idx):
    body = cassette["http_interactions"][idx]["response"]["body"]
    if "base64_string" in body:
        return base64.b64decode(body["base64_string"]).decode()
    return body["string"]


@pytest.mark.parametrize("mode", [m.value for m in list(GenerationMode)] + ["all"])
@pytest.mark.parametrize("args", [(), ("--report-preserve-bytes",)], ids=("plain", "base64"))
def test_store_cassette(ctx, cli, cassette_path, hypothesis_max_examples, args, mode):
    api = ctx.openapi.serve(basic.success_and_upload_file())
    hypothesis_max_examples = hypothesis_max_examples or 2
    cli.run_and_assert(
        api.schema_url,
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

    assert all("checks" in interaction for interaction in interactions)
    for interaction in interactions:
        assert len(interaction["checks"]) >= 1
        assert float(interaction["response"]["elapsed"]) >= 0

    if mode == "positive":
        # Operation order may vary.
        success_idx = None
        for idx in range(len(interactions)):
            body = load_response_body(cassette, idx)
            normalized = body.replace(" ", "").replace("\n", "")
            if '{"success":true}' in normalized:
                success_idx = idx
                break
        assert success_idx is not None, "Could not find /success interaction in positive mode"
        success_interaction = interactions[success_idx]
        assert success_interaction["status"] == "SUCCESS"
        assert success_interaction["generation"]["mode"] == mode
        assert success_interaction["phase"]["name"] in ("examples", "coverage", "fuzzing")
        assert len(success_interaction["checks"]) == 1
        assert success_interaction["checks"][0] == {
            "name": "not_a_server_error",
            "status": "SUCCESS",
            "message": None,
        }
    else:
        first_interaction = interactions[0]
        assert first_interaction["status"] == "SUCCESS"
        if mode == "all":
            assert first_interaction["generation"]["mode"] in ["positive", "negative"]
        else:
            assert first_interaction["generation"]["mode"] == mode
        assert first_interaction["phase"]["name"] in ("explicit", "coverage", "generate")
    for interaction in interactions:
        if (
            interaction["phase"]["name"] == "coverage"
            and interaction["generation"]["mode"] == "negative"
            and not interaction["phase"]["data"]["description"].startswith("Unspecified")
            and interaction["phase"]["data"]["description"] != "Missing request body"
        ):
            assert interaction["phase"]["data"]["location"] is not None
            assert interaction["phase"]["data"]["parameter"] is not None
            assert interaction["phase"]["data"]["parameter_location"] is not None


@pytest.mark.parametrize("format", ["vcr", "har", "ndjson"])
def test_store_timeout(ctx, cli, cassette_path, format):
    api = ctx.openapi.apps.slow()
    cli.run_and_assert(
        api.schema_url,
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
    elif format == "har":
        data = load_json_or_fail(cassette_path)
        assert len(data["log"]["entries"]) == 3
        assert data["log"]["entries"][1]["response"]["bodySize"] == -1
    else:
        events = load_ndjson(cassette_path)
        assert "Initialize" in events[0]
        assert events[0]["Initialize"]["seed"] == 1


def test_interaction_status(ctx, cli, hypothesis_max_examples, cassette_path):
    # See GH-695
    api = ctx.openapi.apps.flaky()
    cli.run_and_assert(
        api.schema_url,
        f"--report-vcr-path={cassette_path}",
        f"--max-examples={hypothesis_max_examples or 5}",
        "--seed=1",
        exit_code=ExitCode.TESTS_FAILED,
    )
    cassette = load_cassette(cassette_path)
    assert len(cassette["http_interactions"]) >= 1
    # Each interaction keeps its own status, not the overall test status.
    assert cassette["http_interactions"][0]["status"] == "FAILURE"
    assert "Internal Server Error" in load_response_body(cassette, 0)


def test_bad_yaml_headers(ctx, cli, cassette_path, hypothesis_max_examples):
    # See GH-708
    # A non-ASCII header value that CP1251 cannot encode must still reach the cassette.
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
    api = ctx.openapi.apps.success()
    result = cli.run_and_assert(
        str(schema_path),
        f"--url={api.base_url}/api",
        f"--max-examples={hypothesis_max_examples or 1}",
        f"--report-vcr-path={cassette_path}",
        "--checks=not_a_server_error",
        "--mode=positive",
    )
    assert "UnicodeEncodeError" not in result.stdout
    cassette = load_cassette(cassette_path)
    assert len(cassette["http_interactions"]) == 2
    assert cassette["http_interactions"][0]["request"]["headers"][header_name] == [fixed_header]


def test_cassette_uri_with_single_quote(ctx, cli, cassette_path):
    app, _ = ctx.openapi.make_flask_app({"/api/o'brien": {"get": {"responses": {"200": {"description": "OK"}}}}})

    @app.route("/api/o'brien")
    def obrien():
        return flask.jsonify({})

    cli.run_openapi_app(
        app, f"--report-vcr-path={cassette_path}", "--max-examples=1", "--phases=fuzzing", "--mode=positive"
    )
    assert [
        interaction["request"]["uri"].split("/", 3)[3]
        for interaction in load_cassette(cassette_path)["http_interactions"]
    ] == ["api/o'brien"]


@pytest.mark.skipif(platform.system() == "Windows", reason="Simpler to setup on Linux")
def test_run_subprocess(ctx, testdir, hypothesis_max_examples, snapshot_cli):
    api = ctx.openapi.apps.success()
    cassette_path = Path(str(testdir.tmpdir)) / "output.yaml"
    result = testdir.run(
        str(Path(sys.executable).with_name("schemathesis")),
        "run",
        f"--report-vcr-path={cassette_path}",
        f"--max-examples={hypothesis_max_examples or 2}",
        api.schema_url,
    )
    assert result == snapshot_cli
    cassette = load_cassette(cassette_path)
    assert len(cassette["http_interactions"]) == 10
    command = f"st run --report-vcr-path={cassette_path} --max-examples={hypothesis_max_examples or 2} {api.schema_url}"
    assert cassette["command"] == command


@pytest.mark.parametrize("value", ["true", "false"])
@pytest.mark.parametrize("args", [(), ("--report-preserve-bytes",)], ids=("plain", "base64"))
def test_har_format(ctx, cli, cassette_path, hypothesis_max_examples, args, value):
    api = ctx.openapi.apps.success_and_failure()
    cassette_path = cassette_path.with_suffix(".har")
    auth = "secret"
    result = cli.run_and_assert(
        api.schema_url,
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
    data = load_json_or_fail(cassette_path)
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


LARGE_RESPONSE_BODY = json.dumps({"padding": "x" * 2000}).encode()


def test_cassettes_mark_truncated_response_body(ctx, cli, cassette_path):
    app, _ = ctx.openapi.make_flask_app({"/api/large": {"get": {"responses": {"200": {"description": "OK"}}}}})

    @app.route("/api/large")
    def large():
        return flask.Response(LARGE_RESPONSE_BODY, content_type="application/json")

    har_path = cassette_path.with_suffix(".har")
    cli.run_openapi_app(
        app,
        f"--report-har-path={har_path}",
        f"--report-vcr-path={cassette_path}",
        "--max-examples=1",
        "--phases=fuzzing",
        "--mode=positive",
        config={"output": {"truncation": {"max-recorded-payload-size": 64}}},
    )
    assert [
        (entry["response"]["content"]["size"], entry["response"]["content"]["text"], entry["response"]["bodySize"])
        for entry in load_json_or_fail(har_path)["log"]["entries"]
    ] == [(len(LARGE_RESPONSE_BODY), LARGE_RESPONSE_BODY[:64].decode(), len(LARGE_RESPONSE_BODY))]
    assert [
        (load_response_body(load_cassette(cassette_path), index), interaction["response"]["truncated_from"])
        for index, interaction in enumerate(load_cassette(cassette_path)["http_interactions"])
    ] == [(LARGE_RESPONSE_BODY[:64].decode(), len(LARGE_RESPONSE_BODY))]


def test_har_cookies(ctx, cli, cassette_path):
    app, _ = ctx.openapi.make_flask_app({"/api/cookies": {"get": {"responses": {"200": {"description": "OK"}}}}})

    @app.route("/api/cookies")
    def cookies():
        response = flask.jsonify({})
        response.set_cookie("session", "abc", path="/")
        return response

    har_path = cassette_path.with_suffix(".har")
    cli.run_openapi_app(
        app,
        f"--report-har-path={har_path}",
        "-H",
        "Cookie: theme=dark",
        "--max-examples=1",
        "--phases=fuzzing",
        "--mode=positive",
        "--output-sanitize=false",
    )
    assert [
        (entry["request"]["cookies"], entry["response"]["cookies"])
        for entry in load_json_or_fail(har_path)["log"]["entries"]
    ] == [([{"name": "theme", "value": "dark"}], [{"name": "session", "value": "abc", "path": "/"}])]


# HAR headers and crash files keep only the first value of a repeated header.
@pytest.mark.snapshot(replace_reproduce_with=False)
def test_reports_sanitize_cookies_and_api_keys_by_name(ctx, cli, tmp_path, snapshot_cli):
    app, _ = ctx.openapi.make_flask_app(
        {
            "/items": {
                "get": {
                    "parameters": [
                        {"name": "sessionid", "in": "cookie", "required": True, "schema": {"type": "string"}},
                        {"name": "lang", "in": "cookie", "required": True, "schema": {"type": "string"}},
                    ],
                    "security": [{"JwtCookie": [], "AccessHeader": [], "QueryKey": []}],
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
        components={
            "securitySchemes": {
                "JwtCookie": {"type": "apiKey", "in": "cookie", "name": "jwt"},
                "AccessHeader": {"type": "apiKey", "in": "header", "name": "X-Access"},
                "QueryKey": {"type": "apiKey", "in": "query", "name": "k"},
            }
        },
    )

    @app.route("/items")
    def items():
        response = flask.jsonify({})
        response.status_code = 500
        response.headers.add("Set-Cookie", "sessionid=xyz; Path=/; HttpOnly")
        response.headers.add("Set-Cookie", "lang=en; Path=/")
        response.headers.add("Set-Cookie", "jwt=issued; Path=/")
        return response

    assert (
        cli.run_openapi_app(
            app,
            "--phases=fuzzing",
            "--max-examples=1",
            "--checks=not_a_server_error",
            f"--report-har-path={tmp_path / 'report.har'}",
            f"--report-vcr-path={tmp_path / 'report.yaml'}",
            f"--report-ndjson-path={tmp_path / 'report.ndjson'}",
            config={
                "parameters": {"sessionid": "abc", "lang": "en"},
                "auth": {
                    "openapi": {
                        "JwtCookie": {"api_key": "jwt-secret"},
                        "AccessHeader": {"api_key": "header-secret"},
                        "QueryKey": {"api_key": "query-secret"},
                    }
                },
            },
        )
        == snapshot_cli
    )
    har = load_json_or_fail(tmp_path / "report.har")["log"]["entries"][0]
    vcr = load_cassette(tmp_path / "report.yaml")["http_interactions"][0]
    ndjson = next(
        interaction
        for event in load_ndjson(tmp_path / "report.ndjson")
        if "ScenarioFinished" in event
        for interaction in event["ScenarioFinished"]["recorder"]["interactions"].values()
    )
    [crash_file] = [path for path in crash_cache_dir(tmp_path).rglob("*.json") if path.name != "manifest.json"]
    [crash] = load_json_or_fail(crash_file)["sequence"]
    cookie = "sessionid=[Filtered]; lang=en; jwt=[Filtered]"
    set_cookie = ["sessionid=[Filtered]; Path=/; HttpOnly", "lang=en; Path=/", "jwt=[Filtered]; Path=/"]
    query = "k=%5BFiltered%5D"
    assert {
        "har": (
            [header["value"] for header in har["request"]["headers"] if header["name"] in ("Cookie", "X-Access")],
            har["request"]["cookies"],
            urlsplit(har["request"]["url"]).query,
            [header["value"] for header in har["response"]["headers"] if header["name"] == "set-cookie"],
            har["response"]["cookies"],
        ),
        "vcr": (
            vcr["request"]["headers"]["Cookie"],
            vcr["request"]["headers"]["X-Access"],
            urlsplit(vcr["request"]["uri"]).query,
            vcr["response"]["headers"]["set-cookie"],
        ),
        "ndjson": (
            ndjson["request"]["headers"]["Cookie"],
            ndjson["request"]["headers"]["X-Access"],
            urlsplit(ndjson["request"]["uri"]).query,
            ndjson["response"]["headers"]["set-cookie"],
        ),
        "crash": (
            crash["headers"]["Cookie"],
            crash["headers"]["X-Access"],
            urlsplit(crash["url"]).query,
            crash["response"]["headers"]["set-cookie"],
        ),
    } == {
        "har": (
            ["[Filtered]", cookie],
            [
                {"name": "sessionid", "value": "[Filtered]"},
                {"name": "lang", "value": "en"},
                {"name": "jwt", "value": "[Filtered]"},
            ],
            query,
            set_cookie[:1],
            [
                {"name": "sessionid", "value": "[Filtered]", "path": "/", "httpOnly": True},
                {"name": "lang", "value": "en", "path": "/"},
                {"name": "jwt", "value": "[Filtered]", "path": "/"},
            ],
        ),
        "vcr": ([cookie], ["[Filtered]"], query, set_cookie),
        "ndjson": ([cookie], ["[Filtered]"], query, set_cookie),
        "crash": (cookie, "[Filtered]", query, set_cookie[0]),
    }


def test_har_response_mime_type_and_redirect_url(ctx, cli, cassette_path):
    app, _ = ctx.openapi.make_flask_app({"/api/items": {"post": {"responses": {"201": {"description": "Created"}}}}})

    @app.route("/api/items", methods=["POST"])
    def create():
        return flask.jsonify({}), 201, {"Location": "/api/items/1"}

    har_path = cassette_path.with_suffix(".har")
    cli.run_openapi_app(
        app,
        f"--report-har-path={har_path}",
        "--max-examples=1",
        "--phases=fuzzing",
        "--mode=positive",
    )
    assert [
        (entry["response"]["content"]["mimeType"], entry["response"]["redirectURL"])
        for entry in load_json_or_fail(har_path)["log"]["entries"]
    ] == [("application/json", "/api/items/1")]


@pytest.mark.parametrize(
    ("charset", "body", "text"),
    [("latin-1", '"\xe9"'.encode("latin-1"), '"\xe9"'), ("bogus-xyz", b'"\xc3\xa9"', '"\xe9"')],
    ids=["latin-1", "unknown-charset"],
)
def test_response_body_charset_in_reports(ctx, cli, cassette_path, charset, body, text):
    app, _ = ctx.openapi.make_flask_app({"/api/text": {"get": {"responses": {"200": {"description": "OK"}}}}})

    @app.route("/api/text")
    def text_body():
        return flask.Response(body, content_type=f"application/json; charset={charset}")

    har_path = cassette_path.with_suffix(".har")
    cli.run_openapi_app(
        app,
        f"--report-har-path={har_path}",
        f"--report-vcr-path={cassette_path}",
        "--max-examples=1",
        "--phases=fuzzing",
        "--mode=positive",
    )
    assert [entry["response"]["content"]["text"] for entry in load_json_or_fail(har_path)["log"]["entries"]] == [text]
    assert [interaction["response"]["body"] for interaction in load_cassette(cassette_path)["http_interactions"]] == [
        {"encoding": charset, "string": text}
    ]


def test_har_url_with_credentials_is_sanitized(ctx, cli, cassette_path):
    api = ctx.openapi.apps.success()
    har_path = cassette_path.with_suffix(".har")
    host = api.base_url.removeprefix("http://")

    cli.run(
        api.schema_url,
        f"--url=http://user:pw@{host}",
        f"--report-har-path={har_path}",
        "--max-examples=1",
        "--phases=fuzzing",
    )

    assert [entry["request"]["url"] for entry in load_json_or_fail(har_path)["log"]["entries"]] == [
        f"http://[Filtered]@{host}/api/success"
    ]


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
        ("A=a; $=b", []),
    ],
)
def test_cookie_to_har(value, expected):
    assert list(_cookie_to_har(value)) == expected


@pytest.mark.parametrize("value", ["true", "false"])
def test_output_sanitization(ctx, cli, hypothesis_max_examples, cassette_path, value):
    api = ctx.openapi.apps.headers()
    auth = "secret-auth"
    cli.run_and_assert(
        api.schema_url,
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


def test_forbid_preserve_bytes_without_cassette_path(ctx, cli, snapshot_cli):
    api = ctx.openapi.apps.success()
    assert cli.run(api.schema_url, "--report-preserve-bytes") == snapshot_cli


@pytest.mark.parametrize("in_config", [True, False])
def test_report_dir(ctx, cli, tmp_path, in_config):
    api = ctx.openapi.apps.success()
    report_dir = tmp_path / "reports"
    args = [
        "--max-examples=1",
    ]
    kwargs = {}
    if in_config:
        kwargs["config"] = {"reports": {"junit": {"enabled": True}, "directory": str(report_dir)}}
    else:
        args = ["--report=junit", f"--report-dir={report_dir}", *args]
    cli.run(api.schema_url, *args, **kwargs)
    assert report_dir.exists()
    assert list(report_dir.glob("*.xml"))

    args = [
        "--max-examples=1",
    ]
    kwargs = {}
    if in_config:
        kwargs["config"] = {
            "reports": {
                "vcr": {"enabled": True},
                "har": {"enabled": True},
                "ndjson": {"enabled": True},
                "directory": str(report_dir),
            }
        }
    else:
        args = [f"--report-dir={report_dir}", "--report=vcr,har,ndjson", *args]
    cli.run(api.schema_url, *args, **kwargs)
    assert list(report_dir.glob("*.yaml"))
    assert list(report_dir.glob("*.json"))
    assert list(report_dir.glob("*.ndjson"))


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


def test_rejected_report_path_leaves_no_writer_threads(cli, tmp_path):
    # A rejected report path must not strand an already-started writer thread and hang the process on exit.
    (tmp_path / "blocker").touch()
    before = set(threading.enumerate())

    result = cli.run(
        "http://127.0.0.1:1/openapi.json",
        "--report-vcr-path=cassette.yaml",
        "--report-har-path=blocker/output.har",
    )

    assert result.exit_code == 2, result.stdout
    assert sorted(thread.name for thread in set(threading.enumerate()) - before) == []


def test_store_stateful_timeout(ctx, cli, tmp_path):
    api = ctx.openapi.apps.stateful_users(SlowOperations({("GET", "/users/<int:user_id>"): 0.5}))
    vcr_path = tmp_path / "output.yaml"
    har_path = tmp_path / "output.har"
    cli.run(
        api.schema_url,
        "--phases=stateful",
        "--max-examples=10",
        "--request-timeout=0.1",
        "--seed=1",
        "-c",
        "not_a_server_error",
        f"--report-vcr-path={vcr_path}",
        f"--report-har-path={har_path}",
    )
    timed_out = [
        interaction["request"]["uri"]
        for interaction in load_cassette(vcr_path)["http_interactions"]
        if interaction["response"] is None
    ]
    assert timed_out
    har_entries = [
        entry for entry in load_json_or_fail(har_path)["log"]["entries"] if entry["response"]["bodySize"] == -1
    ]
    assert [entry["request"]["url"] for entry in har_entries] == timed_out
