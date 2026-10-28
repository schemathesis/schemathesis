import json
import os
import pathlib
import platform
import re
import shutil
import socket
import ssl
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from itertools import count
from urllib.parse import urljoin

import hypothesis
import pytest
import strawberry
import trustme
import yaml
from _pytest.main import ExitCode
from flask import Flask, Response, jsonify, redirect, request, url_for
from hypothesis.internal.observability import with_observability_callback

import schemathesis
from schemathesis.cli.commands.run.context import ExecutionContext
from schemathesis.config import SchemathesisConfig
from schemathesis.config._output import MAX_PAYLOAD_SIZE
from schemathesis.core.hooks import HOOKS_MODULE_ENV_VAR
from schemathesis.core.shell import ShellType
from schemathesis.engine import Status, control, events
from schemathesis.engine.run import Phase, PhaseName
from schemathesis.specs.openapi import unregister_string_format
from test.apps.catalog.graphql import bookstore as graphql_bookstore
from test.apps.catalog.openapi import basic as openapi_basic
from test.apps.fragments import handlers
from test.apps.fragments.schemas import PAYLOAD_SCHEMA, linked_users
from test.fixtures.app_runner import accept_connections, wait_for_port
from test.utils import HERE, SIMPLE_PATH, refusing_url


def test_commands_help(cli, snapshot_cli):
    assert cli.main() == snapshot_cli


def test_run_subprocess(testdir):
    # To verify that CLI entry point is installed properly
    result = testdir.run(str(pathlib.Path(sys.executable).with_name("schemathesis")))
    assert result.ret == ExitCode.INTERRUPTED


@pytest.mark.skipif(platform.system() == "Windows", reason="Requires extra setup on Windows")
def test_run_as_module(testdir):
    result = testdir.run(sys.executable, "-m", "schemathesis.cli")
    assert result.ret == ExitCode.INTERRUPTED


@pytest.mark.parametrize(
    "args",
    [
        (),
        (SIMPLE_PATH,),
        (SIMPLE_PATH, "--url=test"),
        (SIMPLE_PATH, "--url=127.0.0.1:8080"),
        ("http://127.0.0.1", "--request-timeout=-5"),
        ("http://127.0.0.1", "--request-timeout=0"),
        ("http://127.0.0.1", "--auth=123"),
        ("http://127.0.0.1", "--auth=:pass"),
        ("http://127.0.0.1", "--auth=тест:pass"),
        ("http://127.0.0.1", "--auth=user:тест"),
        ("http://127.0.0.1", "--header=123"),
        ("http://127.0.0.1", "--header=:"),
        ("http://127.0.0.1", "--header= :"),
        ("http://127.0.0.1", "--header=тест:test"),
        ("http://127.0.0.1", "--header=test:тест"),
        ("//test",),
        ("http://127.0.0.1", "--max-response-time=0"),
        ("unknown.json",),
        ("unknown.json", "--url=http://127.0.0.1"),
        ("--help",),
        ("http://127.0.0.1", "--generation-codec=foobar"),
        ("http://127.0.0.1", "--report=unknown"),
    ],
)
def test_run_output(cli, args, snapshot_cli):
    assert cli.run(*args) == snapshot_cli


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_run_rejects_unknown_phase(cli, snapshot_cli):
    assert cli.run("http://127.0.0.1:1/openapi.json", "--phases=examples,foo") == snapshot_cli


def test_hooks_module_not_found(cli, snapshot_cli):
    # When an unknown hook module is passed to CLI
    assert cli.main("run", "http://127.0.0.1:1", hooks="hook") == snapshot_cli
    assert os.getcwd() in sys.path


def test_hooks_with_inner_import_error(ctx, cli, snapshot_cli):
    # When the hook module itself raises an ImportError
    module = ctx.write_pymodule("import something_else")
    assert cli.main("run", "http://127.0.0.1:1", hooks=module) == snapshot_cli


def test_hooks_invalid(ctx, cli):
    # When hooks are passed to the CLI call
    # And its importing causes an exception
    module = ctx.write_pymodule("1 / 0")

    result = cli.main("run", "http://127.0.0.1:1", hooks=module)

    # Then CLI run should fail
    assert result.exit_code == 2, result.stdout
    # And a helpful message should be displayed in the output
    lines = result.stdout.strip().split("\n")
    assert lines[0] == "Unable to load Schemathesis extension hooks"
    assert lines[8] == "ZeroDivisionError: division by zero"


def test_certificate_only_key(cli, tmp_path, snapshot_cli):
    # When cert key is passed without cert itself
    # Then an appropriate error should be displayed
    assert cli.run("http://127.0.0.1", f"--request-cert-key={tmp_path}") == snapshot_cli


@pytest.mark.parametrize("header", ["Authorization", "authorization"])
def test_auth_and_authorization_header_are_disallowed(ctx, cli, header, snapshot_cli):
    # When ``--auth`` is passed together with ``--header`` that sets the ``Authorization`` header
    # Then it causes a validation error
    api = ctx.openapi.apps.success()
    assert cli.run(api.schema_url, "--auth=test:test", f"--header={header}:token123") == snapshot_cli


@pytest.mark.parametrize("workers", [1, 2])
def test_schema_not_available(cli, workers, snapshot_cli):
    # When the given schema is unreachable
    # Then the whole Schemathesis run should fail
    # And error message is displayed
    assert cli.run("http://127.0.0.1:1/schema.yaml", f"--workers={workers}") == snapshot_cli


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_workers_out_of_range_lists_compact_choices(cli, snapshot_cli):
    assert cli.run("http://127.0.0.1:1/schema.yaml", "--workers=999") == snapshot_cli


@pytest.mark.snapshot_suffix(platform.python_implementation().lower())
def test_empty_schema_file(testdir, cli, snapshot_cli):
    # When the schema file is empty
    filename = testdir.makefile(".json", schema="")
    # Then a proper error should be reported
    assert cli.run(str(filename), "--url=http://127.0.0.1:1") == snapshot_cli


def _malformed_schema_file(tmp_path):
    path = tmp_path / "schema.json"
    path.write_text("{broken")
    return [str(path), "--url=http://127.0.0.1:1"]


def _unreachable_schema(tmp_path):
    return ["http://127.0.0.1:1/openapi.json"]


def _wait_for_unreachable_schema(tmp_path):
    return ["http://127.0.0.1:1/openapi.json", "--wait-for-schema=1"]


@pytest.mark.parametrize(
    ("command", "make_args"),
    [
        pytest.param("run", _malformed_schema_file, id="malformed-schema-run"),
        pytest.param("fuzz", _malformed_schema_file, id="malformed-schema-fuzz"),
        pytest.param("run", _unreachable_schema, id="unreachable-schema-run"),
        pytest.param("fuzz", _unreachable_schema, id="unreachable-schema-fuzz"),
        pytest.param("fuzz", _wait_for_unreachable_schema, id="wait-for-schema-timeout-fuzz"),
    ],
)
def test_fatal_error_exit_code(cli, tmp_path, command, make_args):
    result = cli.main(command, *make_args(tmp_path))
    assert result.exit_code == 2, result.stdout


@pytest.mark.parametrize("command", ["run", "fuzz"])
def test_config_file_error_exit_code(cli, command):
    result = cli.main("--config-file=unknown-file.toml", command, "http://127.0.0.1:1/openapi.json")
    assert result.exit_code == 2, result.stdout


@pytest.mark.parametrize(
    "args",
    [["run", "--max-examples=1"], ["fuzz", "--max-time=1"]],
    ids=["run", "fuzz"],
)
def test_piped_output_has_no_blank_line_runs_or_trailing_whitespace(ctx, cli, args):
    api = ctx.openapi.apps.success_and_failure()
    command, *options = args
    stdout = cli.main(command, api.schema_url, *options).stdout
    lines = [line.strip() for line in stdout.splitlines()]
    assert not any(not first and not second for first, second in zip(lines, lines[1:], strict=False)), stdout
    assert [line for line in stdout.splitlines() if line != line.rstrip()] == [], stdout


@pytest.mark.snapshot(replace_reproduce_with=True)
@pytest.mark.parametrize("args", [["run"], ["fuzz", "--max-time=1"]], ids=["run", "fuzz"])
def test_runtime_config_error_exit_code(ctx, cli, snapshot_cli, args):
    api = ctx.openapi.apps.success()
    command, *options = args
    result = cli.main(command, api.schema_url, *options, config={"checks": {"no_such_check": {"threshold": 1}}})
    assert result.exit_code == 2, result.stdout
    assert result == snapshot_cli


def test_force_color_nocolor(ctx, cli, snapshot_cli):
    api = ctx.openapi.apps.success()
    assert cli.run(api.schema_url, "--force-color", "--no-color") == snapshot_cli


def test_certificates(cli, app_runner, tmp_path):
    ca = trustme.CA()
    ca_path = tmp_path / "ca.pem"
    ca.cert_pem.write_to_path(str(ca_path))
    client_cert = ca.issue_cert("test.org")
    client_cert_path = tmp_path / "client.pem"
    client_cert.private_key_and_cert_chain_pem.write_to_path(str(client_cert_path))
    app = openapi_basic.success().server
    presented = {}

    @app.before_request
    def record_client_certificate():
        presented.setdefault(request.path, set()).add(ssl.PEM_cert_to_DER_cert(request.environ["SSL_CLIENT_CERT"]))

    port = app_runner.run_client_cert_flask_app(app, ca)
    cli.run_and_assert(
        f"https://127.0.0.1:{port}/openapi.json",
        f"--request-cert={client_cert_path}",
        f"--tls-verify={ca_path}",
    )
    expected = {ssl.PEM_cert_to_DER_cert(client_cert.cert_chain_pems[0].bytes().decode())}
    assert presented["/openapi.json"] == presented["/api/success"] == expected


def test_hypothesis_database_with_derandomize(ctx, cli, snapshot_cli):
    api = ctx.openapi.apps.success()
    assert cli.run(api.schema_url, "--generation-database=:memory:", "--generation-deterministic") == snapshot_cli


def collect_fuzzed_query_values(ctx, cli, *args):
    app, _ = ctx.openapi.make_flask_app(
        {
            "/items": {
                "get": {
                    "parameters": [{"name": "id", "in": "query", "required": True, "schema": {"type": "integer"}}],
                    "responses": {"200": {"description": "OK"}},
                }
            }
        }
    )
    received = []

    @app.route("/items")
    def items():
        received.append(request.args.get("id"))
        return jsonify({})

    cli.run_openapi_app(app, "--phases=fuzzing", "--max-examples=10", *args)
    return received


def test_explicit_seed_repeats_requests(ctx, cli):
    assert collect_fuzzed_query_values(ctx, cli, "--seed=1") == collect_fuzzed_query_values(ctx, cli, "--seed=1")


def test_explicit_seed_takes_precedence_over_deterministic_mode(ctx, cli):
    assert collect_fuzzed_query_values(ctx, cli, "--seed=1", "--generation-deterministic") != (
        collect_fuzzed_query_values(ctx, cli, "--seed=2", "--generation-deterministic")
    )


def test_hypothesis_parameters(ctx, cli):
    # When Hypothesis options are passed via command line
    api = ctx.openapi.apps.success()
    cli.run_and_assert(
        api.schema_url,
        "--generation-deterministic",
        "--max-examples=10",
        "--suppress-health-check=all",
        "--checks=not_a_server_error",
    )
    # Then they should be correctly converted into arguments accepted by `hypothesis.settings`
    # Parameters are validated in `hypothesis.settings`


@pytest.mark.usefixtures("hypothesis_ci_profile")
@pytest.mark.parametrize("args", [("--suppress-health-check=all",), ()], ids=["suppressed", "not-suppressed"])
@pytest.mark.snapshot(replace_reproduce_with=True)
def test_suppress_health_check_under_ci_profile(ctx, cli, snapshot_cli, args):
    app, _ = ctx.openapi.make_flask_app(
        {
            "/items": {
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "array",
                                    "minItems": 40,
                                    "items": {
                                        "type": "array",
                                        "minItems": 20,
                                        "items": {"type": "string", "pattern": "^[a-f]{20}$"},
                                    },
                                }
                            }
                        },
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            }
        }
    )

    @app.route("/items", methods=["POST"])
    def items():
        return jsonify({})

    assert (
        cli.run_openapi_app(app, "--max-examples=2", "--phases=fuzzing", "--checks=not_a_server_error", *args)
        == snapshot_cli
    )


@pytest.fixture
def settings_recorder(ctx):
    return ctx.write_pymodule(
        """
import hypothesis

@schemathesis.hook
def before_call(context, case, kwargs):
    current = hypothesis.settings.default
    checks = sorted(check.name for check in current.suppress_health_check)
    note(f"SETTINGS {current.derandomize} {current.print_blob} {checks}")
"""
    )


ALL_HEALTH_CHECKS = sorted(check.name for check in hypothesis.HealthCheck)


@pytest.mark.usefixtures("hypothesis_ci_profile")
@pytest.mark.parametrize("deterministic", [False, True])
@pytest.mark.parametrize(
    ("command", "app", "suppressed"),
    [
        (("run", "--phases=fuzzing", "--max-examples=2"), "success", []),
        (("run", "--phases=stateful", "--max-examples=2"), "stateful_users", ALL_HEALTH_CHECKS),
        (("fuzz", "--max-time=1"), "success", []),
    ],
    ids=["run-fuzzing", "run-stateful", "fuzz"],
)
def test_cli_ignores_hypothesis_ci_profile(ctx, cli, settings_recorder, command, app, suppressed, deterministic):
    api = getattr(ctx.openapi.apps, app)()
    result = cli.main(
        command[0],
        api.schema_url,
        *command[1:],
        config={"generation": {"deterministic": deterministic}},
        hooks=settings_recorder,
    )
    assert {line for line in result.stdout.splitlines() if line.startswith("SETTINGS ")} == {
        f"SETTINGS {deterministic} False {suppressed}"
    }


@pytest.mark.skipif(platform.system() == "Windows", reason="Simpler to setup on Linux")
def test_cli_ignores_hypothesis_ci_profile_detected_from_environment(ctx, testdir, settings_recorder):
    api = ctx.openapi.apps.success()
    result = subprocess.run(
        [
            str(pathlib.Path(sys.executable).with_name("st")),
            "run",
            api.schema_url,
            "--phases=fuzzing",
            "--max-examples=2",
        ],
        capture_output=True,
        text=True,
        check=False,
        cwd=str(testdir.tmpdir),
        env={**os.environ, "CI": "true", HOOKS_MODULE_ENV_VAR: settings_recorder, "PYTHONPATH": str(testdir.tmpdir)},
    )
    assert {line for line in result.stdout.splitlines() if line.startswith("SETTINGS ")} == {"SETTINGS False False []"}


@pytest.mark.parametrize("workers", [1, 2])
def test_cli_run_only_failure(ctx, cli, workers, snapshot_cli):
    api = ctx.openapi.apps.failure()
    assert cli.run(api.schema_url, f"--workers={workers}", "-c not_a_server_error") == snapshot_cli


def test_cli_binary_body(ctx, cli, hypothesis_max_examples):
    api = ctx.openapi.apps.upload_file()
    result = cli.run_and_assert(
        api.schema_url,
        "--suppress-health-check=filter_too_much",
        "--mode=positive",
        f"--max-examples={hypothesis_max_examples or 1}",
    )
    assert " HYPOTHESIS OUTPUT " not in result.stdout


def test_openapi_auth_skips_malformed_security_requirements(cli, ctx):
    api = ctx.openapi.apps.ignored_auth()
    schema_path = ctx.openapi.write_schema(
        {
            "/ignored_auth": {
                "get": {
                    "security": [
                        None,
                        {"ApiKeyAuth": []},
                    ],
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
        components={
            "securitySchemes": {
                "ApiKeyAuth": {
                    "type": "apiKey",
                    "name": "X-API-Key",
                    "in": "header",
                }
            }
        },
    )

    result = cli.run(
        str(schema_path),
        f"--url={api.base_url}/api",
        "--max-examples=1",
        "--checks=not_a_server_error",
        config={"auth": {"openapi": {"ApiKeyAuth": {"api_key": "secret"}}}},
    )

    assert result.exit_code == ExitCode.OK, result.stdout
    # The coverage phase also sends one request without credentials.
    assert {request.headers.get("X-Api-Key") for request in api.requests} == {None, "secret"}


@pytest.mark.parametrize("workers", [1, 2])
def test_cli_run_output_empty(ctx, cli, workers):
    api = ctx.openapi.apps.no_operations()
    result = cli.run_and_assert(api.schema_url, f"--workers={workers}", exit_code=ExitCode.INTERRUPTED)
    assert " HYPOTHESIS OUTPUT " not in result.stdout
    assert " SUMMARY " in result.stdout

    lines = result.stdout.strip().split("\n")
    assert "= The schema defines no API operations =" in lines[-1]


def test_workers_beyond_operations_start_no_extra_threads(ctx, cli, tmp_path):
    # Every started thread costs memory, so a huge `workers` value with a small API must not start them all.
    schema_path = tmp_path / "schema.json"
    schema_path.write_text(
        json.dumps(ctx.openapi.build_schema({"/users": {"get": {"responses": {"200": {"description": "OK"}}}}}))
    )
    local = threading.local()

    def run_counting_threads(workers):
        started = []

        def trace(frame, event, arg):
            if not getattr(local, "seen", False):
                local.seen = True
                started.append(threading.current_thread().name)

        threading.settrace(trace)
        try:
            result = cli.main(
                "run", str(schema_path), "--url=http://127.0.0.1:1", "--phases=examples", config={"workers": workers}
            )
        finally:
            threading.settrace(None)
        return result.exit_code, len(started)

    assert run_counting_threads(1000) == run_counting_threads(1)


def test_cli_run_changed_base_url(ctx, cli, snapshot_cli):
    # When the CLI receives custom base URL
    api = ctx.openapi.apps.success_and_failure()
    # Then the base URL should be correctly displayed in the CLI output
    assert cli.run(api.schema_url, "--url", api.base_url, "-c not_a_server_error") == snapshot_cli


@pytest.mark.parametrize(
    ("url", "message"),
    [
        ("/doesnt_exist", "Failed to load schema due to client error (HTTP 404 Not Found)"),
        ("/api/failure", "Failed to load schema due to server error (HTTP 500 Internal Server Error)"),
    ],
)
@pytest.mark.parametrize("workers", [1, 2])
def test_execute_missing_schema(ctx, cli, url, message, workers):
    api = ctx.openapi.apps.failure()
    result = cli.run_and_assert(f"{api.base_url}{url}", f"--workers={workers}", exit_code=2)
    assert message in result.stdout


def test_timeout_reclassified_as_error_on_flaky_replay(ctx, cli):
    # A timed-out request must be reported as a network error, never as a check failure.
    counter = [0]
    counter_lock = threading.Lock()

    app, _ = ctx.openapi.make_flask_app({"/flaky": {"get": {"responses": {"200": {"description": "OK"}}}}})

    @app.route("/flaky")
    def flaky():
        with counter_lock:
            counter[0] += 1
            n = counter[0]
        if n % 2 == 1:
            time.sleep(0.2)
        return jsonify({"ok": True})

    result = cli.run_openapi_app(
        app,
        "--request-timeout=0.08",
        "--workers=1",
        "--phases=fuzzing",
    )
    assert " 1 failed" not in result.stdout, result.stdout
    assert "Network Error" in result.stdout, result.stdout


@pytest.mark.parametrize("workers", [1, 2])
def test_connection_timeout(ctx, cli, workers):
    # Timeout on a slow endpoint surfaces as a Network Error and fails the run.
    api = ctx.openapi.apps.slow()
    with ctx.restore_checks():

        @schemathesis.check
        def noop(ctx, response, case):
            pass

        result = cli.run(
            api.schema_url,
            "--request-timeout=0.05",
            f"--workers={workers}",
            "--phases=fuzzing",
            "--checks=noop",
        )
        assert result.exit_code == 1, result.stdout
        assert "Network Error" in result.stdout, result.stdout
        assert "GET /api/slow" in result.stdout, result.stdout
        assert "Read timed out" in result.stdout, result.stdout


# Only the first request stalls, and only past the default timeout, so a missing timeout fails instead of hanging.
def test_default_request_timeout(ctx, cli):
    app, _ = ctx.openapi.make_flask_app({"/stall": {"get": {"responses": {"200": {"description": "OK"}}}}})
    release = threading.Event()
    calls = count()

    @app.route("/stall")
    def stall():
        if next(calls) == 0:
            release.wait(15)
        return jsonify({})

    try:
        result = cli.run_openapi_app(app, "--max-examples=1", "--phases=fuzzing", "--checks=not_a_server_error")
    finally:
        release.set()
    assert "Read timed out after 10 seconds" in result.stdout, result.stdout


@pytest.mark.snapshot(replace_test_cases=False, replace_reproduce_with=True)
def test_stateful_timeout_absorbed_by_health_monitor(ctx, cli, snapshot_cli):
    app, _ = ctx.openapi.make_flask_app(linked_users())
    release = threading.Event()
    calls = count()
    handlers.register_linked_users_create(app)

    # Too few timeouts to flag the operation as unresponsive
    @app.route("/users/<int:user_id>")
    def get_user(user_id):
        if next(calls) < 3:
            release.wait()
        return jsonify({"id": user_id})

    try:
        result = cli.run_openapi_app(
            app,
            "--max-examples=1",
            "--generation-deterministic",
            "--phases=stateful",
            "--checks=not_a_server_error",
            config={"operations": [{"include-name": "GET /users/{userId}", "request-timeout": 0.05}]},
        )
    finally:
        release.set()
    assert result == snapshot_cli


@pytest.mark.snapshot(replace_test_cases=False, replace_reproduce_with=True)
def test_fuzzing_timeout_outlasting_time_budget(ctx, cli, snapshot_cli):
    # The first request times out on its own; the second one is still waiting when `--max-time` runs out.
    app, _ = ctx.openapi.make_flask_app(
        {
            "/first": {"get": {"responses": {"200": {"description": "OK"}}}},
            "/second": {"get": {"responses": {"200": {"description": "OK"}}}},
        }
    )
    release = threading.Event()

    @app.route("/first")
    def first():
        release.wait()
        return jsonify({})

    @app.route("/second")
    def second():
        release.wait()
        return jsonify({})

    try:
        result = cli.run_openapi_app(
            app,
            "--max-time=3",
            "--request-timeout=2",
            "--phases=fuzzing",
            "--checks=not_a_server_error",
        )
    finally:
        release.set()
    assert result == snapshot_cli


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_max_time_cuts_hanging_request(ctx, cli, snapshot_cli):
    app, _ = ctx.openapi.make_flask_app(
        {
            "/a/hanging": {"get": {"responses": {"200": {"description": "OK"}}}},
            **{
                f"/b/fast{index}": {
                    "get": {
                        "parameters": [{"name": "q", "in": "query", "schema": {"type": "integer"}}],
                        "responses": {"200": {"description": "OK"}},
                    }
                }
                for index in range(3)
            },
        }
    )
    release = threading.Event()

    @app.route("/a/hanging")
    def hanging():
        release.wait()
        return jsonify({})

    @app.route("/b/fast<int:index>")
    def fast(index):
        return jsonify({})

    started = time.monotonic()
    try:
        result = cli.run_openapi_app(
            app,
            "--max-time=2",
            "--request-timeout=30",
            "--phases=examples,coverage,fuzzing",
            "--checks=not_a_server_error",
        )
    finally:
        release.set()
    assert time.monotonic() - started < 15
    assert result == snapshot_cli


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_max_time_reports_unreached_operations(ctx, cli, snapshot_cli):
    app, _ = ctx.openapi.make_flask_app(
        {
            "/a/slow": {"get": {"responses": {"200": {"description": "OK"}}}},
            **{
                f"/b/fast{index}": {
                    "get": {
                        "parameters": [{"name": "q", "in": "query", "schema": {"type": "integer"}}],
                        "responses": {"200": {"description": "OK"}},
                    }
                }
                for index in range(3)
            },
        }
    )

    @app.route("/a/slow")
    def slow():
        time.sleep(2)
        return jsonify({})

    @app.route("/b/fast<int:index>")
    def fast(index):
        return jsonify({})

    assert (
        cli.run_openapi_app(
            app, "--max-time=1", "--phases=examples,coverage,fuzzing", "--checks=not_a_server_error", "--workers=1"
        )
        == snapshot_cli
    )


@pytest.fixture
def raw_http_server(ctx):
    # Serves the schema, then answers every other request with `reply`, for responses no framework would send.
    schema = json.dumps(ctx.openapi.build_schema(SUCCESS_PATHS)).encode()
    listeners = []

    def handle(connection, reply):
        with connection:
            request_line = connection.recv(65536).split(b"\r\n", 1)[0]
            if b" /openapi.json " in request_line:
                connection.sendall(
                    b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: "
                    + str(len(schema)).encode()
                    + b"\r\nConnection: close\r\n\r\n"
                    + schema
                )
            else:
                reply(connection)

    def start(reply):
        listener = socket.create_server(("127.0.0.1", 0))
        listeners.append(listener)
        accept_connections(
            listener,
            lambda connection: threading.Thread(target=handle, args=(connection, reply), daemon=True).start(),
        )
        return f"http://127.0.0.1:{listener.getsockname()[1]}/openapi.json"

    yield start
    for listener in listeners:
        listener.close()


SUCCESS_PATHS = {"/api/success": {"get": {"responses": {"200": {"description": "OK"}}}}}


def test_read_content_timeout(cli, raw_http_server, snapshot_cli):
    def stall_mid_body(connection):
        connection.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: 20\r\n\r\n{")
        time.sleep(1)

    assert cli.run(raw_http_server(stall_mid_body), "--request-timeout=0.1", "--max-examples=1") == snapshot_cli


@pytest.mark.parametrize("workers", [1, 2])
def test_unsatisfiable(ctx, cli, workers, snapshot_cli):
    # When the app's schema contains parameters that can't be generated
    # For example if it contains contradiction in the parameters' definition - requires to be integer AND string at the
    # same time
    # And more clear error message is displayed instead of Hypothesis one
    api = ctx.openapi.apps.unsatisfiable()
    assert cli.run(api.schema_url, "--mode=positive", f"--workers={workers}") == snapshot_cli


def test_invalid_operation(ctx, cli, snapshot_cli):
    # When the app's schema contains errors
    # For example if its type is "int" but should be "integer"
    # And schema validation is disabled
    api = ctx.openapi.apps.invalid()
    assert cli.run(api.schema_url, "--phases=fuzzing", "--mode=positive") == snapshot_cli


def test_invalid_type_with_ref(cli, ctx, snapshot_cli):
    api = ctx.openapi.apps.success()
    schema_path = ctx.openapi.write_schema(
        {
            "/test": {
                "post": {
                    "parameters": [
                        {
                            "in": "header",
                            "name": "h",
                            "schema": {
                                "$ref": "#/components/schemas/S",
                                "type": "invalid",
                            },
                        },
                    ],
                    "responses": {"default": {"description": "Ok"}},
                }
            },
            "/ref-to-non-schema": {
                "get": {
                    "parameters": [{"in": "query", "name": "q", "schema": {"$ref": "#/info/title"}}],
                    "responses": {"default": {"description": "Ok"}},
                }
            },
        },
        # Open API 3.0 ignores keywords next to `$ref`, so only 3.1 reads the invalid `type`.
        version="3.1.0",
        components={"schemas": {"S": {"maxProperties": 5}}},
    )
    assert cli.run(str(schema_path), f"--url={api.base_url}/api", "--phases=fuzzing", "--mode=positive") == snapshot_cli


def test_unsatisfiable_with_ref(cli, ctx, snapshot_cli):
    api = ctx.openapi.apps.success()
    schema_path = ctx.openapi.write_schema(
        {
            "/test": {
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "$ref": "#/components/schemas/ObjectType",
                                }
                            }
                        },
                    },
                    "responses": {"default": {"description": "Ok"}},
                }
            }
        },
        components={
            "schemas": {
                "IntegerType": {"type": "integer", "minimum": 100, "maximum": 1000},
                "StringType": {"type": "string", "pattern": "^[A-Z]{3,10}$"},
                "ObjectType": {
                    "type": "object",
                    "properties": {
                        "nested": {"$ref": "#/components/schemas/NestedSchema"},
                        "another": {
                            "allOf": [
                                {"$ref": "#/components/schemas/IntegerType"},
                                {"$ref": "#/components/schemas/StringType"},
                            ]
                        },
                    },
                    "required": ["nested", "another"],
                },
                "NestedSchema": {"type": "array", "items": {"type": "boolean"}, "minItems": 5},
            }
        },
    )
    assert cli.run(str(schema_path), f"--url={api.base_url}/api", "--phases=fuzzing", "--mode=positive") == snapshot_cli


def test_unsatisfiable_query_parameter(cli, ctx, snapshot_cli):
    api = ctx.openapi.apps.success()
    schema_path = ctx.openapi.write_schema(
        {
            "/test": {
                "get": {
                    "parameters": [
                        {
                            "name": "id",
                            "in": "query",
                            "required": True,
                            "schema": {"type": "integer", "minimum": 100, "maximum": 10},
                        }
                    ],
                    "responses": {"default": {"description": "Ok"}},
                }
            }
        }
    )
    assert cli.run(str(schema_path), f"--url={api.base_url}/api", "--phases=fuzzing", "--mode=positive") == snapshot_cli


def test_unsatisfiable_empty_array_items(cli, ctx, snapshot_cli):
    # Each `oneOf` branch contradicts the sibling `minimum`, so no array item can be generated.
    api = ctx.openapi.apps.success()
    schema_path = ctx.openapi.write_schema(
        {
            "/test": {
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "array",
                                    "minItems": 1,
                                    "items": {
                                        "type": "integer",
                                        "minimum": 10,
                                        "oneOf": [{"maximum": 1}, {"maximum": 2}, {"maximum": 3}],
                                    },
                                }
                            }
                        },
                    },
                    "responses": {"default": {"description": "Ok"}},
                }
            }
        }
    )
    assert cli.run(str(schema_path), f"--url={api.base_url}/api", "--phases=fuzzing", "--mode=positive") == snapshot_cli


@pytest.mark.parametrize("workers", [1, 2])
def test_status_code_conformance(ctx, cli, workers, snapshot_cli):
    # When operation returns a status code, that is not listed in "responses"
    # And "status_code_conformance" is specified
    # Then the whole Schemathesis run should fail
    # And this operation should be marked as failed in the progress line
    api = ctx.openapi.apps.teapot()
    assert cli.run(api.schema_url, "-c", "status_code_conformance", f"--workers={workers}") == snapshot_cli


def test_headers_conformance_valid(ctx, cli):
    api = ctx.openapi.apps.headers()
    result = cli.run_and_assert(api.schema_url, "-c", "response_headers_conformance", "-H", "X-Custom-Header: 42")

    lines = result.stdout.split("\n")
    assert "1. Received a response with missing headers: X-Custom-Header" not in lines


def test_multiple_failures_single_check(ctx, cli, snapshot_cli):
    api = ctx.openapi.apps.multiple_failures()
    assert (
        cli.run(
            api.schema_url,
            "--generation-deterministic",
            "-c not_a_server_error,positive_data_acceptance",
            "--mode=positive",
        )
        == snapshot_cli
    )


def test_continue_on_failure(ctx, cli):
    api = ctx.openapi.apps.multiple_failures()
    result = cli.run_and_assert(api.schema_url, "--continue-on-failure", exit_code=ExitCode.TESTS_FAILED)
    assert "118 generated" in result.stdout


def test_multiple_failures_different_check(ctx, cli, snapshot_cli):
    api = ctx.openapi.apps.multiple_failures()
    assert (
        cli.run(
            api.schema_url,
            "-c",
            "status_code_conformance",
            "-c",
            "not_a_server_error",
            "--mode=positive",
            "--generation-deterministic",
        )
        == snapshot_cli
    )


@pytest.mark.parametrize("workers", [1, 2])
def test_connection_error(ctx, cli, workers, snapshot_cli):
    # When the given base_url is unreachable
    # Then the whole Schemathesis run should fail
    # And the proper error messages should be displayed for each operation
    api = ctx.openapi.apps.success_and_failure()
    assert (
        cli.run(api.schema_url, "--url=http://127.0.0.1:1/api", f"--workers={workers}", "--mode=positive")
        == snapshot_cli
    )


# A listener with a full accept queue drops new connection attempts, so they time out instead of being refused.
def test_server_that_times_out_connections_has_unknown_capabilities(ctx, cli):
    api = ctx.openapi.apps.success()
    with socket.create_server(("127.0.0.1", 0), backlog=0) as server:
        port = server.getsockname()[1]
        pending = [socket.socket() for _ in range(3)]
        for connection in pending:
            connection.setblocking(False)
            connection.connect_ex(("127.0.0.1", port))
        try:
            lines = cli.run(
                api.schema_url,
                f"--url=http://127.0.0.1:{port}/api",
                "--phases=fuzzing",
                "-n",
                "1",
                "--request-timeout=0.2",
            ).stdout.splitlines()
        finally:
            for connection in pending:
                connection.close()
    assert [line.strip() for line in lines if "API capabilities" in line or "✘" in line] == [
        "⚠️  API capabilities: unknown, server unreachable"
    ]


@pytest.mark.snapshot(replace_test_cases=False)
def test_connection_error_cases_are_not_reported_as_skipped(ctx, cli, snapshot_cli):
    api = ctx.openapi.apps.success()
    assert cli.run(api.schema_url, "--url=http://127.0.0.1:1/api", "--phases=fuzzing", "-n", "5") == snapshot_cli


@pytest.mark.snapshot(replace_test_cases=False, replace_traceback=True)
def test_crashed_check_cases_are_reported_as_errored(ctx, cli, snapshot_cli):
    api = ctx.openapi.apps.success()
    with ctx.check(
        """
@schemathesis.check
def broken_check(ctx, response, case):
    raise ZeroDivisionError("boom")
"""
    ) as module:
        assert (
            cli.run(
                api.schema_url,
                "--max-examples=5",
                "--phases=fuzzing",
                "--checks=broken_check",
                hooks=module,
            )
            == snapshot_cli
        )


def test_chunked_encoding_error(cli, raw_http_server, snapshot_cli):
    def empty_chunk_length(connection):
        connection.sendall(
            b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\nContent-Type: application/json\r\n\r\n\r\n"
        )

    assert cli.run(raw_http_server(empty_chunk_length), "--phases=fuzzing") == snapshot_cli


@pytest.mark.skipif(
    platform.system() == "Windows", reason="conn.close() on Windows does not raise ConnectionError on the client side"
)
def test_remote_disconnected_error(cli, snapshot_cli):
    listener = socket.create_server(("127.0.0.1", 0))

    def close_without_response(connection):
        with connection:
            connection.recv(65536)

    accept_connections(listener, close_without_response)
    with listener:
        assert cli.run(f"http://127.0.0.1:{listener.getsockname()[1]}/openapi.json") == snapshot_cli


@pytest.mark.skipif(
    platform.system() == "Windows", reason="conn.close() on Windows does not raise ConnectionError on the client side"
)
def test_remote_disconnected_error_with_empty_header(cli, raw_http_server, snapshot_cli):
    # An empty header value on a request the server drops must still render in the error report.
    def close_without_response(connection):
        pass

    assert cli.run(raw_http_server(close_without_response), config={"headers": {"X-Empty": ""}}) == snapshot_cli


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_server_error_closing_keep_alive_connection_is_not_a_network_error(ctx, cli, snapshot_cli):
    api = ctx.openapi.apps.crash_closes_connection()
    assert (
        cli.run(api.schema_url, "--phases=fuzzing", "--continue-on-failure", "--mode=positive", "--max-examples=5")
        == snapshot_cli
    )


@pytest.mark.skipif(platform.system() == "Windows", reason="Linux specific error")
def test_proxy_error(ctx, cli, snapshot_cli, app_runner):
    api = ctx.openapi.apps.success()
    # A port nothing listens on: port 80 may well be taken by a local web server, which then answers as the proxy.
    assert cli.run(api.schema_url, f"--proxy=http://127.0.0.1:{app_runner.unused_port()}") == snapshot_cli


@pytest.fixture
def digits_format(ctx):
    module = ctx.write_pymodule(
        """
    import string
    from hypothesis import strategies as st

    schemathesis.openapi.format(
        "digits",
        st.text(
            min_size=1,
            alphabet=st.characters(
                whitelist_characters=string.digits,
                whitelist_categories=()
            )
        )
    )
    """
    )
    yield module
    unregister_string_format("digits")


def test_hooks_valid(ctx, cli, digits_format):
    api = ctx.openapi.apps.custom_format()
    # When a hook is passed to the CLI call
    result = cli.main(
        "run",
        "--suppress-health-check=filter_too_much",
        "--phases=fuzzing",
        "--mode=positive",
        api.schema_url,
        hooks=digits_format,
    )
    # Then CLI should run successfully
    assert result.exit_code == ExitCode.OK, result.stdout
    test_cases = [req for req in api.requests if req.path == "/api/custom_format" and "id" in req.query]
    assert test_cases
    # And all registered new string format should produce digits as expected
    assert all(req.query["id"].isdigit() for req in test_cases)


@pytest.fixture
def conditional_check(ctx):
    with ctx.check("""
@schemathesis.check
def conditional_check(ctx, response, case):
    # skip this check
    return True
""") as module:
        yield module


def test_conditional_checks(ctx, cli, hypothesis_max_examples, conditional_check):
    api = ctx.openapi.apps.success()
    result = cli.main(
        "run",
        "-c",
        "conditional_check",
        api.schema_url,
        f"--max-examples={hypothesis_max_examples or 1}",
        hooks=conditional_check,
    )

    assert result.exit_code == ExitCode.OK


@pytest.fixture(
    params=[
        'AssertionError("Custom check failed!")',
        "AssertionError",
    ]
)
def new_check(ctx, request, cli):
    exception = request.param
    with ctx.check(
        f"""
@schemathesis.check
def new_check(ctx, response, result):
    raise {exception}
"""
    ) as module:
        yield module
    # To verify that "new_check" is unregistered
    assert "new_check" not in cli.run("--help").stdout


def test_register_check(ctx, new_check, cli, snapshot_cli):
    api = ctx.openapi.apps.success()
    # When hooks are passed to the CLI call
    # And it contains registering a new check, which always fails for the testing purposes
    # Then CLI run should fail
    # And a message from the new check should be displayed
    assert cli.main("run", "-c", "new_check", "--mode=positive", api.schema_url, hooks=new_check) == snapshot_cli


@pytest.mark.parametrize("workers", [1, 2])
@pytest.mark.filterwarnings("ignore:Exception in thread")
def test_keyboard_interrupt(ctx, cli, workers, snapshot_cli):
    api = ctx.openapi.apps.success()
    module = ctx.write_pymodule(INTERRUPT_ON_SECOND_RESPONSE)
    result = cli.run(api.schema_url, f"--workers={workers}", "--mode=positive", hooks=module)
    if workers == 1:
        assert result == snapshot_cli
    else:
        assert "skipped" in result.stdout


@pytest.mark.filterwarnings("ignore:Exception in thread")
def test_keyboard_interrupt_threaded(ctx, cli, snapshot_cli):
    # The hook runs on the worker threads, so the interrupt starts there rather than in the main thread.
    api = ctx.openapi.apps.success_and_failure()
    module = ctx.write_pymodule(INTERRUPT_ON_ADD_EXAMPLES)
    assert cli.run(api.schema_url, "--workers=2", "--generation-deterministic", hooks=module) == snapshot_cli


def test_keyboard_interrupt_during_schema_loading(ctx, cli, tmp_path, snapshot_cli):
    api = ctx.openapi.apps.success()
    module = ctx.write_pymodule(INTERRUPT_ON_SCHEMA_LOAD)
    report_path = tmp_path / "report.json"
    assert cli.run(api.schema_url, f"--report-json-path={report_path}", hooks=module) == snapshot_cli
    report = json.loads(report_path.read_text())
    assert (report["exit_code"], report["complete"], report["stop_reason"]) == (130, False, "interrupted")


INTERRUPT_ON_SCHEMA_LOAD = """
@schemathesis.hook
def before_load_schema(context, raw_schema):
    raise KeyboardInterrupt
"""

INTERRUPT_ON_ADD_EXAMPLES = """
@schemathesis.hook
def before_add_examples(context, examples):
    raise KeyboardInterrupt
"""


INTERRUPT_ON_SECOND_RESPONSE = """
calls = 0

@schemathesis.hook
def after_call(context, case, response):
    global calls
    calls += 1
    if calls > 1:
        raise KeyboardInterrupt
"""


@pytest.mark.parametrize(
    ("command", "status", "args", "failures"),
    [
        ("run", 200, ["--phases=fuzzing", "--max-examples=10"], 0),
        ("run", 500, ["--phases=fuzzing", "--max-examples=10", "--continue-on-failure"], 1),
        ("fuzz", 200, ["--max-time=10"], 0),
    ],
    ids=["run", "run-after-failure", "fuzz"],
)
def test_keyboard_interrupt_exit_code(ctx, cli, app_runner, tmp_path, command, status, args, failures):
    app, _ = ctx.openapi.make_flask_app(
        {
            "/items": {
                "get": {
                    "parameters": [{"name": "id", "in": "query", "required": True, "schema": {"type": "integer"}}],
                    "responses": {"200": {"description": "OK"}},
                }
            }
        }
    )

    @app.route("/items")
    def items():
        return jsonify({}), status

    module = ctx.write_pymodule(INTERRUPT_ON_SECOND_RESPONSE)
    report_path = tmp_path / "report.json"
    result = cli.main(
        command,
        app_runner.openapi_url(app),
        "--checks=not_a_server_error",
        f"--report-json-path={report_path}",
        *args,
        hooks=module,
    )
    report = json.loads(report_path.read_text())
    assert (result.exit_code, report["exit_code"], report["stop_reason"], len(report["failures"])) == (
        130,
        130,
        "interrupted",
        failures,
    ), result.stdout


def test_keyboard_interrupt_exit_code_during_hooks_loading(ctx, cli):
    api = ctx.openapi.apps.success()
    module = ctx.write_pymodule("raise KeyboardInterrupt")
    assert cli.main("run", api.schema_url, hooks=module).exit_code == 130


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_keyboard_interrupt_in_custom_handler_during_stateful(ctx, cli, snapshot_cli):
    @schemathesis.cli.handler()
    class Interrupter(schemathesis.cli.EventHandler):
        def handle_event(self, context, event) -> None:
            if isinstance(event, events.ScenarioStarted) and event.phase == PhaseName.STATEFUL_TESTING:
                raise KeyboardInterrupt

    api = ctx.openapi.apps.users_crud()
    assert cli.run(api.schema_url, "--phases=stateful", "--max-examples=1") == snapshot_cli
    assert [thread.name for thread in threading.enumerate() if thread.name == "schemathesis_stateful_tests"] == []


def test_multiple_files_schema(ctx, cli, hypothesis_max_examples):
    api = ctx.openapi.apps.teapot()
    # When the schema contains references to other files
    uri = pathlib.Path(HERE).as_uri() + "/"
    schema = ctx.openapi.build_schema(
        {
            "/teapot": {
                "post": {
                    "parameters": [
                        {
                            # during the CLI run we have a different working directory,
                            # so specifying an absolute uri
                            "schema": {"$ref": urljoin(uri, "data/petstore_v2.yaml#/definitions/Pet")},
                            "in": "body",
                            "name": "user",
                            "required": True,
                        }
                    ],
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
        version="2.0",
    )
    schema_path = ctx.makefile(schema)
    # And file path is given to the CLI
    result = cli.run(
        str(schema_path),
        f"--url={api.base_url}/api",
        f"--max-examples={hypothesis_max_examples or 5}",
        "--generation-deterministic",
        "-c not_a_server_error",
    )
    # Then Schemathesis should resolve it and run successfully
    assert result.exit_code == ExitCode.OK, result.stdout
    # And all relevant requests should contain proper data for resolved references
    teapot_calls = api.calls_to("/api/teapot", method="POST")
    payload = teapot_calls[0].json()
    assert isinstance(payload["name"], str)
    assert isinstance(payload["photoUrls"], list)


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_required_as_boolean(ctx, cli, snapshot_cli):
    # Happens in the wild, even though it is incorrect
    api = ctx.openapi.apps.success()
    variants = [
        (required, kwargs) for required in (True, False) for kwargs in ({}, {"required": []}, {"required": ["region"]})
    ]
    schema_path = ctx.openapi.write_schema(
        {
            f"/success{index}": {
                "post": {
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "properties": {"region": {"required": required, "type": "string"}},
                                    "type": "object",
                                    **kwargs,
                                }
                            }
                        },
                        "required": True,
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            }
            for index, (required, kwargs) in enumerate(variants)
        }
    )
    assert (
        cli.run(str(schema_path), f"--url={api.base_url}/api", "-c not_a_server_error", "--max-examples=5")
        == snapshot_cli
    )


def test_invalid_response_definition(ctx, cli, snapshot_cli):
    api = ctx.openapi.apps.success()
    schema_path = ctx.openapi.write_schema(
        {
            "/success": {
                "get": {
                    "responses": {
                        "default": {
                            "description": "text",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        # Seen in real-life schema
                                        "properties": "ABC",
                                    }
                                }
                            },
                        }
                    }
                }
            }
        }
    )
    assert (
        cli.run(str(schema_path), f"--url={api.base_url}/api", "-c response_schema_conformance", "--max-examples=1")
        == snapshot_cli
    )


def test_invalid_response_definition_behind_reference(ctx, cli, snapshot_cli):
    api = ctx.openapi.apps.success()
    schema_path = ctx.openapi.write_schema(
        {
            "/success": {
                "get": {
                    "responses": {
                        "default": {
                            "description": "text",
                            "content": {"application/json": {"schema": {"$ref": "#/components/schemas/Offset"}}},
                        }
                    }
                }
            }
        },
        components={
            "schemas": {
                "Base": {"type": "object"},
                "Offset": {
                    "allOf": [{"$ref": "#/components/schemas/Base"}, {"properties": {"earliest": {"type": "int64"}}}]
                },
            }
        },
    )
    assert (
        cli.run(str(schema_path), f"--url={api.base_url}/api", "-c response_schema_conformance", "--max-examples=1")
        == snapshot_cli
    )


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_no_useless_traceback(ctx, cli, snapshot_cli):
    api = ctx.openapi.apps.success()
    schema_path = ctx.openapi.write_schema(
        {
            "/success": {
                "post": {
                    "parameters": [
                        {"name": "key", "in": "query", "required": True, "schema": {"type": "integer"}, "example": 42}
                    ],
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "properties": {
                                        "region": {
                                            "nullable": True,
                                            "pattern": "^[\\p{Tibetan}]+$",
                                            "type": "string",
                                        },
                                    },
                                    "required": ["region"],
                                    "type": "object",
                                }
                            }
                        },
                        "required": True,
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            }
        }
    )
    assert cli.run(str(schema_path), f"--url={api.base_url}/api", "--mode=positive") == snapshot_cli


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_negative_mode_with_unsupported_regex_in_body(cli, ctx, snapshot_cli):
    app, _ = ctx.openapi.make_flask_app(
        {
            "/items": {
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {"name": {"type": "string", "pattern": "(?<=a+)b"}},
                                    "required": ["name"],
                                }
                            }
                        },
                    },
                    "responses": {"200": {"description": "OK"}, "400": {"description": "Bad"}},
                }
            }
        }
    )

    @app.route("/items", methods=["POST"])
    def items():
        return jsonify({}), 400

    assert (
        cli.run_openapi_app(app, "--phases=fuzzing", "--mode=negative", "--max-examples=5", "--seed=1") == snapshot_cli
    )


@pytest.mark.skipif(not yaml.__with_libyaml__, reason="Pure-Python PyYAML words the error differently")
def test_invalid_yaml(testdir, cli, simple_openapi, snapshot_cli, ctx):
    api = ctx.openapi.apps.success()
    schema = yaml.dump(simple_openapi)
    schema += "\x00"
    schema_file = testdir.makefile(".yaml", schema=schema)
    assert cli.run(str(schema_file), f"--url={api.base_url}/api") == snapshot_cli


@pytest.fixture
def with_error(ctx):
    with ctx.check(
        """
@schemathesis.check
def with_error(ctx, response, case):
    1 / 0
"""
    ) as module:
        yield module


@pytest.mark.skipif(
    sys.version_info >= (3, 13) or platform.system() == "Windows",
    reason="Cover only tracebacks that highlight error positions in every line",
)
def test_useful_traceback(ctx, cli, snapshot_cli, with_error):
    api = ctx.openapi.apps.success()
    assert cli.main("run", api.schema_url, "-c", "with_error", hooks=with_error) == snapshot_cli


# Errors raised from two places make Hypothesis report them together as a group.
@pytest.mark.snapshot(replace_reproduce_with=True, replace_traceback=True)
def test_multiple_generation_errors_in_fuzzing(ctx, cli, snapshot_cli):
    app, _ = ctx.openapi.make_flask_app(
        {
            "/items": {
                "get": {
                    "parameters": [{"name": "kind", "in": "query", "required": True, "schema": {"enum": ["a", "b"]}}],
                    "responses": {"200": {"description": "OK"}},
                }
            }
        }
    )
    with ctx.hook(
        """
@schemathesis.hook
def map_query(ctx, query):
    if query["kind"] == "a":
        raise ValueError("Generation failed")
    raise ValueError("Generation failed")
"""
    ) as module:
        assert (
            cli.run_openapi_app(app, "--phases=fuzzing", "--mode=positive", "--max-examples=10", hooks=module)
            == snapshot_cli
        )


@pytest.mark.parametrize("media_type", ["multipart/form-data", "multipart/mixed", "multipart/*"])
def test_multipart_upload(ctx, tmp_path, hypothesis_max_examples, cli, media_type):
    api = ctx.openapi.apps.success()
    cassette_path = tmp_path / "output.yaml"
    # When requestBody has a binary field or an array of binary items
    responses = {"200": {"description": "OK", "content": {"application/json": {"schema": {"type": "object"}}}}}
    schema_path = ctx.openapi.write_schema(
        {
            "/property": {
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {
                            media_type: {
                                "schema": {
                                    "type": "object",
                                    "properties": {"file": {"type": "string", "format": "binary"}},
                                    "required": ["file"],
                                    "additionalProperties": False,
                                }
                            }
                        },
                    },
                    "responses": responses,
                }
            },
            "/array": {
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {
                            media_type: {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "files": {
                                            "type": "array",
                                            "items": {"type": "string", "format": "binary"},
                                            "minItems": 1,
                                        }
                                    },
                                    "required": ["files"],
                                    "additionalProperties": False,
                                }
                            }
                        },
                    },
                    "responses": responses,
                }
            },
        }
    )
    result = cli.run_and_assert(
        str(schema_path),
        f"--url={api.base_url}/api",
        f"--max-examples={hypothesis_max_examples or 5}",
        "--generation-deterministic",
        f"--report-vcr-path={cassette_path}",
        "-c not_a_server_error",
        "--mode=positive",
    )
    # Then it should be correctly sent to the server
    assert "= ERRORS =" not in result.stdout

    with cassette_path.open(encoding="utf-8") as fd:
        raw = fd.read()
        cassette = yaml.safe_load(raw)

    def decode(idx):
        request = cassette["http_interactions"][idx]["request"]
        if "body" not in request:
            return None
        return request["body"]["string"].encode()

    first_decoded = decode(0)
    if first_decoded:
        assert re.search(rb'Content-Disposition: form-data; name="file"; filename="file(\.\w+)?"\r\n', first_decoded)
    last_decoded = decode(-1)
    if last_decoded:
        assert re.search(rb'Content-Disposition: form-data; name="files"; filename="files(\.\w+)?"\r\n', last_decoded)
    # NOTE, that the actual API operation is not checked in this test


@pytest.mark.parametrize(
    "field_name,field_schema,content_type",
    [
        ("image", {"type": "string", "format": "binary"}, "image/png"),
        ("metadata", {"type": "string"}, "application/json"),
        ("files", {"type": "array", "items": {"type": "string", "format": "binary"}}, "image/jpeg"),
    ],
    ids=["binary", "string", "array"],
)
def test_multipart_encoding_content_type(ctx, cli, snapshot_cli, field_name, field_schema, content_type):
    schema_def = {
        "type": "object",
        "properties": {field_name: field_schema},
        "required": [field_name],
    }
    app, _ = ctx.openapi.make_flask_app(
        {
            "/upload": {
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {
                            "multipart/form-data": {
                                "schema": schema_def,
                                "encoding": {field_name: {"contentType": content_type}},
                            }
                        },
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            }
        }
    )

    @app.route("/upload", methods=["POST"])
    def upload():
        # Check if field exists (could be in files or form)
        if field_name in request.files:
            file_obj = request.files[field_name]
            if file_obj.content_type != content_type:
                return jsonify({"error": f"Expected {content_type}, got: {file_obj.content_type}"}), 500
        # Accept any request as long as content type is correct when field is present
        return jsonify({"status": "ok"}), 200

    assert (
        cli.run_openapi_app(
            app,
            "--phases=fuzzing",
            "--max-examples=5",
            "--mode=positive",
            "--checks=not_a_server_error",
        )
        == snapshot_cli
    )


@pytest.mark.parametrize("version", ["3.0.2", "3.1.0"])
@pytest.mark.snapshot(replace_reproduce_with=True)
def test_coverage_multipart_comma_separated_content_type(ctx, cli, snapshot_cli, version):
    app, _ = ctx.openapi.make_flask_app(
        {
            "/upload": {
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {
                            "multipart/form-data": {
                                "schema": {
                                    "type": "object",
                                    "required": ["file"],
                                    "properties": {"file": {"type": "string", "format": "binary"}},
                                },
                                "encoding": {"file": {"contentType": "image/png, image/jpeg"}},
                            }
                        },
                    },
                    "responses": {"201": {"description": "Stored"}, "400": {"description": "Bad part"}},
                }
            }
        },
        version=version,
    )
    received = []

    @app.route("/upload", methods=["POST"])
    def upload():
        content_type = request.files["file"].content_type if "file" in request.files else None
        received.append(content_type)
        if content_type is not None and "," in content_type:
            return jsonify({"error": content_type}), 400
        return jsonify({}), 201

    result = cli.run_openapi_app(
        app, "--phases=coverage", "--mode=positive", "--checks=positive_data_acceptance", "--max-examples=10"
    )
    assert set(received) - {None} == {"image/png"}
    assert result == snapshot_cli


def test_no_schema_in_media_type(ctx, cli, snapshot_cli):
    app, _ = ctx.openapi.make_flask_app(
        {
            "/property": {
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {"multipart/form-data": {}},
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            },
        }
    )

    @app.route("/property", methods=["POST"])
    def property_handler():
        return jsonify({}), 200

    assert cli.run_openapi_app(app, "--max-examples=1", "--checks=not_a_server_error") == snapshot_cli


@pytest.mark.parametrize(
    "content",
    [
        {"application.json": {"schema": {"type": "object"}}},
        {"": {"schema": {"type": "object"}}, "application/json": {"schema": PAYLOAD_SCHEMA}},
    ],
    ids=["only-malformed", "valid-sibling"],
)
@pytest.mark.filterwarnings("error")
def test_malformed_media_type_in_request_body(ctx, cli, snapshot_cli, content):
    api = ctx.openapi.apps.payload()
    schema_path = ctx.openapi.write_schema(
        {
            "/payload": {
                "post": {
                    "requestBody": {"required": True, "content": content},
                    "responses": {"200": {"description": "OK"}},
                }
            }
        }
    )
    assert (
        cli.run(str(schema_path), f"--url={api.base_url}/api", "--max-examples=1", "--checks=not_a_server_error")
        == snapshot_cli
    )


MALFORMED_KEYWORD_OPERATIONS = {
    "query-min-length": {
        "parameters": [{"name": "q", "in": "query", "required": True, "schema": {"type": "string", "minLength": "x"}}]
    },
    "query-maximum": {
        "parameters": [{"name": "q", "in": "query", "required": True, "schema": {"type": "integer", "maximum": "x"}}]
    },
    "query-min-items": {
        "parameters": [
            {
                "name": "q",
                "in": "query",
                "required": True,
                "schema": {"type": "array", "items": {"type": "integer"}, "minItems": "x"},
            }
        ]
    },
    "query-max-items": {
        "parameters": [
            {
                "name": "q",
                "in": "query",
                "required": True,
                "schema": {"type": "array", "items": {"type": "integer"}, "maxItems": "x"},
            }
        ]
    },
    "query-disallowed-empty-min-length": {
        "parameters": [
            {
                "name": "q",
                "in": "query",
                "allowEmptyValue": False,
                "schema": {"type": "string", "minLength": "x"},
            }
        ]
    },
    "body-min-length": {
        "requestBody": {
            "required": True,
            "content": {
                "application/json": {
                    "schema": {"type": "object", "properties": {"name": {"type": "string", "minLength": "x"}}}
                }
            },
        }
    },
    "body-contains-unknown-type": {
        "requestBody": {
            "required": True,
            "content": {"application/json": {"schema": {"type": "array", "contains": {"type": "foo"}}}},
        }
    },
}


@pytest.mark.parametrize("phase", ["coverage", "fuzzing"])
@pytest.mark.snapshot(replace_reproduce_with=True)
def test_malformed_keyword(ctx, cli, snapshot_cli, phase):
    api = ctx.openapi.apps.success()
    schema_path = ctx.openapi.write_schema(
        {
            f"/{name}": {"post": {**operation, "responses": {"200": {"description": "OK"}}}}
            for name, operation in MALFORMED_KEYWORD_OPERATIONS.items()
        }
    )
    assert cli.run(str(schema_path), f"--url={api.base_url}/api", f"--phases={phase}") == snapshot_cli


@pytest.mark.parametrize("phase", ["examples", "coverage", "fuzzing"])
@pytest.mark.snapshot(replace_reproduce_with=True)
def test_non_string_pattern(ctx, cli, snapshot_cli, phase):
    app, _ = ctx.openapi.make_flask_app(
        {
            "/data": {
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {"a": {"type": "string", "pattern": ["x"]}},
                                }
                            }
                        },
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            }
        }
    )

    @app.route("/data", methods=["POST"])
    def data():
        return jsonify({})

    assert cli.run_openapi_app(app, f"--phases={phase}", "--checks=not_a_server_error") == snapshot_cli


@pytest.mark.parametrize(
    "operation",
    [
        {
            "requestBody": {
                "required": True,
                "content": {
                    "application/json": {
                        "schema": {
                            "type": "object",
                            "properties": {"a": {"type": "integer", "minimum": "5"}},
                        }
                    }
                },
            }
        },
        {"parameters": [{"name": "q", "in": "query", "required": True, "schema": {"type": "integer", "minimum": "5"}}]},
    ],
    ids=["body", "query"],
)
@pytest.mark.snapshot(replace_reproduce_with=True)
def test_negative_mode_malformed_keyword(ctx, cli, snapshot_cli, operation):
    app, _ = ctx.openapi.make_flask_app(
        {"/items": {"post": {**operation, "responses": {"200": {"description": "OK"}}}}}
    )
    assert cli.run_openapi_app(app, "--max-examples=5", "--phases=fuzzing", "--mode=negative") == snapshot_cli


def test_nested_binary_in_yaml(ctx, cli, app_runner, snapshot_cli):
    paths = {
        "/property": {
            "post": {
                "requestBody": {
                    "required": True,
                    "content": {
                        "*/*": {
                            "schema": {
                                "type": "object",
                                "properties": {"file": {"type": "string", "format": "binary"}},
                                "required": ["file"],
                            }
                        }
                    },
                },
                "responses": {
                    "200": {"description": "OK", "content": {"application/json": {"schema": {"type": "object"}}}}
                },
            }
        },
    }
    schema = ctx.openapi.build_schema(paths)
    app = ctx.openapi.make_permissive_flask_app(schema)
    base_url = app_runner.openapi_url(app, path="")
    schema_path = ctx.openapi.write_schema(paths)
    assert (
        cli.run(str(schema_path), f"--url={base_url}/api", "--max-examples=10", "-c not_a_server_error") == snapshot_cli
    )


def test_urlencoded_form(ctx, cli):
    # When the API operation accepts application/x-www-form-urlencoded
    # Then Schemathesis should generate appropriate payload
    api = ctx.openapi.apps.form()
    cli.run_and_assert(api.schema_url, "--mode=positive")


def _urlencoded_operation(version, schema):
    if version == "2.0":
        return {
            "consumes": ["application/x-www-form-urlencoded"],
            "parameters": [{"in": "body", "name": "body", "required": True, "schema": schema}],
            "responses": {"200": {"description": "OK"}},
        }
    return {
        "requestBody": {"required": True, "content": {"application/x-www-form-urlencoded": {"schema": schema}}},
        "responses": {"200": {"description": "OK"}},
    }


# `exclusiveMinimum: true` without `minimum` is rejected by the Draft 4 meta-schema.
@pytest.mark.parametrize(
    ("version", "keyword"),
    [("2.0", "exclusiveMinimum"), ("2.0", "exclusiveMaximum"), ("3.0.3", "exclusiveMinimum")],
    ids=["swagger-exclusive-minimum", "swagger-exclusive-maximum", "openapi-30-exclusive-minimum"],
)
@pytest.mark.snapshot(replace_reproduce_with=True)
def test_urlencoded_example_with_dangling_exclusive_bound(ctx, cli, snapshot_cli, version, keyword):
    schema = {
        "type": "object",
        "properties": {"bound": {"type": "integer", keyword: True}, "value": {"type": "integer", "example": 1}},
    }
    app, _ = ctx.openapi.make_flask_app({"/form": {"post": _urlencoded_operation(version, schema)}}, version=version)

    @app.route("/form", methods=["POST"])
    @app.route("/api/form", methods=["POST"])
    def form():
        return jsonify({})

    assert cli.run_openapi_app(app, "--phases=examples") == snapshot_cli


@pytest.mark.parametrize("workers", [1, 2])
def test_targeted(ctx, cli, workers):
    api = ctx.openapi.apps.success()
    observations = []
    with with_observability_callback(lambda observation, _: observations.append(observation), all_threads=True):
        cli.run_and_assert(api.schema_url, f"--workers={workers}", "--generation-maximize=response_time")

    assert any(
        "target:response_time" in observation.features
        for observation in observations
        if observation.type == "test_case"
    )


@pytest.mark.parametrize(
    ("options", "expected"),
    [
        (
            ("--exclude-deprecated",),
            "Selected: 1/2",
        ),
        (
            (),
            "Selected: 2/2",
        ),
    ],
)
def test_exclude_deprecated(ctx, cli, options, expected):
    # When there are some deprecated API operations
    api = ctx.openapi.apps.success()
    definition = {
        "responses": {"200": {"description": "OK", "content": {"application/json": {"schema": {"type": "object"}}}}}
    }
    schema_path = ctx.openapi.write_schema(
        {
            "/users": {
                "get": definition,
                "post": {
                    "deprecated": True,
                    **definition,
                },
            }
        }
    )
    result = cli.run_and_assert(
        str(schema_path),
        f"--url={api.base_url}/api",
        "--max-examples=1",
        "--checks=not_a_server_error",
        *options,
    )
    # Then only not deprecated API operations should be selected
    assert expected in result.stdout


def test_duplicated_filters(ctx, cli, snapshot_cli):
    api = ctx.openapi.apps.success_and_failure()
    assert cli.run(api.schema_url, "--include-path=success", "--include-path=success") == snapshot_cli


def test_invalid_filter(ctx, cli, snapshot_cli):
    api = ctx.openapi.apps.success_and_failure()
    assert cli.run(api.schema_url, "--include-by=fooo") == snapshot_cli


def test_filter_case_sensitivity(ctx, cli, snapshot_cli):
    # Method filter should be case insensitive
    api = ctx.openapi.apps.success_and_failure()
    assert cli.run(api.schema_url, "--include-method=get", "--checks=not_a_server_error") == snapshot_cli


@pytest.mark.parametrize("value", ["--include-by=/x-property == 42", "--exclude-by=/x-property != 42"])
def test_filter_by(ctx, cli, snapshot_cli, value):
    api = ctx.openapi.apps.upload_file_and_custom_format()
    assert cli.run(api.schema_url, "--mode=positive", "--max-examples=1", value) == snapshot_cli


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_schema_serving_endpoint_is_not_tested(ctx, cli, snapshot_cli):
    app, _ = ctx.openapi.make_flask_app(
        {
            "/openapi.json": {"get": {"responses": {"200": {"description": "OK"}}}},
            "/users": {"get": {"responses": {"200": {"description": "OK"}}}},
        }
    )

    @app.route("/users")
    def users():
        return jsonify([])

    assert cli.run_openapi_app(app, "--max-examples=5") == snapshot_cli


def test_colon_in_headers(ctx, cli):
    api = ctx.openapi.apps.success()
    header = "X-FOO"
    value = "bar:spam"
    cli.run_and_assert(api.schema_url, f"--header={header}:{value}")

    # Werkzeug normalises header names to title case
    assert api.requests[0].headers["X-Foo"] == value


def test_headers_passed_to_schema_loading(cli, app_runner, ctx):
    # GH-3440: Headers should be passed to schema loading request
    schema = ctx.openapi.build_schema({"/users": {"get": {"responses": {"200": {"description": "OK"}}}}})

    app = Flask("schemathesis_test")
    schema_requests = []

    @app.route("/openapi.json")
    def openapi():
        schema_requests.append(dict(request.headers))
        auth = request.headers.get("Authorization")
        if auth != "Bearer secret-token":
            return jsonify({"error": "Unauthorized"}), 401
        return jsonify(schema)

    @app.route("/users")
    def users():
        return jsonify([])

    cli.run_and_assert(
        app_runner.openapi_url(app),
        "-H",
        "Authorization: Bearer secret-token",
        "--max-examples=1",
    )

    assert len(schema_requests) >= 1
    assert schema_requests[0].get("Authorization") == "Bearer secret-token"


def test_yaml_parsing_of_floats(ctx, cli, testdir):
    api = ctx.openapi.apps.success()
    # When a YAML schema contains a pattern that looks like a time (e.g., 00:00:00.00),
    # YAML parses it as a float (0.0). The pattern should be gracefully ignored,
    # allowing tests to proceed without errors.
    schema = """info:
  description: Test
  title: Test
  version: 0.1.0
openapi: 3.0.2
paths:
  /test:
    get:
      parameters:
      - in: query
        name: q
        schema:
          pattern: 00:00:00.00
          type: string
      responses:
        '200':
          description: OK"""
    schema_file = testdir.makefile(".yaml", schema=schema)
    # Non-string pattern is gracefully handled (ignored), so tests should pass
    cli.run_and_assert(
        str(schema_file),
        f"--url={api.base_url}/api",
        "--phases=fuzzing",
        "--checks=not_a_server_error",
        "--mode=positive",
    )


@pytest.mark.parametrize("workers", [1, 2])
def test_max_response_time_invalid(ctx, cli, workers, snapshot_cli):
    # When maximum response time check is specified in the CLI and the request takes more time
    # Then the whole Schemathesis run should fail
    # And the given operation should be displayed as a failure
    # And the proper error message should be displayed
    api = ctx.openapi.apps.slow()
    # Only fuzz the declared GET: undeclared-method probes can exceed the tight limit under load, so their
    # competing failures would make the reported case's method non-deterministic.
    assert (
        cli.run(api.schema_url, "--max-response-time=0.05", "--phases=fuzzing", f"--workers={workers}") == snapshot_cli
    )


def test_max_response_time_valid(ctx, cli):
    api = ctx.openapi.apps.slow()
    # When maximum response time check is specified in the CLI and the request takes less time
    # Then no errors should occur
    cli.run_and_assert(api.schema_url, "--max-response-time=200")


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_timeouts_given_in_milliseconds_warning(ctx, cli, snapshot_cli):
    api = ctx.openapi.apps.success()

    assert (
        cli.run(
            api.schema_url,
            "--request-timeout=2000",
            "--phases=fuzzing",
            "--max-examples=1",
            config={"checks": {"max_response_time": 30000}},
        )
        == snapshot_cli
    )


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_timeouts_given_in_milliseconds_warning_large_values(ctx, cli, snapshot_cli):
    api = ctx.openapi.apps.success()

    assert (
        cli.run(
            api.schema_url,
            "--request-timeout=1e6",
            "--phases=fuzzing",
            "--max-examples=1",
            config={"checks": {"max_response_time": 2.5e6}},
        )
        == snapshot_cli
    )


def test_no_timeout_units_warning_below_1000_seconds(ctx, cli):
    api = ctx.openapi.apps.success()

    result = cli.run(
        api.schema_url, "--request-timeout=999", "--max-response-time=999", "--phases=fuzzing", "--max-examples=1"
    )

    assert "Schemathesis takes seconds" not in result.stdout


@pytest.mark.parametrize(
    "args, config",
    [
        (("--warnings=off",), None),
        (("--warnings=missing_auth",), None),
        ((), {"warnings": {"enabled": False}}),
        ((), {"warnings": {"display": ["missing_auth"]}}),
    ],
    ids=["cli-off", "cli-other-kind", "config-disabled", "config-other-kind"],
)
def test_timeout_units_warning_can_be_disabled(ctx, cli, args, config):
    api = ctx.openapi.apps.success()

    result = cli.run(
        api.schema_url, "--request-timeout=2000", "--phases=fuzzing", "--max-examples=1", *args, config=config
    )

    assert "Schemathesis takes seconds" not in result.stdout


@pytest.mark.snapshot(remove_last_line=True)
def test_exit_first(ctx, cli, snapshot_cli):
    # When the `--max-failures=1` CLI option is passed
    # And a failure occurs
    api = ctx.openapi.apps.success_and_failure()
    assert (
        cli.run(api.schema_url, "--max-failures=1", "--phases=fuzzing", "--checks=not_a_server_error") == snapshot_cli
    )


@pytest.mark.snapshot(replace_cycle_metrics=True, replace_reproduce_with=True)
def test_max_time_reports_the_first_cycle_in_full(ctx, cli, snapshot_cli):
    # Each phase still reports everything it covered on its first pass, budget or no budget.
    app, _ = ctx.openapi.make_flask_app(linked_users())
    counter = count()
    handlers.register_linked_users_create(app)

    @app.route("/users/<int:user_id>")
    def get_user(user_id):
        # Fuzzing is bounded by `--max-examples`, so only the linked calls can reach the bug.
        return ("", 500) if next(counter) >= 5 else jsonify({"id": user_id})

    assert (
        cli.run_openapi_app(
            app,
            "--max-time=30",
            "--max-failures=1",
            "--max-examples=5",
            "--generation-database=none",
            "--phases=fuzzing,stateful",
            "--checks=not_a_server_error",
        )
        == snapshot_cli
    )


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_stateful_unresponsive_operation_counted_as_errored(ctx, cli, snapshot_cli):
    app, _ = ctx.openapi.make_flask_app(linked_users())
    release = threading.Event()
    handlers.register_linked_users_create(app)

    @app.route("/users/<int:user_id>")
    def get_user(user_id):
        release.wait()
        return jsonify({"id": user_id})

    try:
        result = cli.run_openapi_app(
            app,
            "--max-examples=20",
            "--generation-deterministic",
            "--phases=stateful",
            "--checks=not_a_server_error",
            config={"operations": [{"include-name": "GET /users/{userId}", "request-timeout": 0.25}]},
        )
    finally:
        release.set()
    assert result == snapshot_cli


def test_max_time_live_cycle_line_counts_errors(ctx, cli):
    api = ctx.openapi.apps.slow()
    result = cli.main(
        "run",
        api.schema_url,
        "--phases=fuzzing",
        "--max-time=30",
        "--max-failures=1",
        "--request-timeout=0.001",
        env={"PYTEST_VERSION": None, "FORCE_COLOR": "1"},
    )
    assert re.search(r"\d+ cases  🚫 1 error  ⏳ \S+ left", result.stdout)


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_max_time_counts_every_operation_once(ctx, cli, snapshot_cli):
    # A phase repeats until the budget is gone; its block still counts operations, not passes over them.
    app, _ = ctx.openapi.make_flask_app(
        {
            "/users": {"get": {"responses": {"200": {"description": "OK"}}}},
            "/boom": {"get": {"responses": {"200": {"description": "OK"}}}},
        }
    )

    @app.route("/users")
    def users():
        return jsonify([])

    @app.route("/boom")
    def boom():
        return "", 500

    assert (
        cli.run_openapi_app(
            app,
            "--max-time=2",
            "--max-examples=5",
            "--generation-database=none",
            "--phases=fuzzing",
            "--checks=not_a_server_error",
        )
        == snapshot_cli
    )


@pytest.mark.snapshot(replace_reproduce_with=True, replace_phase_statistic=True)
def test_coverage_stops_at_failure_limit(ctx, cli, snapshot_cli):
    api = ctx.openapi.apps.success_and_failure()
    assert (
        cli.run(api.schema_url, "--phases=coverage", "--max-examples=5", "--max-failures=1", "--seed=1") == snapshot_cli
    )


@pytest.mark.snapshot(replace_cycle_metrics=True, replace_reproduce_with=True)
def test_max_time_reports_a_failure_found_in_a_repeat(ctx, cli, snapshot_cli):
    # The collapsed line stands for every repeat, so a failure inside one has to show on it.
    app, _ = ctx.openapi.make_flask_app({"/users": {"get": {"responses": {"200": {"description": "OK"}}}}})
    counter = count()

    @app.route("/users")
    def users():
        # The first cycle is bounded by `--max-examples`, so only a repeat can reach the bug.
        return ("", 500) if next(counter) >= 5 else jsonify([])

    assert (
        cli.run_openapi_app(
            app,
            "--max-time=30",
            "--max-failures=1",
            "--max-examples=5",
            "--generation-database=none",
            "--phases=fuzzing",
        )
        == snapshot_cli
    )


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_max_time_reports_coverage_once_with_its_repeats(ctx, cli, snapshot_cli):
    # Coverage repeats for operations whose error responses taught something; it still reads as one phase.
    api = ctx.openapi.apps.planted_bug()
    assert (
        cli.run(
            api.schema_url,
            "--max-time=30",
            "--generation-database=none",
            "--phases=coverage",
            "--checks=not_a_server_error",
        )
        == snapshot_cli
    )


@pytest.mark.snapshot(replace_cycle_metrics=True, replace_reproduce_with=True)
def test_max_time_reports_coverage_once_beside_fuzzing(ctx, cli, snapshot_cli):
    # Rejections only start teaching after the first coverage pass, so coverage repeats between fuzzing passes.
    app, _ = ctx.openapi.make_flask_app(
        {
            "/users": {
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {"type": "object", "properties": {"name": {"type": "string"}}}
                            }
                        },
                    },
                    "responses": {"201": {"description": "OK"}, "400": {"description": "Bad"}},
                }
            }
        }
    )
    counter = count()

    @app.route("/users", methods=["POST"])
    def create_user():
        current = next(counter)
        if current < 20:
            return jsonify({}), 201
        return jsonify({"messages": [f"field{current} - must not be blank"]}), 400

    assert (
        cli.run_openapi_app(
            app,
            "--max-time=5",
            "--max-examples=5",
            "--generation-database=none",
            "--phases=coverage,fuzzing",
            "--checks=not_a_server_error",
            "--warnings=off",
        )
        == snapshot_cli
    )


def test_long_operation_output(ctx, cli, app_runner, snapshot_cli):
    # See GH-990
    # When there is a narrow screen
    # And the API schema contains an operation with a long name
    paths = {
        f"/{'a' * 100}": {
            "get": {
                "responses": {"200": {"description": "OK"}},
            }
        },
        f"/{'a' * 10}": {
            "get": {
                "responses": {"200": {"description": "OK"}},
            }
        },
    }
    schema = ctx.openapi.build_schema(paths)
    app = ctx.openapi.make_permissive_flask_app(schema)
    base_url = app_runner.openapi_url(app, path="")
    schema_path = ctx.openapi.write_schema(paths)
    # Then this operation name should be truncated
    assert cli.run(str(schema_path), f"--url={base_url}/api", "-c not_a_server_error") == snapshot_cli


def test_unsupported_regex(ctx, cli, app_runner, snapshot_cli):
    def make_definition(min_items, pattern=r"\p{Tibetan}"):
        return {
            "post": {
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "array",
                                # Java-style regular expression
                                "items": {"type": "string", "pattern": pattern},
                                "maxItems": 3,
                                "minItems": min_items,
                            }
                        }
                    },
                },
                "responses": {"200": {"description": "OK"}},
            }
        }

    # When an operation uses an unsupported regex syntax
    paths = {
        # Can't generate anything
        "/foo": make_definition(min_items=1),
        # Can generate an empty array
        "/bar": make_definition(min_items=0),
        # A quote inside the pattern changes how it is quoted when reported
        "/baz": make_definition(min_items=1, pattern=r"\p{Tibetan}'"),
    }
    schema = ctx.openapi.build_schema(paths)
    app = ctx.openapi.make_permissive_flask_app(schema)
    base_url = app_runner.openapi_url(app, path="")
    schema_path = ctx.openapi.write_schema(paths)
    # Then if it is possible it should generate at least something
    # And if it is not then there should be an error with a descriptive error message
    assert (
        cli.run(
            str(schema_path),
            "--max-examples=1",
            f"--url={base_url}/api",
            "-c not_a_server_error",
            "--mode=positive",
        )
        == snapshot_cli
    )


@pytest.mark.parametrize("extra", ["--auth='test:wrong'", "-H Authorization: Basic J3Rlc3Q6d3Jvbmcn"])
def test_auth_override_on_protected_operation(ctx, cli, extra, snapshot_cli):
    # See GH-792
    # When the tested API operation has basic auth
    # And the auth is overridden (directly or via headers)
    # And there is an error during testing
    # Then the code sample representation in the output should have the overridden value
    api = ctx.openapi.apps.basic()
    assert cli.run(api.schema_url, "--output-sanitize=false", "--phases=fuzzing", extra) == snapshot_cli


@pytest.mark.parametrize("sanitize", ["true", "false"])
def test_url_credentials_in_output(ctx, cli, snapshot_cli, sanitize):
    api = ctx.openapi.apps.success()
    schema_url = api.schema_url.replace("http://", "http://user:secret@")
    assert cli.run(schema_url, f"--output-sanitize={sanitize}", "--max-examples=1", "--phases=fuzzing") == snapshot_cli


def test_explicit_headers_in_output_on_errors(ctx, cli, snapshot_cli):
    # When there is a non-fatal error during testing (e.g. flakiness)
    # And custom headers were passed explicitly
    api = ctx.openapi.apps.flaky()
    auth = "Basic J3Rlc3Q6d3Jvbmcn"
    # Then the code sample should have the overridden value
    assert (
        cli.run(api.schema_url, "--output-sanitize=false", f"-H Authorization: {auth}", "--mode=positive")
        == snapshot_cli
    )


def test_response_payload_encoding(ctx, cli, snapshot_cli):
    # See GH-1073
    # When the "failed" response has non UTF-8 encoding
    # Then it should be displayed according its actual encoding
    api = ctx.openapi.apps.cp866()
    assert cli.run(api.schema_url) == snapshot_cli


@pytest.mark.snapshot(replace_test_cases=False)
def test_response_schema_conformance_deduplication(ctx, cli, snapshot_cli):
    # See GH-907
    # When the "response_schema_conformance" check is present
    # And the app return different error messages caused by the same validator
    # Then the errors should be deduplicated
    api = ctx.openapi.apps.conformance()
    assert cli.run(api.schema_url, "--checks=response_schema_conformance") == snapshot_cli


@pytest.mark.skipif(platform.python_implementation() == "PyPy", reason="PyPy behaves differently")
def test_malformed_json_deduplication(ctx, cli, snapshot_cli):
    # See GH-1518
    # When responses are not JSON as expected and their content differ each time
    # Then the errors should be deduplicated
    api = ctx.openapi.apps.malformed_json()
    assert cli.run(api.schema_url, "--checks=response_schema_conformance") == snapshot_cli


@pytest.mark.parametrize("kind", ["env_var", "arg"])
def test_no_color(ctx, monkeypatch, cli, kind):
    api = ctx.openapi.apps.success()
    args = (api.schema_url,)
    if kind == "env_var":
        monkeypatch.setenv("NO_COLOR", "1")
    if kind == "arg":
        args += ("--no-color",)
    result = cli.run_and_assert(*args, color=True)

    assert "\x1b[" not in result.stdout


@pytest.mark.skipif(platform.system() == "Windows", reason="ANSI colors are not properly supported in Windows tests")
def test_force_color(ctx, cli):
    api = ctx.openapi.apps.success()
    # Using `--force-color` adds ANSI escape codes forcefully
    result = cli.run_and_assert(api.schema_url, "--force-color", color=False)

    assert "[1m" in result.stdout


@pytest.mark.parametrize(
    "args",
    [
        (),
        ("--checks", "all"),
    ],
)
@pytest.mark.parametrize("endpoint", ["/graphql", "/foo"])
def test_graphql_url(ctx, cli, endpoint, args, snapshot_cli):
    # When the target API is GraphQL
    api = ctx.graphql.apps.books(endpoint=endpoint)
    assert cli.run(api.schema_url, "--max-examples=5", "--mode=positive", *args) == snapshot_cli


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_graphql_schema_violation(ctx, cli, snapshot_cli):
    # Every case hits the same broken field, so the run must report one finding rather than one per case.
    @strawberry.type
    class Query:
        @strawberry.field
        def author_name(self) -> str:
            return None

    api = ctx.graphql.apps.from_schema(strawberry.Schema(Query))

    assert cli.run(api.schema_url, "--max-examples=5", "--mode=positive", "--continue-on-failure") == snapshot_cli


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_graphql_data_mismatch(ctx, cli, tmp_path, snapshot_cli):
    # The same defect on every list item and in every case is one finding with one line.
    sdl = tmp_path / "schema.graphql"
    sdl.write_text("type Book { title: String! } type Query { books: [Book!]! }")
    api = ctx.graphql.apps.canned_response({"data": {"books": [{"title": None}, {"title": None}]}})

    assert (
        cli.run(str(sdl), f"--url={api.base_url}", "--max-examples=5", "--mode=positive", "--continue-on-failure")
        == snapshot_cli
    )


@pytest.mark.parametrize("location", ["path", "query", "header", "cookie"])
def test_missing_content_and_schema(ctx, cli, location, snapshot_cli):
    # When an Open API 3 parameter is missing `schema` & `content`
    api = ctx.openapi.apps.success()
    schema_path = ctx.openapi.write_schema(
        {"/foo": {"get": {"parameters": [{"in": location, "name": "X-Foo", "required": True}]}}}
    )
    # Then CLI should show that this API operation errored
    # And show the proper message under its "ERRORS" section
    assert cli.run(str(schema_path), "--max-examples=1", f"--url={api.base_url}/api") == snapshot_cli


def test_explicit_query_token_sanitization(ctx, cli, snapshot_cli):
    api = ctx.openapi.apps.failure()
    schema_path = ctx.openapi.write_schema(
        {
            "/api/failure": {
                "get": {
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
        security=[{"api_key": []}],
        components={
            "securitySchemes": {
                "api_key": {
                    "type": "apiKey",
                    "name": "token",
                    "in": "query",
                },
            }
        },
    )
    token = "secret"
    result = cli.run_and_assert(
        str(schema_path),
        f"--url={api.base_url}",
        "-c not_a_server_error",
        config={"parameters": {"token": token}},
        exit_code=ExitCode.TESTS_FAILED,
    )
    assert result == snapshot_cli
    assert token not in result.stdout


def test_skip_not_negated_tests(ctx, cli, snapshot_cli):
    api = ctx.openapi.apps.success()
    # See GH-1463
    # When an endpoint has no parameters to negate
    # Then it should be skipped
    assert cli.run(api.schema_url, "--mode", "negative") == snapshot_cli


def test_dont_skip_when_generation_is_possible(ctx, cli, snapshot_cli):
    api = ctx.openapi.apps.success()
    assert cli.run(api.schema_url, "--mode", "all") == snapshot_cli


def test_explicit_example_failure_output(ctx, cli, snapshot_cli):
    api = ctx.openapi.apps.failure()
    # When an explicit example fails
    schema_path = ctx.openapi.write_schema(
        {
            "/api/failure": {
                "get": {
                    "parameters": [{"in": "query", "name": "key", "example": "foo", "schema": {"type": "string"}}],
                    "responses": {"200": {"description": "OK"}},
                }
            },
        }
    )
    assert (
        cli.run(str(schema_path), f"--url={api.base_url}", "--output-sanitize=false", "-c not_a_server_error")
        == snapshot_cli
    )


def test_curl_with_non_printable_characters(ctx, cli, snapshot_cli, monkeypatch):
    monkeypatch.setenv("SHELL", "/bin/bash")
    api = ctx.openapi.apps.failure()
    schema_path = ctx.openapi.write_schema(
        {
            "/api/failure": {
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {
                            "text/plain": {
                                "schema": {"type": "string"},
                                "example": "line1\nline2\ttab\x1fcontrol",
                            }
                        },
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            },
        }
    )
    assert (
        cli.run(str(schema_path), f"--url={api.base_url}", "--output-sanitize=false", "-c not_a_server_error")
        == snapshot_cli
    )


# Stand-ins for `curl` that print the request body they receive
CURL_ECHO = {
    ShellType.BASH: 'curl() { while [ $# -gt 0 ]; do if [ "$1" = -d ]; then printf %s "$2"; shift; fi; shift; done; }',
    ShellType.FISH: "function curl; set -l i (contains -i -- -d $argv); printf %s $argv[(math $i + 1)]; end",
}


@pytest.mark.parametrize("shell", list(CURL_ECHO), ids=lambda shell: shell.value)
@pytest.mark.parametrize(
    "body",
    [
        "7\x92Y\x83",
        "line separator paragraph\x85next",
        "mixed\x1c\x85\U0009c8e5\x01​",
    ],
    ids=["c1-controls", "unicode-line-separators", "mixed-with-ascii-controls"],
)
def test_curl_reproduces_body_with_line_separator_characters(ctx, cli, monkeypatch, body, shell):
    executable = shutil.which(shell.value)
    if executable is None:
        pytest.skip(f"{shell.value} is not installed")
    monkeypatch.setenv("SHELL", f"/bin/{shell.value}")
    api = ctx.openapi.apps.failure()
    schema_path = ctx.openapi.write_schema(
        {
            "/api/failure": {
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {"text/plain": {"schema": {"type": "string"}, "example": body}},
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            },
        }
    )
    result = cli.run(
        str(schema_path),
        f"--url={api.base_url}",
        "--phases=examples",
        "--output-sanitize=false",
        "-c status_code_conformance",
    )
    printed = result.stdout.split("Reproduce with:\n\n", 1)[1].split("\n\n", 1)[0]
    command = "\n".join(line.removeprefix("    ") for line in printed.split("\n"))
    # Paste the printed command into a real shell
    echoed = subprocess.run([executable, "-c", f"{CURL_ECHO[shell]}\n{command}"], capture_output=True, check=False)
    assert echoed.stderr == b""
    assert echoed.stdout == body.encode("utf-8")
    # Raw control characters in the terminal can be interpreted as escape sequences.
    assert command.isprintable()


@pytest.mark.skipif(platform.system() == "Windows", reason="Requires more complex setup")
def test_curl_with_non_printable_characters_unknown_shell(ctx, cli, snapshot_cli, monkeypatch):
    monkeypatch.setenv("SHELL", "/bin/unknown")
    api = ctx.openapi.apps.failure()
    schema_path = ctx.openapi.write_schema(
        {
            "/api/failure": {
                "post": {
                    "parameters": [
                        {
                            "in": "header",
                            "name": "X-Custom",
                            "schema": {"type": "string"},
                            "example": "test\x00value",
                        }
                    ],
                    "requestBody": {
                        "required": True,
                        "content": {
                            "text/plain": {
                                "schema": {"type": "string"},
                                "example": "data\x1f",
                            }
                        },
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            },
        }
    )
    assert (
        cli.run(str(schema_path), f"--url={api.base_url}", "--output-sanitize=false", "-c not_a_server_error")
        == snapshot_cli
    )


def test_skipped_on_no_explicit_examples(ctx, cli, snapshot_cli):
    api = ctx.openapi.apps.success()
    # See GH-1323
    # When there are no explicit examples
    # Then tests should be marked as skipped
    assert cli.run(api.schema_url, "--phases=examples") == snapshot_cli


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_skipped_on_no_coverage_cases(ctx, cli, snapshot_cli):
    app, _ = ctx.openapi.make_flask_app(
        {
            "/items": {
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {"schema": {"allOf": [{"type": "string"}, {"type": "integer"}]}}
                        },
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            }
        }
    )
    assert cli.run_openapi_app(app, "--phases=coverage", "--mode=positive") == snapshot_cli


@pytest.fixture
def data_generation_check(ctx):
    with ctx.check(
        """
@schemathesis.check
def data_generation_check(ctx, response, case):
    if case.meta.generation.mode:
        note("MODE: {}".format(case.meta.generation.mode.value))
"""
    ) as module:
        yield module


@pytest.mark.flaky(reruns=4)
def test_multiple_generation_modes(ctx, cli, data_generation_check):
    api = ctx.openapi.apps.payload()
    # When multiple data generation methods are supplied in CLI
    result = cli.main(
        "run",
        "-c",
        "data_generation_check",
        "-c",
        "not_a_server_error",
        api.schema_url,
        "--max-examples=25",
        "--suppress-health-check=all",
        "--mode",
        "all",
        hooks=data_generation_check,
    )
    # Then there should be cases generated from different methods
    assert result.exit_code == ExitCode.OK, result.stdout
    assert "MODE: positive" in result.stdout
    assert "MODE: negative" in result.stdout


@pytest.mark.parametrize(
    ("schema_path", "make_app"),
    (
        [
            (
                "openapi.json",
                lambda: openapi_basic.success().server,
            ),
            (
                "graphql",
                lambda: graphql_bookstore.books().server,
            ),
        ]
    ),
)
def test_wait_for_schema(cli, app_runner, schema_path, make_app):
    # When Schemathesis is asked to wait for API schema to become available
    app = make_app()
    original_run = app.run

    def run_with_delay(*args, **kwargs):
        time.sleep(0.1)
        return original_run(*args, **kwargs)

    app.run = run_with_delay
    schema_url = app_runner.openapi_url(app, path=f"/{schema_path}", wait=False)
    cli.run_and_assert(schema_url, "--wait-for-schema=1", "--max-examples=1", "--mode=positive")


@pytest.mark.skipif(platform.system() == "Windows", reason="Fails on Windows")
def test_wait_for_schema_not_enough(cli, snapshot_cli):
    with refusing_url() as url:
        assert cli.run(f"{url}/openapi.json", "--wait-for-schema=1", "--max-examples=1") == snapshot_cli


def test_wait_for_schema_retries_503(cli, app_runner, ctx):
    # When the schema endpoint returns 503 initially, then succeeds
    schema = ctx.openapi.build_schema({"/success": {"get": {"responses": {"200": {"description": "OK"}}}}})
    app = Flask(__name__)
    call_count = [0]

    @app.route("/openapi.json")
    def openapi_spec():
        call_count[0] += 1
        if call_count[0] < 3:
            return jsonify({"error": "service unavailable"}), 503
        return jsonify(schema)

    @app.route("/success")
    def success():
        return jsonify({})

    cli.run_and_assert(app_runner.openapi_url(app), "--wait-for-schema=5", "--max-examples=1", "--mode=positive")


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_wait_for_schema_503_exhausted(cli, app_runner, snapshot_cli):
    # When the schema endpoint always returns 503 until the wait timeout expires
    app = Flask(__name__)

    @app.route("/openapi.json")
    def always_503():
        return jsonify({"error": "service unavailable"}), 503

    assert cli.run(app_runner.openapi_url(app), "--wait-for-schema=1") == snapshot_cli


def test_rate_limit(ctx, cli):
    api = ctx.openapi.apps.success()
    start = time.perf_counter()
    result = cli.run(
        api.schema_url,
        "--rate-limit=1/s",
        "--phases=coverage",
        config={"phases": {"coverage": {"unexpected-methods": ["PUT", "POST"]}}},
    )
    assert result.exit_code == ExitCode.OK, result.stdout
    assert len(api.requests) >= 3
    assert time.perf_counter() - start >= len(api.requests) - 1


def test_invalid_tls_verify(ctx, cli, snapshot_cli):
    api = ctx.openapi.apps.failure()
    assert cli.run(api.schema_url.replace("http", "https"), "--tls-verify=falst") == snapshot_cli


@pytest.mark.parametrize("version", ["3.0.2", "3.1.0"])
def test_invalid_schema_with_disabled_validation(
    ctx, cli, openapi_3_schema_with_invalid_security, version, snapshot_cli
):
    # When there is an error in the schema
    api = ctx.openapi.apps.success()
    openapi_3_schema_with_invalid_security["openapi"] = version
    schema_path = ctx.makefile(openapi_3_schema_with_invalid_security)
    # And the validation is disabled (default)
    # Then we should show an error message derived from JSON Schema
    assert cli.run(str(schema_path), f"--url={api.base_url}/api") == snapshot_cli


def test_unresolvable_reference(ctx, cli, open_api_3_schema_with_recoverable_errors, snapshot_cli):
    # When there is an error in the schema
    api = ctx.openapi.apps.success()
    del open_api_3_schema_with_recoverable_errors["paths"]["/bar"]["get"]
    schema_path = ctx.makefile(open_api_3_schema_with_recoverable_errors)
    # Then we should show an error message derived from JSON Schema
    assert cli.run(str(schema_path), f"--url={api.base_url}/api") == snapshot_cli


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_optional_parameter_with_unresolvable_reference(ctx, cli, snapshot_cli):
    # The operation stays testable; the dropped parameter is reported instead
    app, _ = ctx.openapi.make_flask_app(
        {
            "/things": {
                "get": {
                    "parameters": [
                        {
                            "in": "query",
                            "name": "filter",
                            "required": False,
                            "schema": {"$ref": "#/components/schemas/Missing"},
                        }
                    ],
                    "responses": {"200": {"description": "OK"}},
                }
            }
        }
    )

    @app.route("/things")
    def things():
        return jsonify([])

    assert cli.run_openapi_app(app, "--max-examples=1") == snapshot_cli


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_unresolvable_reference_in_response_schema(ctx, cli, snapshot_cli):
    # Only the unresolvable response goes unvalidated; the operation keeps running every other check
    app, _ = ctx.openapi.make_flask_app(
        {
            "/things": {
                "get": {
                    "responses": {
                        "200": {"description": "OK", "content": {"application/json": {"schema": {"type": "object"}}}},
                        "404": {
                            "description": "Not Found",
                            "content": {"*/*": {"schema": {"$ref": "#/components/schemas/ExceptionResponse"}}},
                        },
                    }
                }
            }
        }
    )

    @app.route("/things")
    def things():
        return jsonify({"detail": "nope"}), 404

    assert cli.run_openapi_app(app, "--max-examples=1") == snapshot_cli


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_required_body_with_unresolvable_reference(ctx, cli, snapshot_cli):
    # With no body schema, omitting the body is the only request the operation can be judged on.
    app, _ = ctx.openapi.make_flask_app(
        {
            "/things": {
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {"application/json": {"schema": {"$ref": "#/components/schemas/Missing"}}},
                    },
                    "responses": {"200": {"description": "OK"}, "400": {"description": "Bad Request"}},
                }
            }
        }
    )

    @app.route("/things", methods=["POST"])
    def things():
        if not request.get_data():
            return jsonify({"error": "body required"}), 400
        return jsonify([])

    assert cli.run_openapi_app(app, "--max-examples=1") == snapshot_cli


@pytest.mark.parametrize("version", ["2.0", "3.0.2", "3.1.0"])
def test_array_form_items_tuple_in_request_body(ctx, cli, version):
    # Pydantic v1 spells tuples as `items: [...]` even in Open API 3.1 schemas.
    prefix = "#/definitions" if version == "2.0" else "#/components/schemas"
    definitions = {
        "Config": {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]},
        "Update": {
            "type": "object",
            "properties": {
                "callbacks": {
                    "type": "array",
                    "items": [{"type": "string"}, {"$ref": f"{prefix}/Config"}],
                    "minItems": 2,
                    "maxItems": 2,
                }
            },
            "required": ["callbacks"],
        },
    }
    body_schema = {"$ref": f"{prefix}/Update"}
    if version == "2.0":
        route = "/api/update"
        operation = {"parameters": [{"in": "body", "name": "body", "required": True, "schema": body_schema}]}
        extra = {"definitions": definitions}
    else:
        route = "/update"
        operation = {"requestBody": {"required": True, "content": {"application/json": {"schema": body_schema}}}}
        extra = {"components": {"schemas": definitions}}
    operation["responses"] = {"200": {"description": "OK"}}
    app, _ = ctx.openapi.make_flask_app({"/update": {"post": operation}}, version=version, **extra)
    bodies = []

    @app.route(route, methods=["POST"])
    def update():
        bodies.append(request.get_json())
        return jsonify({})

    result = cli.run_openapi_app(app, "--max-examples=10", "--mode=positive", "--phases=coverage,fuzzing")
    assert "Schema Error" not in result.stdout
    assert bodies
    for body in bodies:
        first, second = body["callbacks"]
        assert isinstance(first, str), body
        assert isinstance(second, dict), body
        assert isinstance(second["url"], str), body


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_required_body_with_one_unresolvable_media_type(ctx, cli, snapshot_cli):
    # A sibling media type that resolves keeps the operation in every phase
    app, _ = ctx.openapi.make_flask_app(
        {
            "/things": {
                "post": {
                    "parameters": [{"name": "tag", "in": "query", "schema": {"type": "string"}}],
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {"name": {"type": "string"}},
                                    "required": ["name"],
                                }
                            },
                            "application/xml": {"schema": {"$ref": "#/components/schemas/Missing"}},
                        },
                    },
                    "responses": {"200": {"description": "OK"}, "400": {"description": "Bad Request"}},
                }
            }
        }
    )

    @app.route("/things", methods=["POST"])
    def things_with_xml():
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict) or not isinstance(payload.get("name"), str):
            return jsonify({"error": "invalid body"}), 400
        return jsonify([])

    assert cli.run_openapi_app(app, "--max-examples=1") == snapshot_cli


@pytest.mark.parametrize("value", ["true", "false"])
def test_output_sanitization(ctx, cli, hypothesis_max_examples, value):
    api = ctx.openapi.apps.failure()
    auth = "secret-auth"
    result = cli.run_and_assert(
        api.schema_url,
        f"--max-examples={hypothesis_max_examples or 5}",
        "--seed=1",
        f"-H Authorization: {auth}",
        f"--output-sanitize={value}",
        exit_code=ExitCode.TESTS_FAILED,
    )

    if value == "false":
        expected = f"curl -X GET -H 'Authorization: {auth}'"
    else:
        expected = "curl -X GET -H 'Authorization: [Filtered]'"
    assert expected in result.stdout


@pytest.mark.parametrize("override", [True, False])
@pytest.mark.parametrize("enabled", [True, False])
def test_output_sanitization_via_config(ctx, cli, hypothesis_max_examples, enabled, override):
    api = ctx.openapi.apps.failure()
    auth = "secret-auth"
    args = ()
    if override:
        # Should differ from the config file
        if enabled:
            args = ("--output-sanitize=false",)
        else:
            args = ("--output-sanitize=true",)
    result = cli.run_and_assert(
        api.schema_url,
        f"--max-examples={hypothesis_max_examples or 5}",
        "--seed=1",
        f"-H Authorization: {auth}",
        *args,
        config={"output": {"sanitization": {"enabled": enabled}}},
        exit_code=ExitCode.TESTS_FAILED,
    )

    if override:
        if enabled:
            # Config enables sanitization, CLI disables it
            expected = f"curl -X GET -H 'Authorization: {auth}'"
        else:
            # Config disables sanitization, CLI enables it
            expected = "curl -X GET -H 'Authorization: [Filtered]'"
    elif enabled:
        # Config enables sanitization
        expected = "curl -X GET -H 'Authorization: [Filtered]'"
    else:
        # Config disables sanitization
        expected = f"curl -X GET -H 'Authorization: {auth}'"
    assert expected in result.stdout, result.stdout


@pytest.mark.flaky(reruns=4)
def test_multiple_failures_in_single_check(ctx, app_runner, cli, snapshot_cli):
    schema_path = ctx.openapi.write_schema(
        {
            "/success": {
                "get": {
                    "responses": {
                        "default": {
                            "description": "text",
                            "content": {"application/json": {"schema": {"type": "integer"}}},
                        }
                    }
                },
            },
        }
    )
    app = Flask(__name__)

    @app.route("/success")
    def success():
        response = Response(b"{}")
        del response.headers["Content-Type"]
        return response

    base_url = app_runner.openapi_url(app, path="")
    assert cli.run(str(schema_path), f"--url={base_url}", "--checks=all", "--mode=positive") == snapshot_cli


@pytest.mark.flaky(reruns=4)
def test_binary_payload(ctx, cli, snapshot_cli):
    app, _ = ctx.openapi.make_flask_app(
        {
            "/binary": {
                "get": {
                    "responses": {
                        "default": {
                            "description": "text",
                            "content": {"application/octet-stream": {"schema": {"type": "string", "format": "binary"}}},
                        }
                    }
                },
            },
        }
    )

    @app.route("/binary", methods=["GET"])
    def binary_handler():
        return Response(
            b"\xa7\xf5=\x18H\xc7\xff'\xf0\xeep\x06M-RX",
            status=500,
            content_type="application/octet-stream",
        )

    assert (
        cli.run_openapi_app(
            app,
            "--exclude-checks=positive_data_acceptance",
        )
        == snapshot_cli
    )


@pytest.mark.flaky(reruns=4)
def test_long_payload(ctx, cli, snapshot_cli):
    app, _ = ctx.openapi.make_flask_app(
        {
            "/long": {
                "get": {
                    "responses": {
                        "default": {
                            "description": "text",
                            "content": {"application/json": {"schema": {"type": "array"}}},
                        }
                    }
                },
            },
        }
    )

    @app.route("/long", methods=["GET"])
    def long_handler():
        return Response(
            json.dumps(["A"] * MAX_PAYLOAD_SIZE),
            status=500,
            content_type="application/json",
        )

    assert (
        cli.run_openapi_app(
            app,
            "--exclude-checks=positive_data_acceptance",
        )
        == snapshot_cli
    )


@pytest.mark.skipif(platform.system() == "Windows", reason="Snapshot is inaccurate on Windows")
@pytest.mark.flaky(reruns=4)
def test_multiple_errors(ctx, cli, snapshot_cli):
    schema_path = ctx.openapi.write_schema(
        {
            "/test": {
                "post": {
                    "requestBody": {
                        "content": {
                            "application/octet-stream": {
                                "examples": {
                                    "first": {
                                        "value": "FIRST",
                                    }
                                },
                                "schema": {"format": "binary", "type": "string"},
                            },
                            "application/zip": {
                                "examples": {
                                    "second": {
                                        "value": "SECOND",
                                    }
                                },
                                "schema": {"format": "binary", "type": "string"},
                            },
                        },
                        "required": True,
                    },
                    "responses": {"204": {"description": "Success."}},
                }
            }
        }
    )
    assert cli.run(str(schema_path), "--url=http://127.0.0.1:1") == snapshot_cli


@pytest.mark.flaky(reruns=4)
def test_group_errors(ctx, cli, snapshot_cli):
    schema_path = ctx.openapi.write_schema(
        {
            "/test": {
                "post": {
                    "requestBody": {
                        "content": {
                            "application/x-json-smile": {
                                "schema": {
                                    "properties": {
                                        "user_id": {
                                            "example": 1,
                                            "type": "integer",
                                        },
                                    },
                                    "required": ["user_id"],
                                }
                            },
                            "application/x-csv": {
                                "schema": {
                                    "properties": {
                                        "user_id": {
                                            "example": 1,
                                            "type": "integer",
                                        },
                                    },
                                    "required": ["user_id"],
                                }
                            },
                        }
                    },
                    "responses": {"204": {"description": "Success."}},
                }
            }
        }
    )
    # Unexpected-method negatives need no body, so they would reach the unreachable URL and add a network error.
    assert (
        cli.run(
            str(schema_path),
            "--url=http://127.0.0.1:1",
            config={"phases": {"coverage": {"unexpected-methods": []}}},
        )
        == snapshot_cli
    )


@pytest.mark.flaky(reruns=4)
def test_complex_urlencoded_example(ctx, cli, snapshot_cli):
    api = ctx.openapi.apps.success()
    schema_path = ctx.openapi.write_schema(
        {
            "/test": {
                "post": {
                    "requestBody": {
                        "content": {
                            "invalid": {"schema": {"example": 1}},
                            "application/x-www-form-urlencoded": {
                                "schema": {
                                    "example": [
                                        {
                                            "tag": "0",
                                            "timestamp": "2016-04-07T19:39:18Z",
                                            "url": "http://127.0.0.1:8001",
                                        },
                                        {"tag": "1", "url": "http://127.0.0.1:8002"},
                                        {
                                            "tag": "2",
                                            "timestamp": "2016-04-07T19:39:18Z",
                                            "url": "http://127.0.0.1:8003",
                                        },
                                    ],
                                    "items": {
                                        "properties": {
                                            "closest": {
                                                "enum": ["either", "after", "before"],
                                                "type": "string",
                                            },
                                            "tag": {
                                                "type": "string",
                                            },
                                            "timestamp": {
                                                "type": "string",
                                            },
                                            "url": {"type": "string"},
                                        },
                                        "required": ["url"],
                                        "type": "object",
                                    },
                                    "type": "array",
                                }
                            },
                        }
                    },
                    "responses": {"204": {"description": "Success."}},
                }
            }
        }
    )
    assert (
        cli.run(str(schema_path), f"--url={api.base_url}/api", "--phases=examples", "--checks=not_a_server_error")
        == snapshot_cli
    )


@pytest.fixture
def custom_strings(ctx):
    with ctx.check(
        """
@schemathesis.check
def custom_strings(ctx, response, case):
    if not isinstance(case.body, str):
        return
    try:
        case.body.encode("ascii")
    except Exception as exc:
        raise AssertionError(str(exc))
    assert "\\x00" not in case.body
"""
    ) as module:
        yield module


def test_custom_strings(ctx, cli, hypothesis_max_examples, custom_strings):
    api = ctx.openapi.apps.plain_text_body()
    result = cli.main(
        "run",
        "-c",
        "custom_strings",
        "--generation-allow-x00=false",
        "--generation-codec=ascii",
        api.schema_url,
        f"--max-examples={hypothesis_max_examples or 100}",
        hooks=custom_strings,
    )
    assert result.exit_code == ExitCode.OK, result.stdout


@pytest.fixture
def verify_overrides(ctx):
    with ctx.check(
        """
@schemathesis.check
def verify_overrides(ctx, response, case):
    if "key" in case.operation.path_parameters:
        assert case.path_parameters["key"] == "foo"
        assert "id" not in (case.query or {}), "`id` is present"
    if "id" in case.operation.query:
        assert case.query["id"] == "bar"
        assert "key" not in (case.path_parameters or {}), "`key` is present"
"""
    ) as module:
        yield module


def test_parameter_overrides(ctx, cli, verify_overrides):
    api = ctx.openapi.apps.path_variable_and_custom_format()
    result = cli.main(
        "run",
        "-c",
        "verify_overrides",
        "--phases=fuzzing",
        api.schema_url,
        hooks=verify_overrides,
        config={"parameters": {"key": "foo", "id": "bar"}},
    )
    assert result.exit_code == ExitCode.OK, result.stdout


@pytest.fixture
def verify_configured_headers(ctx):
    with ctx.hook(
        """
@schemathesis.hook
def before_call(ctx, case, kwargs):
    assert {key: case.headers.get(key) for key in ("User-Agent", "X-Key")} == {"User-Agent": "ok", "X-Key": "v"}
"""
    ) as module:
        yield module


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_configured_user_agent_in_case_headers_before_call(ctx, cli, snapshot_cli, verify_configured_headers):
    api = ctx.openapi.apps.success()
    assert (
        cli.main(
            "run",
            "--phases=coverage,fuzzing",
            "--max-examples=1",
            api.schema_url,
            hooks=verify_configured_headers,
            config={"headers": {"User-Agent": "ok", "X-Key": "v"}},
        )
        == snapshot_cli
    )


def test_header_parameter_override_with_configured_headers(ctx, cli):
    api = ctx.openapi.apps.header_constraint_bug()
    cli.run(
        api.schema_url,
        "--phases=fuzzing",
        "--mode=positive",
        "--max-examples=5",
        config={"headers": {"X-Custom": "value"}, "parameters": {"X-Token": "ABCDEFGH"}},
    )
    assert {
        (request.headers.get("X-Token"), request.headers.get("X-Custom"))
        for request in api.requests
        if request.path.startswith("/api/")
    } == {("ABCDEFGH", "value")}


@pytest.mark.parametrize(
    ["args", "config"],
    (
        (
            ("--max-redirects=5",),
            {},
        ),
        (
            (),
            {"max-redirects": 5},
        ),
        (
            ("--max-redirects=0",),
            {},
        ),
    ),
)
def test_max_redirects(cli, app_runner, ctx, snapshot_cli, args, config):
    raw_schema = {
        "openapi": "3.0.0",
        "info": {"title": "Redirect Test", "version": "1.0.0"},
        "paths": {
            "/redirect": {
                "get": {
                    "responses": {
                        "302": {"description": "Redirect"},
                        "200": {
                            "description": "Success",
                        },
                    },
                }
            }
        },
    }

    app = ctx.openapi.make_flask_app_from_schema(raw_schema)

    @app.route("/redirect", methods=["GET"])
    def redirect_endpoint():
        # Infinite loop
        return redirect(url_for("redirect_endpoint"), code=302)

    assert (
        cli.main(
            "run",
            "--phases=fuzzing",
            "--max-examples=1",
            app_runner.openapi_url(app),
            *args,
            config=config,
        )
        == snapshot_cli
    )


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost"])
@pytest.mark.snapshot(replace_reproduce_with=True)
def test_cross_host_redirect_not_followed(cli, app_runner, ctx, snapshot_cli, host):
    third_party = Flask("third_party")
    third_party_requests = []

    @third_party.route("/authorize")
    def authorize():
        third_party_requests.append(request.url)
        return jsonify({})

    third_party_port = app_runner.run_flask_app(third_party)
    app, _ = ctx.openapi.make_flask_app(
        {"/redirect": {"get": {"responses": {"200": {"description": "OK"}}}}},
    )

    @app.route("/redirect")
    def redirect_endpoint():
        return Response(status=303, headers={"Location": f"http://{host}:{third_party_port}/authorize"})

    assert (
        cli.run_openapi_app(app, "--phases=fuzzing", "--max-examples=1", "--checks=status_code_conformance")
        == snapshot_cli
    )
    assert third_party_requests == []


@pytest.mark.parametrize("location", ["absolute", "relative"])
def test_same_host_redirect_followed(cli, ctx, location):
    target_requests = []
    app, _ = ctx.openapi.make_flask_app(
        {"/redirect": {"get": {"responses": {"200": {"description": "OK"}}}}},
    )

    @app.route("/redirect")
    def redirect_endpoint():
        return redirect(url_for("target", _external=location == "absolute"), code=303)

    @app.route("/target")
    def target():
        target_requests.append(request.method)
        return jsonify({})

    result = cli.run_openapi_app(app, "--phases=fuzzing", "--max-examples=1", "--checks=status_code_conformance")
    assert result.exit_code == ExitCode.OK, result.stdout
    assert target_requests == ["GET"]


def test_documented_redirect_not_followed(cli, ctx):
    target_requests = []
    app, _ = ctx.openapi.make_flask_app(
        {"/redirect": {"get": {"responses": {"302": {"description": "Found"}}}}},
    )

    @app.route("/redirect")
    def redirect_endpoint():
        return redirect(url_for("target"), code=302)

    @app.route("/target")
    def target():
        target_requests.append(request.method)
        return jsonify({}), 404

    result = cli.run_openapi_app(app, "--phases=fuzzing", "--max-examples=1", "--checks=status_code_conformance")
    assert result.exit_code == ExitCode.OK, result.stdout
    assert target_requests == []


@pytest.fixture
def no_null_bytes(ctx):
    with ctx.check(
        r"""
@schemathesis.check
def no_null_bytes(ctx, response, case):
    assert "\x00" not in case.headers.get("X-KEY", {})
"""
    ) as module:
        yield module


def test_null_byte_in_header_probe(ctx, cli, snapshot_cli, no_null_bytes):
    api = ctx.openapi.apps.success()
    schema_path = ctx.openapi.write_schema(
        {
            "/success": {
                "get": {
                    "parameters": [{"name": "X-KEY", "in": "header", "required": True, "schema": {"type": "string"}}],
                    "responses": {"200": {"description": "OK"}},
                }
            }
        }
    )
    assert (
        cli.main(
            "run",
            str(schema_path),
            "-c",
            "no_null_bytes",
            f"--url={api.base_url}/api",
            "--max-examples=1",
            hooks=no_null_bytes,
        )
        == snapshot_cli
    )


def test_slow_probe_response_does_not_adapt_generation(ctx, cli):
    app, _ = ctx.openapi.make_flask_app(
        {
            "/items/{name}": {
                "get": {
                    "parameters": [
                        {"name": "name", "in": "path", "required": True, "schema": {"type": "string", "enum": ["\\"]}}
                    ],
                    "responses": {"200": {"description": "OK"}},
                }
            }
        }
    )
    names = []

    @app.before_request
    def delay_probes():
        if "X-Schemathesis-Probe" in request.headers:
            time.sleep(1)

    @app.route("/items/<path:name>")
    def items(name):
        names.append(name)
        return jsonify({})

    lines = cli.run_openapi_app(
        app, "--phases=fuzzing", "--max-examples=1", "--request-timeout=0.5"
    ).stdout.splitlines()
    assert [line.strip() for line in lines if "✘" in line] == []
    assert set(names) == {"\\"}


def test_probe_waits_as_long_as_test_requests_by_default(ctx, cli):
    app, _ = ctx.openapi.make_flask_app({"/items": {"get": {"responses": {"200": {"description": "OK"}}}}})

    @app.before_request
    def delay_probe():
        if "X-Schemathesis-Probe-Null" in request.headers:
            time.sleep(3)

    lines = cli.run_openapi_app(app, "--phases=examples").stdout.splitlines()
    assert [line.split() for line in lines if "NULL byte" in line] == [
        ["Supports", "NULL", "byte", "in", "headers:", "✓"]
    ]


@pytest.mark.skipif(sys.version_info >= (3, 13), reason="Error message is different")
def test_malformed_schema(testdir, cli, snapshot_cli, ctx):
    api = ctx.openapi.apps.success()
    schema_path = testdir.makefile(
        ".json",
        schema="""
{
   "swagger": "2.0",
}
    """,
    )
    assert cli.main("run", str(schema_path), f"--url={api.base_url}/api", "--max-examples=1") == snapshot_cli


@pytest.mark.skipif(platform.system() == "Windows", reason="Fails on Windows due to recursion")
def test_recursive_reference_error_message(ctx, cli, schema_with_recursive_references, snapshot_cli):
    api = ctx.openapi.apps.success()
    schema_path = ctx.makefile(schema_with_recursive_references)
    assert cli.run(str(schema_path), f"--url={api.base_url}/api", "--mode=positive") == snapshot_cli


@pytest.mark.skipif(platform.system() == "Windows", reason="May behave differently on Windows")
def test_empty_reference_does_not_cause_infinite_recursion(ctx, cli, snapshot_cli):
    # Empty $ref should be gracefully skipped during bundling, not cause RecursionError
    api = ctx.openapi.apps.success()
    schema_path = ctx.openapi.write_schema(
        {
            "/items": {
                "put": {
                    "parameters": [{"in": "body", "name": "body", "schema": {"$ref": "#/definitions/Connection"}}],
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
        version="2.0",
        definitions={
            "Connection": {
                "allOf": [{"$ref": "#/definitions/Resource"}],
                "properties": {
                    "key": {
                        "properties": {
                            "key": {
                                "$ref": ""  # Empty reference - should be skipped
                            }
                        }
                    }
                },
            },
            "Resource": {},
        },
    )
    assert cli.run(str(schema_path), f"--url={api.base_url}/api", "--phases=examples") == snapshot_cli


def test_nullable_reference(ctx, cli, app_runner, snapshot_cli):
    paths = {
        "/test": {
            "get": {
                "parameters": [
                    {
                        "in": "query",
                        "name": "q",
                        "schema": {
                            "$ref": "#/components/schemas/MessageStatus",
                            "nullable": True,
                        },
                    }
                ],
                "responses": {"default": {"description": "Ok"}},
            }
        }
    }
    components = {"schemas": {"MessageStatus": {}}}
    schema = ctx.openapi.build_schema(paths, components=components)
    app = ctx.openapi.make_permissive_flask_app(schema)
    base_url = app_runner.openapi_url(app, path="")
    schema_path = ctx.openapi.write_schema(paths, components=components)
    assert cli.run(str(schema_path), f"--url={base_url}/api", "--mode=positive", "--phases=fuzzing") == snapshot_cli


def test_reference_in_examples(ctx, cli, snapshot_cli):
    api = ctx.openapi.apps.success()
    schema_path = ctx.openapi.write_schema(
        {
            "/test": {
                "post": {
                    "parameters": [
                        {"$ref": "#components/parameters/metadata"},
                        {"$ref": "#components/parameters/applicationId"},
                    ]
                }
            }
        },
        components={
            "parameters": {
                "applicationId": {
                    "example": 0,
                    "in": "header",
                    "name": "xd",
                    "schema": {},
                },
                "metadata": {
                    "content": {"text/plain": {"schema": {"$ref": "t"}}},
                    "in": "header",
                    "name": "xa",
                },
            }
        },
    )
    assert cli.run(str(schema_path), f"--url={api.base_url}/api", "--phases=examples") == snapshot_cli


@pytest.mark.skipif(sys.version_info >= (3, 13), reason="Traceback is different")
def test_unknown_schema_error(ctx, cli, snapshot_cli):
    api = ctx.openapi.apps.payload()
    module = ctx.write_pymodule(
        r"""
import schemathesis

@schemathesis.metric
def buggy(ctx):
    raise AssertionError("Something bad happen")
"""
    )
    assert (
        cli.main(
            "run",
            api.schema_url,
            "--generation-maximize=buggy",
            "-c not_a_server_error",
            hooks=module,
        )
        == snapshot_cli
    )


def test_custom_cli_option(ctx, cli, snapshot_cli):
    api = ctx.openapi.apps.success()
    module = ctx.write_pymodule(
        r"""
from schemathesis import cli, engine


group = cli.add_group("My custom group")
group.add_option("--custom-counter", type=int)

group = cli.add_group("Another group", index=-1)
group.add_option("--custom-counter-2", type=int)

def gen():
    yield "first"
    yield "second"


@cli.handler()
class EventCounter(cli.EventHandler):
    def __init__(self, *args, **params):
        self.counter = params["custom_counter"] or 0

    def handle_event(self, ctx, event) -> None:
        self.counter += 1
        if isinstance(event, cli.LoadingFinished):
            ctx.add_initialization_line("Counter initialized!")
            ctx.add_initialization_line(gen())
        elif isinstance(event, engine.events.EngineFinished):
            ctx.add_summary_line(f"Counter: {self.counter}")
            ctx.add_summary_line(gen())
"""
    )
    assert (
        cli.main(
            "run",
            api.schema_url,
            "--custom-counter=42",
            "--max-examples=1",
            hooks=module,
        )
        == snapshot_cli
    )


def test_custom_handler_report_listed_and_shutdown_error_keeps_exit_code(ctx, cli):
    # A reporter failing at shutdown must not mask the run's verdict.
    @schemathesis.cli.handler()
    class BrokenShutdown(schemathesis.cli.EventHandler):
        def handle_event(self, run_ctx, event) -> None:
            if isinstance(event, events.EngineFinished):
                run_ctx.add_report("Schema coverage", "coverage.html")

        def shutdown(self, run_ctx) -> None:
            raise RuntimeError("oops")

    api = ctx.openapi.apps.success()
    result = cli.run(api.schema_url, "--max-examples=1")
    assert result.exit_code == ExitCode.OK, result.stdout
    assert "CLI Handler Error" in result.stdout
    lines = result.stdout.splitlines()
    start = lines.index("Reports:")
    assert lines[start : start + 2] == ["Reports:", "  - Schema coverage: coverage.html"]


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_custom_handler_start_error(ctx, cli, snapshot_cli):
    shutdown_exit_codes = []

    @schemathesis.cli.handler()
    class BrokenStart(schemathesis.cli.EventHandler):
        def start(self, run_ctx) -> None:
            raise ValueError("broken start")

        def shutdown(self, run_ctx) -> None:
            shutdown_exit_codes.append(run_ctx.exit_code)

    api = ctx.openapi.apps.success()
    assert cli.run(api.schema_url, "--max-examples=1") == snapshot_cli
    assert shutdown_exit_codes == [2]


@pytest.mark.parametrize(
    ["ordering_mode", "expected"],
    [
        pytest.param("none", ["GET /users/{id}", "DELETE /users/{id}", "POST /users", "GET /users"], id="none"),
        # auto mode: Layer 0 (sorted): GET /users, POST /users -> Layer 1: GET /users/{id} -> Layer 2: DELETE /users/{id}
        pytest.param("auto", ["POST /users", "GET /users", "GET /users/{id}", "DELETE /users/{id}"], id="auto"),
    ],
)
def test_operation_ordering(ctx, cli, app_runner, ordering_mode, expected):
    spec = ctx.openapi.build_schema(
        {
            "/users/{id}": {
                "get": {
                    "operationId": "getUser",
                    "parameters": [{"in": "path", "name": "id", "required": True, "schema": {"type": "integer"}}],
                    "responses": {
                        "200": {
                            "description": "OK",
                            "content": {
                                "application/json": {
                                    "schema": {"type": "object", "properties": {"id": {"type": "integer"}}}
                                }
                            },
                        }
                    },
                },
                "delete": {
                    "operationId": "deleteUser",
                    "parameters": [{"in": "path", "name": "id", "required": True, "schema": {"type": "integer"}}],
                    "responses": {"204": {"description": "No Content"}},
                },
            },
            "/users": {
                "post": {
                    "operationId": "createUser",
                    "responses": {
                        "201": {
                            "description": "Created",
                            "content": {
                                "application/json": {
                                    "schema": {"type": "object", "properties": {"id": {"type": "integer"}}}
                                }
                            },
                            "links": {
                                "GetUser": {
                                    "operationId": "getUser",
                                    "parameters": {"id": "$response.body#/id"},
                                },
                                "DeleteUser": {
                                    "operationId": "deleteUser",
                                    "parameters": {"id": "$response.body#/id"},
                                },
                            },
                        }
                    },
                },
                "get": {"operationId": "listUsers", "responses": {"200": {"description": "OK"}}},
            },
        },
        version="3.0.0",
    )
    app = Flask("schemathesis_test")

    @app.route("/openapi.json")
    def openapi_spec():
        return app.response_class(response=json.dumps(spec, sort_keys=False), status=200, mimetype="application/json")

    @app.route("/users/<int:user_id>", methods=["GET"])
    def get_user(user_id):
        return jsonify({"id": user_id})

    @app.route("/users/<int:user_id>", methods=["DELETE"])
    def delete_user(user_id):
        return "", 204

    @app.route("/users", methods=["GET", "POST"])
    def users():
        return jsonify({"success": True}), 200 if request.method == "GET" else 201

    module = ctx.write_pymodule(
        """
from schemathesis import cli, engine

@cli.handler()
class OperationOrderTracker(cli.EventHandler):
    def __init__(self, *args, **params):
        self.operations = []

    def handle_event(self, ctx, event):
        if isinstance(event, engine.events.ScenarioFinished):
            self.operations.append(event.label)
        elif isinstance(event, engine.events.EngineFinished):
            ctx.add_summary_line(f"OPERATION_ORDER: {','.join(self.operations)}")
        """
    )

    result = cli.run(
        app_runner.openapi_url(app),
        "--max-examples=1",
        "--workers=1",
        "--continue-on-failure",
        "--phases=fuzzing",
        hooks=module,
        config={"phases": {"fuzzing": {"operation-ordering": ordering_mode}}},
    )

    for line in result.stdout.split("\n"):
        if line.startswith("OPERATION_ORDER:"):
            actual_order = line.split(":", 1)[1].strip().split(",")
            break
    else:
        raise AssertionError("OPERATION_ORDER marker not found in output")

    assert actual_order == expected, (
        f"Operation order mismatch for mode={ordering_mode}\nExpected: {expected}\nActual:   {actual_order}"
    )


@pytest.mark.skipif(platform.system() == "Windows", reason="Requires a more complex test setup")
def test_rediscover_the_same_failure_in_different_phases_and_store_junit(ctx, cli, tmp_path, snapshot_cli):
    # See GH-2814
    api = ctx.openapi.apps.users_crud()
    report_dir = tmp_path / "reports"
    with ctx.check(
        r"""
@schemathesis.check
def always_fails(ctx, response, case):
    if case.operation.label == "GET /users/{user_id}":
        raise AssertionError("Failed!")
"""
    ) as module:
        assert (
            cli.main(
                "run",
                api.schema_url,
                "-c",
                "always_fails",
                "--max-examples=1",
                f"--report-dir={report_dir}",
                f"--report-junit-path={report_dir}/junit.xml",
                "--report=junit",
                "--mode=positive",
                hooks=module,
            )
            == snapshot_cli
        )


@pytest.mark.parametrize(
    ("report", "message"),
    [
        ("html", "Could not create directory '{path}': Not a directory"),
        ("junit", "Could not create parent directory for '{path}': Not a directory"),
    ],
)
def test_unwritable_report_dir_names_option_and_full_path(ctx, cli, tmp_path, report, message):
    api = ctx.openapi.apps.success()
    parent = tmp_path / "file"
    parent.write_text("", encoding="utf-8")

    result = cli.run(api.schema_url, f"--report={report}", f"--report-dir={parent / 'y'}")

    assert result.exit_code == 2
    path = str(parent / "y" / f"{report}-TIMESTAMP{'.xml' if report == 'junit' else ''}")
    assert re.sub(r"\d{8}T\d{6}Z", "TIMESTAMP", result.stderr) == (
        "Usage: schemathesis run [OPTIONS] LOCATION\n"
        "Try 'schemathesis run --help' for help.\n\n"
        f"Error: Invalid value for '--report-dir': {message.format(path=path)}\n"
    )


STOP_LISTENING = """
import socket


def stop_listening(server):
    # `shutdown` refuses the next connection right away. Closing instead keeps completing handshakes for up to
    # a second and resetting them, which reads as a crash rather than an outage - but macOS rejects `shutdown`
    # on a listening socket, so closing is the only option there.
    try:
        server.socket.shutdown(socket.SHUT_RDWR)
    except OSError:
        server.socket.close()
"""


CRASHING_SERVER = (
    STOP_LISTENING
    + """
import json
import os
import struct
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MODE = os.environ["CRASH_MODE"]
# Answered collections before a large one may crash the server, so the requests leading up to it are known.
CRASH_AFTER = int(os.environ.get("CRASH_AFTER", "0"))
ADDRESS = ("127.0.0.1", int(os.environ["PORT"]))
SCHEMA = {
    "openapi": "3.0.0",
    "info": {"title": "Crashing server", "version": "1.0.0"},
    "paths": {
        "/collections": {
            "post": {
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "properties": {"size": {"type": "integer", "minimum": 0, "maximum": 100000}},
                                "required": ["size"],
                                "additionalProperties": False,
                            }
                        }
                    },
                },
                "responses": {
                    "201": {
                        "description": "Created",
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {"id": {"type": "integer"}},
                                    "required": ["id"],
                                }
                            }
                        },
                        "links": {
                            "GetCollection": {
                                "operationId": "getCollection",
                                "parameters": {"id": "$response.body#/id"},
                            }
                        },
                    }
                },
            }
        },
        "/collections/{id}": {
            "get": {
                "operationId": "getCollection",
                "parameters": [{"name": "id", "in": "path", "required": True, "schema": {"type": "integer"}}],
                "responses": {"200": {"description": "OK"}},
            }
        },
        "/health": {"get": {"responses": {"200": {"description": "OK"}}}},
        "/points": {"get": {"responses": {"200": {"description": "OK"}}}},
    },
}
KILLED = False
CREATED = 0


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        self.reset_after_crash()
        if self.path == "/openapi.json":
            self.send_json(200, SCHEMA)
        else:
            self.send_json(200, {"id": 1})

    def do_POST(self):
        global CREATED, KILLED
        # Reading the body first keeps the reset after the client's last write, so it always sees the reset.
        payload = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        self.reset_after_crash()
        try:
            body = json.loads(payload)
        except ValueError:
            body = None
        size = body.get("size") if isinstance(body, dict) else None
        if KILLED or CREATED < CRASH_AFTER or not isinstance(size, int) or size <= 50000:
            CREATED += 1
            self.send_json(201, {"id": 1})
            return
        KILLED = True
        if MODE not in ("reset", "proxy"):
            # Stop listening before answering: the next request is refused, never queued.
            stop_listening(self.server)
        self.send_json(201, {"id": 1})
        if MODE == "refuse":
            os._exit(1)
        if MODE == "flap":
            threading.Thread(target=self.server.shutdown).start()

    def handle(self):
        if KILLED and MODE == "proxy":
            # A port proxy in front of a dead process accepts every connection, reads the request, then resets it.
            # Reading the body too keeps the reset after the client's last write, so it always sees the reset.
            length = 0
            while (line := self.rfile.readline()) not in (b"\\r\\n", b"\\n", b""):
                name, _, value = line.partition(b":")
                if name.strip().lower() == b"content-length":
                    length = int(value)
            self.rfile.read(length)
            self.rfile.close()
            self.connection.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
            self.connection.close()
            return
        super().handle()

    def reset_after_crash(self):
        # The process dies while this connection is queued: the client sees a reset, then refusals.
        if KILLED and MODE == "reset":
            stop_listening(self.server)
            self.connection.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
            os._exit(1)

    def send_json(self, status, payload):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        self.wfile.flush()


server = ThreadingHTTPServer(ADDRESS, Handler)
try:
    server.serve_forever()
except OSError:
    # A closed listening socket ends the loop the same way an explicit shutdown does.
    pass
# Only a flapping server gets here: it listens again after a short outage.
server.server_close()
time.sleep(0.5)
ThreadingHTTPServer(ADDRESS, Handler).serve_forever()
"""
)


@pytest.mark.skipif(platform.system() == "Windows", reason="Requires extra setup on Windows")
@pytest.mark.skipif(platform.python_implementation() == "PyPy", reason="PyPy behaves differently")
@pytest.mark.parametrize(
    "mode",
    ["refuse", "reset", "flap", "proxy"],
    ids=["refused", "reset-then-refused", "back-after-refusal", "reset-by-proxy"],
)
def test_server_stops_accepting_connections(subprocess_runner, cli, snapshot_cli, mode):
    port = subprocess_runner.run_app(CRASHING_SERVER, env={"CRASH_MODE": mode})
    assert (
        cli.main(
            "run",
            f"http://127.0.0.1:{port}/openapi.json",
            "--phases=coverage,fuzzing",
            "--checks=not_a_server_error",
            "--mode=positive",
            "--max-examples=10",
        )
        == snapshot_cli
    )


@pytest.mark.skipif(platform.system() == "Windows", reason="Requires extra setup on Windows")
@pytest.mark.skipif(platform.python_implementation() == "PyPy", reason="PyPy behaves differently")
@pytest.mark.snapshot(replace_reproduce_with=True, replace_phase_statistic=True)
@pytest.mark.parametrize("phase", ["fuzzing", "stateful"])
def test_server_stops_accepting_connections_in_generated_phases(subprocess_runner, cli, snapshot_cli, phase):
    port = subprocess_runner.run_app(CRASHING_SERVER, env={"CRASH_MODE": "refuse", "CRASH_AFTER": "5"})
    result = cli.main(
        "run",
        f"http://127.0.0.1:{port}/openapi.json",
        f"--phases={phase}",
        "--checks=not_a_server_error",
        "--mode=positive",
        "--seed=1",
    )
    assert result == snapshot_cli
    assert "🚫 server stopped responding" in result.stdout.partition("ERRORS")[0]
    # Generated bodies differ across Python versions; the listed request must still be the one that crashed it.
    assert int(re.findall(r"""'\{"size": (\d+)\}'""", result.stdout.split("before it went away:")[1])[0]) > 50000


@pytest.mark.skipif(platform.system() == "Windows", reason="Requires extra setup on Windows")
@pytest.mark.skipif(platform.python_implementation() == "PyPy", reason="PyPy behaves differently")
@pytest.mark.snapshot(replace_reproduce_with=True, replace_phase_statistic=True)
def test_crash_found_before_the_outage_is_still_reported_per_operation(subprocess_runner, cli, snapshot_cli):
    # The reset that killed the server is this operation's finding; the refusals that follow are the run's.
    port = subprocess_runner.run_app(CRASHING_SERVER, env={"CRASH_MODE": "reset", "CRASH_AFTER": "5"})
    assert (
        cli.main(
            "run",
            f"http://127.0.0.1:{port}/openapi.json",
            "--phases=fuzzing",
            "--checks=not_a_server_error",
            "--mode=positive",
        )
        == snapshot_cli
    )


@pytest.mark.skipif(platform.system() == "Windows", reason="Requires extra setup on Windows")
@pytest.mark.skipif(platform.python_implementation() == "PyPy", reason="PyPy behaves differently")
def test_fuzz_stops_when_server_stops_accepting_connections(subprocess_runner, cli):
    port = subprocess_runner.run_app(CRASHING_SERVER, env={"CRASH_MODE": "refuse", "CRASH_AFTER": "5"})
    result = cli.main(
        "fuzz",
        f"http://127.0.0.1:{port}/openapi.json",
        "--checks=not_a_server_error",
        "--mode=positive",
        "--max-time=20",
    )
    assert "Stop reason: Server stopped responding" in result.stdout, result.stdout
    assert "Runtime Error" not in result.stdout, result.stdout
    # The request that crashed the server leads the list.
    assert int(re.findall(r"""'\{"size": (\d+)\}'""", result.stdout.split("before it went away:")[1])[0]) > 50000


ERROR_THEN_GONE_SERVER = (
    STOP_LISTENING
    + """
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ADDRESS = ("127.0.0.1", int(os.environ["PORT"]))
SCHEMA = b'{"openapi": "3.0.0", "info": {"title": "t", "version": "1"}, "paths": {"/data": {"get": {"responses": {"200": {"description": "OK"}}}}}}'


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        if self.path != "/data":
            self.send(200, SCHEMA)
            return
        # Stop listening before answering: the next request is refused, never queued.
        stop_listening(self.server)
        threading.Thread(target=self.server.shutdown).start()
        self.send(500, b"{}")

    def send(self, status, body):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        self.wfile.flush()


server = ThreadingHTTPServer(ADDRESS, Handler)
try:
    server.serve_forever()
except OSError:
    pass
threading.Event().wait()
"""
)


@pytest.mark.skipif(platform.system() == "Windows", reason="Requires extra setup on Windows")
@pytest.mark.skipif(platform.python_implementation() == "PyPy", reason="PyPy behaves differently")
def test_fuzz_reports_failure_answered_right_before_the_server_went_away(subprocess_runner, cli):
    port = subprocess_runner.run_app(ERROR_THEN_GONE_SERVER)
    result = cli.main(
        "fuzz",
        f"http://127.0.0.1:{port}/openapi.json",
        "--checks=not_a_server_error",
        "--workers=1",
        "--max-time=20",
    )
    assert "Stop reason: Server stopped responding" in result.stdout, result.stdout
    assert "Server error" in result.stdout, result.stdout


ONE_SHOT_SERVER = (
    STOP_LISTENING
    + """
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ADDRESS = ("127.0.0.1", int(os.environ["PORT"]))
SCHEMA = {
    "openapi": "3.0.0",
    "info": {"title": "One shot", "version": "1.0.0"},
    "paths": {"/data": {"get": {"security": [{"bearerAuth": []}], "responses": {"200": {"description": "OK"}}}}},
    "components": {"securitySchemes": {"bearerAuth": {"type": "http", "scheme": "bearer"}}},
}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        if self.path == "/openapi.json":
            self.send_json(SCHEMA)
            return
        if self.path.startswith("/data"):
            # Stop listening before answering: the next request is refused, never queued.
            stop_listening(self.server)
            threading.Thread(target=self.server.shutdown).start()
        self.send_json({"ok": True})

    def send_json(self, payload):
        body = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        self.wfile.flush()


server = ThreadingHTTPServer(ADDRESS, Handler)
try:
    server.serve_forever()
except OSError:
    pass
# Stay up with nothing listening, so every further connection is refused rather than queued.
threading.Event().wait()
"""
)


@pytest.mark.skipif(platform.system() == "Windows", reason="Requires extra setup on Windows")
@pytest.mark.skipif(platform.python_implementation() == "PyPy", reason="PyPy behaves differently")
def test_answered_case_survives_a_refused_check_request(subprocess_runner, cli, snapshot_cli):
    # `ignored_auth` sends its own request after the case was answered; losing that answer to the
    # refusal would drop a tested operation from the report.
    port = subprocess_runner.run_app(ONE_SHOT_SERVER)
    assert (
        cli.main(
            "run",
            f"http://127.0.0.1:{port}/openapi.json",
            "--phases=fuzzing",
            "--checks=ignored_auth",
            "--header",
            "Authorization: Bearer secret",
            "--max-examples=1",
            "--mode=positive",
        )
        == snapshot_cli
    )


DIES_AFTER_FIRST_OPERATION_SERVER = (
    STOP_LISTENING
    + """
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ADDRESS = ("127.0.0.1", int(os.environ["PORT"]))
PARAMETER = {"name": "q", "in": "query", "required": True, "schema": {"type": "integer"}, "example": 1}
SCHEMA = {
    "openapi": "3.0.0",
    "info": {"title": "Dies after first operation", "version": "1.0.0"},
    "paths": {
        "/first": {"get": {"parameters": [PARAMETER], "responses": {"200": {"description": "OK"}}}},
        "/second": {"get": {"parameters": [PARAMETER], "responses": {"200": {"description": "OK"}}}},
    },
}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        if self.path == "/openapi.json":
            self.send_json(SCHEMA)
            return
        if not self.path.startswith("/first"):
            self.send_json({"ok": True})
            return
        # Stop listening before answering: the next request is refused, never queued.
        stop_listening(self.server)
        self.send_json({"ok": True})
        os._exit(1)

    def send_json(self, payload):
        body = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        self.wfile.flush()


ThreadingHTTPServer(ADDRESS, Handler).serve_forever()
"""
)


@pytest.mark.skipif(platform.system() == "Windows", reason="Requires extra setup on Windows")
@pytest.mark.skipif(platform.python_implementation() == "PyPy", reason="PyPy behaves differently")
@pytest.mark.snapshot(replace_reproduce_with=True)
def test_operation_refused_on_its_first_request_is_skipped(subprocess_runner, cli, snapshot_cli):
    port = subprocess_runner.run_app(DIES_AFTER_FIRST_OPERATION_SERVER)
    assert (
        cli.main(
            "run",
            f"http://127.0.0.1:{port}/openapi.json",
            "--phases=examples",
            "--checks=not_a_server_error",
            "--workers=1",
        )
        == snapshot_cli
    )


SLOW_AND_DYING_SERVER = (
    STOP_LISTENING
    + """
import json
import os
import struct
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ADDRESS = ("127.0.0.1", int(os.environ["PORT"]))
PROXY = os.environ.get("PROXY") == "1"
PARAMETER = {"name": "q", "in": "query", "required": True, "schema": {"type": "integer"}}
SCHEMA = {
    "openapi": "3.0.0",
    "info": {"title": "Slow and dying", "version": "1.0.0"},
    "paths": {
        "/slow": {"get": {"parameters": [PARAMETER], "responses": {"200": {"description": "OK"}}}},
        "/kill": {"get": {"parameters": [PARAMETER], "responses": {"200": {"description": "OK"}}}},
    },
}
killed = threading.Event()
slow_started = threading.Event()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def handle(self):
        if PROXY and killed.is_set():
            # A port proxy in front of a dead process may also read the request and close without a response.
            while self.rfile.readline() not in (b"\\r\\n", b"\\n", b""):
                pass
            return
        super().handle()

    def reset(self):
        self.connection.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))

    def do_GET(self):
        if self.path == "/openapi.json":
            self.send_json(SCHEMA)
            return
        if self.path.startswith("/slow"):
            # Hold this worker's request open until well past the outage and its confirmation probe.
            slow_started.set()
            killed.wait(timeout=10)
            if PROXY:
                # The proxy drops this connection too, but only once the outage is already confirmed.
                time.sleep(2)
                self.reset()
                return
            time.sleep(2)
            self.send_json({"ok": True})
            return
        if self.path.startswith("/kill") and not killed.is_set():
            # Stop listening before answering: the next request is refused, never queued. The slow request
            # is served by then, so the outage cannot reset it while it still waits to be accepted.
            slow_started.wait(timeout=10)
            if not PROXY:
                stop_listening(self.server)
                threading.Thread(target=self.server.shutdown).start()
            killed.set()
        self.send_json({"ok": True})

    def send_json(self, payload):
        body = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        self.wfile.flush()


server = ThreadingHTTPServer(ADDRESS, Handler)
try:
    server.serve_forever()
except OSError:
    pass
# Stay up with nothing listening, so every further connection is refused rather than queued.
threading.Event().wait()
"""
)


@pytest.mark.skipif(platform.system() == "Windows", reason="Requires extra setup on Windows")
@pytest.mark.skipif(platform.python_implementation() == "PyPy", reason="PyPy behaves differently")
@pytest.mark.snapshot(replace_reproduce_with=True)
@pytest.mark.parametrize(
    ("phase", "extra"),
    [("fuzzing", ("--mode=positive", "--max-examples=5")), ("coverage", ())],
    ids=["fuzzing", "coverage"],
)
def test_workers_share_one_outage_verdict(subprocess_runner, cli, snapshot_cli, phase, extra):
    # The worker still waiting on a response stops with the run instead of reporting its own refusal.
    port = subprocess_runner.run_app(SLOW_AND_DYING_SERVER)
    result = cli.main(
        "run",
        f"http://127.0.0.1:{port}/openapi.json",
        f"--phases={phase}",
        "--checks=not_a_server_error",
        "--workers=2",
        *extra,
    )
    assert result == snapshot_cli
    # Generated queries differ across Python versions; the request still in flight must still lead the list.
    reported = result.stdout.split("before it went away:")[1]
    assert reported.index("/slow") < reported.index("/kill")


@pytest.mark.skipif(platform.system() == "Windows", reason="Requires extra setup on Windows")
@pytest.mark.skipif(platform.python_implementation() == "PyPy", reason="PyPy behaves differently")
@pytest.mark.snapshot(replace_reproduce_with=True)
def test_workers_reset_after_the_outage_report_no_error_of_their_own(subprocess_runner, cli, snapshot_cli):
    port = subprocess_runner.run_app(SLOW_AND_DYING_SERVER, env={"PROXY": "1"})
    assert (
        cli.main(
            "run",
            f"http://127.0.0.1:{port}/openapi.json",
            "--phases=fuzzing",
            "--checks=not_a_server_error",
            "--workers=2",
            "--mode=positive",
            "--max-examples=5",
        )
        == snapshot_cli
    )


RESETTING_SERVER = """
import json
import os
import socket
import ssl
import struct
import time
from http.server import BaseHTTPRequestHandler, HTTPServer, ThreadingHTTPServer
from urllib.parse import urlsplit

MODE = os.environ["RESET_MODE"]
# Killed by its very first request, the server never answers one that could prove it was up.
PATHS = ["/reset"] if MODE == "kill" else ["/ok", "/reset"]
SCHEMA = {
    "openapi": "3.0.0",
    "info": {"title": "Resetting server", "version": "1.0.0"},
    "paths": {path: {"get": {"responses": {"200": {"description": "OK"}}}} for path in PATHS},
}
RESETS = 0


def reset(connection):
    connection.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
    connection.close()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        global RESETS
        # A proxy receives the absolute URL of the target.
        path = urlsplit(self.path).path
        # Twice, since a request dropped on a reused connection is sent once more.
        if path == "/reset" and RESETS < 2:
            RESETS += 1
            if MODE == "kill":
                # The process dies mid-request: this connection is reset, every later one is refused.
                self.server.socket.close()
                self.connection.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
                os._exit(1)
            # The socket is only released once the handler's reader lets go of it.
            self.rfile.close()
            if MODE == "tls":
                # TLS reports a reset as a reset or as a closed connection depending on timing; a clean close does not.
                self.connection.close()
            else:
                reset(self.connection)
            if MODE == "busy" and RESETS == 2:
                # Single-threaded: new connections wait in the backlog, unanswered, while the worker restarts.
                time.sleep(5)
            return
        body = json.dumps(SCHEMA if path == "/openapi.json" else {}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def finish(self):
        try:
            super().finish()
        except OSError:
            pass


class TlsServer(ThreadingHTTPServer):
    def get_request(self):
        context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
        context.load_cert_chain(os.environ["CERT_PATH"])
        while True:
            connection, address = self.socket.accept()
            # Anything but a TLS handshake is dropped without a word.
            if connection.recv(1, socket.MSG_PEEK) == b"\\x16":
                return context.wrap_socket(connection, server_side=True), address
            connection.close()


ADDRESS = ("127.0.0.1", int(os.environ["PORT"]))
if MODE == "never-started":
    # A port proxy with nothing behind it: every connection is accepted, then reset.
    listener = socket.create_server(ADDRESS)
    while True:
        connection = listener.accept()[0]
        # After the request, so the reset never lands while the connection is still being set up.
        connection.recv(65536)
        reset(connection)
server_class = {"busy": HTTPServer, "tls": TlsServer}.get(MODE, ThreadingHTTPServer)
server_class(ADDRESS, Handler).serve_forever()
"""


def run_resetting_server(subprocess_runner, mode, **env):
    port = subprocess_runner.run_app(RESETTING_SERVER, env={"RESET_MODE": mode, **env}, wait_for_server=False)
    wait_for_port(port)
    return port


@pytest.mark.skipif(platform.system() == "Windows", reason="Requires extra setup on Windows")
@pytest.mark.skipif(platform.python_implementation() == "PyPy", reason="PyPy behaves differently")
@pytest.mark.snapshot(replace_reproduce_with=True)
@pytest.mark.parametrize("mode", ["alive", "busy"], ids=["still-answering", "still-holding-connections"])
def test_reset_by_a_live_server_is_not_an_outage(subprocess_runner, cli, snapshot_cli, mode):
    port = run_resetting_server(subprocess_runner, mode)
    assert cli.main("run", f"http://127.0.0.1:{port}/openapi.json", "--phases=examples,fuzzing") == snapshot_cli


@pytest.mark.skipif(platform.system() == "Windows", reason="Requires extra setup on Windows")
@pytest.mark.skipif(platform.python_implementation() == "PyPy", reason="PyPy behaves differently")
@pytest.mark.snapshot(replace_reproduce_with=True)
def test_reset_by_a_tls_server_dropping_plaintext_is_not_an_outage(subprocess_runner, cli, snapshot_cli, tmp_path):
    cert_path = tmp_path / "server.pem"
    trustme.CA().issue_cert("127.0.0.1").private_key_and_cert_chain_pem.write_to_path(str(cert_path))
    port = run_resetting_server(subprocess_runner, "tls", CERT_PATH=str(cert_path))
    assert (
        cli.main("run", f"https://127.0.0.1:{port}/openapi.json", "--phases=examples,fuzzing", "--tls-verify=false")
        == snapshot_cli
    )


@pytest.mark.skipif(platform.system() == "Windows", reason="Requires extra setup on Windows")
@pytest.mark.skipif(platform.python_implementation() == "PyPy", reason="PyPy behaves differently")
@pytest.mark.snapshot(replace_reproduce_with=True)
def test_reset_by_a_configured_proxy_is_not_an_outage(ctx, subprocess_runner, cli, snapshot_cli):
    # The API is reachable only through the proxy, so a direct probe cannot tell anything about it.
    port = run_resetting_server(subprocess_runner, "proxy")
    schema_path = ctx.openapi.write_schema(
        {path: {"get": {"responses": {"200": {"description": "OK"}}}} for path in ("/ok", "/reset")}
    )
    assert (
        cli.main(
            "run",
            str(schema_path),
            "--url=http://api.invalid",
            f"--proxy=http://127.0.0.1:{port}",
            "--phases=examples,fuzzing",
        )
        == snapshot_cli
    )


@pytest.mark.skipif(platform.system() == "Windows", reason="Requires extra setup on Windows")
@pytest.mark.skipif(platform.python_implementation() == "PyPy", reason="PyPy behaves differently")
@pytest.mark.snapshot(replace_reproduce_with=True)
def test_app_never_started_behind_a_port_proxy_is_not_an_outage(ctx, subprocess_runner, cli, snapshot_cli):
    port = run_resetting_server(subprocess_runner, "never-started")
    schema_path = ctx.openapi.write_schema(
        {path: {"get": {"responses": {"200": {"description": "OK"}}}} for path in ("/ok", "/reset")}
    )
    assert (
        cli.main("run", str(schema_path), f"--url=http://127.0.0.1:{port}", "--phases=examples,fuzzing") == snapshot_cli
    )


@pytest.mark.skipif(platform.system() == "Windows", reason="Requires extra setup on Windows")
@pytest.mark.skipif(platform.python_implementation() == "PyPy", reason="PyPy behaves differently")
@pytest.mark.snapshot(replace_reproduce_with=True)
def test_server_killed_by_its_first_request_is_an_outage(subprocess_runner, cli, snapshot_cli):
    port = run_resetting_server(subprocess_runner, "kill")
    result = cli.main("run", f"http://127.0.0.1:{port}/openapi.json", "--phases=examples,fuzzing")
    assert result == snapshot_cli
    assert "/reset" in result.stdout.split("before it went away:")[1]


@pytest.mark.skipif(platform.system() == "Windows", reason="Requires extra setup on Windows")
@pytest.mark.skipif(platform.python_implementation() == "PyPy", reason="PyPy behaves differently")
@pytest.mark.snapshot(replace_traceback=True)
def test_operation_keeps_its_own_error_when_the_server_goes_away(ctx, subprocess_runner, cli, snapshot_cli):
    port = subprocess_runner.run_app(ONE_SHOT_SERVER)
    with ctx.check(
        """
@schemathesis.check
def boom(ctx, response, case):
    raise ValueError("check blew up")
"""
    ) as module:
        assert (
            cli.main(
                "run",
                f"http://127.0.0.1:{port}/openapi.json",
                "--phases=fuzzing",
                "--checks=boom",
                "--header",
                "Authorization: Bearer secret",
                "--max-examples=2",
                "--mode=positive",
                hooks=module,
            )
            == snapshot_cli
        )


@pytest.mark.skipif(platform.system() == "Windows", reason="Requires extra setup on Windows")
@pytest.mark.skipif(platform.python_implementation() == "PyPy", reason="PyPy behaves differently")
def test_app_crash(subprocess_runner, cli, snapshot_cli):
    app = """
import os
from flask import Flask, jsonify

app = Flask(__name__)

raw_schema = {
    "openapi": "3.0.0",
    "info": {"title": "Crash Test", "version": "1.0.0"},
    "paths": {"/crash": {"get": {"responses": {"200": {"description": "Won't return"}}}}},
}

@app.route("/openapi.json")
def openapi():
    return jsonify(raw_schema)

@app.get("/crash")
def crash():
    os._exit(1)

if __name__ == "__main__":
    port = int(os.environ["PORT"])
    app.run(host="127.0.0.1", port=port, debug=False)
"""

    port = subprocess_runner.run_app(app)

    assert (
        cli.main("run", f"http://127.0.0.1:{port}/openapi.json", "--phases=coverage", "--tls-verify=false")
        == snapshot_cli
    )


@pytest.mark.skipif(platform.system() == "Windows", reason="Requires extra setup on Windows")
@pytest.mark.skipif(platform.python_implementation() == "PyPy", reason="PyPy behaves differently")
def test_partial_response(subprocess_runner, cli, snapshot_cli):
    app = """
import os
import sys
from flask import Flask, jsonify, Response
import time

app = Flask(__name__)

raw_schema = {
    "openapi": "3.0.0",
    "info": {"title": "Partial Response Test", "version": "1.0.0"},
    "paths": {"/crash": {"get": {"responses": {"200": {"description": "Won't return"}}}}},
}

@app.route("/openapi.json")
def openapi():
    return jsonify(raw_schema)

@app.get("/crash")
def crash():
    def generate():
        yield '{"partial":'
        sys.stdout.flush()
        # Force connection reset while client expects more data
        os._exit(1)

    return Response(generate(), mimetype='application/json')

if __name__ == "__main__":
    port = int(os.environ["PORT"])
    app.run(host="127.0.0.1", port=port, debug=False)
"""

    port = subprocess_runner.run_app(app)

    assert cli.main("run", f"http://127.0.0.1:{port}/openapi.json", "--tls-verify=false") == snapshot_cli


@pytest.mark.skipif(platform.system() == "Windows", reason="Requires extra setup on Windows")
@pytest.mark.snapshot(replace_phase_statistic=True)
@pytest.mark.skipif(platform.python_implementation() == "PyPy", reason="PyPy behaves differently")
def test_stateful_crash(subprocess_runner, cli, snapshot_cli):
    app = """
import os
from flask import Flask, jsonify, request

app = Flask(__name__)

raw_schema = {
    "openapi": "3.0.0",
    "info": {"title": "Stateful Crash Test", "version": "1.0.0"},
    "paths": {
        "/users": {
            "post": {
                "summary": "Create user",
                "requestBody": {
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "properties": {"name": {"type": "string", "enum": ["alice"]}},
                                "required": ["name"]
                            }
                        }
                    }
                },
                "responses": {
                    "201": {
                        "description": "User created",
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {"id": {"type": "integer"}},
                                    "required": ["id"]
                                }
                            }
                        },
                        "links": {
                            "GetUserById": {
                                "operationId": "getUserById",
                                "parameters": {"id": "$response.body#/id"}
                            }
                        }
                    }
                }
            }
        },
        "/users/{id}": {
            "get": {
                "operationId": "getUserById",
                "summary": "Get user by ID",
                "parameters": [
                    {
                        "name": "id",
                        "in": "path",
                        "required": True,
                        "schema": {"type": "integer"}
                    }
                ],
                "responses": {
                    "200": {
                        "description": "User details",
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "id": {"type": "integer"},
                                        "name": {"type": "string"}
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
    }
}

@app.route("/openapi.json")
def openapi():
    return jsonify(raw_schema)

@app.route("/users", methods=["POST"])
def create_user():
    return jsonify({"id": 123}), 201

@app.route("/users/<int:user_id>", methods=["GET"])
def get_user(user_id):
    # Crash during stateful testing when following the link
    os._exit(1)

if __name__ == "__main__":
    port = int(os.environ["PORT"])
    app.run(host="127.0.0.1", port=port, debug=False)
"""

    port = subprocess_runner.run_app(app)

    assert (
        cli.main(
            "run",
            f"http://127.0.0.1:{port}/openapi.json",
            "--phases=stateful",
            "--tls-verify=false",
            "-c not_a_server_error",
            "--max-examples=1",
            "--mode=positive",
        )
        == snapshot_cli
    )


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_request_retries_option(cli, ctx, snapshot_cli):
    # Server drops first 2 /users connections, succeeds on 3rd.
    # Without --request-retries=2 the run would fail; with it, it passes.
    paths = {"/users": {"get": {"responses": {"200": {"description": "OK"}}}}}
    spec_bytes = json.dumps(ctx.openapi.build_schema(paths)).encode()
    attempt = [0]

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.close_connection = True
            if self.path == "/openapi.json":
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(spec_bytes)))
                self.end_headers()
                self.wfile.write(spec_bytes)
            elif self.path.startswith("/users"):
                attempt[0] += 1
                if attempt[0] > 2:
                    body = b"[]"
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                # else: close without response → ConnectionError → retry
            else:
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", "2")
                self.end_headers()
                self.wfile.write(b"{}")

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        assert (
            cli.run(
                f"http://127.0.0.1:{port}/openapi.json",
                "--request-retries=2",
                "--max-examples=1",
                "--checks=response_schema_conformance",
            )
            == snapshot_cli
        )
    finally:
        server.shutdown()


# `GET /beta` is skipped because its phases are disabled; the examples-phase reason belongs to tested `GET /alpha`.
@pytest.mark.snapshot(replace_reproduce_with=True)
def test_skip_reasons_not_borrowed_from_tested_operations(ctx, cli, snapshot_cli):
    app, _ = ctx.openapi.make_flask_app(
        {
            "/alpha": {"get": {"responses": {"200": {"description": "OK"}}}},
            "/beta": {"get": {"responses": {"200": {"description": "OK"}}}},
        }
    )

    @app.route("/alpha")
    def alpha():
        return jsonify({})

    @app.route("/beta")
    def beta():
        return jsonify({})

    assert (
        cli.run_openapi_app(
            app,
            "--phases=examples,fuzzing",
            "--max-examples=1",
            config={
                "operations": [
                    {
                        "include-name": "GET /beta",
                        "phases": {"examples": {"enabled": False}, "fuzzing": {"enabled": False}},
                    }
                ]
            },
        )
        == snapshot_cli
    )


def test_repeated_phase_keeps_its_executed_status():
    # Under `--max-time` a phase runs once per cycle; a later cycle with no budget left must not
    # rewrite the summary to say the phase never ran.
    ctx = ExecutionContext(config=SchemathesisConfig().projects.get_default())
    phase = Phase(name=PhaseName.STATEFUL_TESTING, is_enabled=True, skip_reason=None)
    for status in (Status.SUCCESS, Status.SKIP):
        ctx.on_event(events.PhaseFinished(phase=phase, status=status, payload=None))

    assert ctx.phases[PhaseName.STATEFUL_TESTING][0] == Status.SUCCESS


@pytest.mark.parametrize(
    ("version", "extra", "args", "config"),
    [
        ("3.0.2", {"servers": [{"url": "/api"}]}, ["--origin={base_url}"], None),
        ("2.0", {"basePath": "/api"}, ["--origin={base_url}"], None),
        ("3.0.2", {"servers": [{"url": "/api"}]}, [], {"origin": "{base_url}"}),
        (
            "3.0.2",
            {"servers": [{"url": "/api"}]},
            ["--origin={base_url}"],
            {"base-url": "http://127.0.0.1:1/nope"},
        ),
        ("3.0.2", {"servers": [{"url": "/nope"}]}, ["--url={base_url}/api"], {"origin": "{base_url}"}),
    ],
    ids=[
        "openapi-3",
        "swagger-2",
        "origin-from-config",
        "origin-overrides-config-base-url",
        "url-overrides-config-origin",
    ],
)
@pytest.mark.snapshot(replace_reproduce_with=True)
def test_origin_resolution(ctx, cli, app_runner, snapshot_cli, version, extra, args, config):
    app, _ = ctx.openapi.make_flask_app(
        {"/success": {"get": {"responses": {"200": {"description": "OK"}}}}}, version=version, **extra
    )
    handlers.register_success(app)
    base_url = app_runner.openapi_url(app, path="")
    if config is not None:
        config = {key: value.format(base_url=base_url) for key, value in config.items()}
    assert (
        cli.run(
            f"{base_url}/openapi.json",
            *(arg.format(base_url=base_url) for arg in args),
            "--max-examples=1",
            config=config,
        )
        == snapshot_cli
    )


@pytest.mark.parametrize(
    "servers",
    [
        {"url": "/"},
        ["/"],
        [{"url": 1}],
    ],
    ids=["object", "non-object-entry", "non-string-url"],
)
@pytest.mark.snapshot(replace_reproduce_with=True)
def test_invalid_servers(ctx, cli, snapshot_cli, servers):
    app, _ = ctx.openapi.make_flask_app(
        {"/success": {"get": {"responses": {"200": {"description": "OK"}}}}}, servers=servers
    )
    assert cli.run_openapi_app(app) == snapshot_cli


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_origin_satisfies_the_base_url_requirement_for_files(ctx, cli, app_runner, snapshot_cli):
    app, _ = ctx.openapi.make_flask_app(
        {"/success": {"get": {"responses": {"200": {"description": "OK"}}}}}, servers=[{"url": "/api"}]
    )

    @app.route("/api/success")
    def success():
        return jsonify({"success": True})

    schema_path = ctx.openapi.write_schema(
        {"/success": {"get": {"responses": {"200": {"description": "OK"}}}}}, servers=[{"url": "/api"}]
    )
    origin = app_runner.openapi_url(app, path="")
    assert cli.run(str(schema_path), f"--origin={origin}", "--max-examples=1") == snapshot_cli


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_origin_with_path_is_rejected(ctx, cli, app_runner, snapshot_cli):
    app, _ = ctx.openapi.make_flask_app({"/success": {"get": {"responses": {"200": {"description": "OK"}}}}})
    base_url = app_runner.openapi_url(app, path="")
    assert cli.run(f"{base_url}/openapi.json", f"--origin={base_url}/api") == snapshot_cli


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_origin_conflicts_with_url(ctx, cli, app_runner, snapshot_cli):
    app, _ = ctx.openapi.make_flask_app({"/success": {"get": {"responses": {"200": {"description": "OK"}}}}})
    base_url = app_runner.openapi_url(app, path="")
    assert cli.run(f"{base_url}/openapi.json", f"--origin={base_url}", f"--url={base_url}") == snapshot_cli


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_ctrl_c_in_custom_handler_after_probing(ctx, cli, snapshot_cli):
    @schemathesis.cli.handler()
    class Interrupter(schemathesis.cli.EventHandler):
        def handle_event(self, run_ctx, event) -> None:
            if isinstance(event, events.PhaseFinished) and event.phase.name == PhaseName.PROBING:
                raise KeyboardInterrupt

    api = ctx.openapi.apps.success()
    assert cli.run(api.schema_url, "--max-examples=1") == snapshot_cli


@pytest.mark.snapshot(replace_reproduce_with=True)
@pytest.mark.parametrize("payload", [b"not json", b"\xff\xfe\x00"], ids=["malformed", "binary"])
def test_link_extraction_from_invalid_json_response(ctx, cli, snapshot_cli, payload):
    app, _ = ctx.openapi.make_flask_app(
        {
            "/items": {
                "post": {
                    "operationId": "createItem",
                    "responses": {
                        "201": {
                            "description": "Created",
                            "content": {"application/json": {"schema": {"type": "object"}}},
                            "links": {
                                "GetItem": {"operationId": "getItem", "parameters": {"id": "$response.body#/id"}}
                            },
                        }
                    },
                }
            },
            "/items/{id}": {
                "get": {
                    "operationId": "getItem",
                    "parameters": [{"name": "id", "in": "path", "required": True, "schema": {"type": "integer"}}],
                    "responses": {"200": {"description": "OK"}},
                }
            },
        }
    )

    @app.route("/items", methods=["POST"])
    def create_item():
        return Response(payload, status=201, content_type="application/json")

    @app.route("/items/<int:item_id>", methods=["GET"])
    def get_item(item_id):
        return jsonify({})

    assert (
        cli.run_openapi_app(
            app,
            "--phases=stateful",
            "--max-examples=5",
            "-c not_a_server_error",
            config={"phases": {"stateful": {"inference": {"algorithms": []}}}},
        )
        == snapshot_cli
    )


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_network_error_reported_when_suite_ends_with_failure(ctx, cli, snapshot_cli):
    app, _ = ctx.openapi.make_flask_app(linked_users())
    counter = count()
    handlers.register_linked_users_create(app)

    @app.route("/users/<int:user_id>")
    def get_user(user_id):
        if next(counter) == 0:

            def broken_body():
                yield '{"id": '
                raise RuntimeError("broken body")

            return Response(broken_body())
        return "", 404

    assert (
        cli.run_openapi_app(
            app,
            "--phases=stateful",
            "--max-examples=10",
            "--seed=4",
            "--checks=status_code_conformance",
        )
        == snapshot_cli
    )


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_stateful_flaky_replay_keeps_network_error(ctx, cli, snapshot_cli):
    app, _ = ctx.openapi.make_flask_app(linked_users())
    counter = count()
    handlers.register_linked_users_create(app)

    @app.route("/users/<int:user_id>")
    def get_user(user_id):
        if next(counter) == 0:

            def broken_body():
                yield '{"id": '
                raise RuntimeError("broken body")

            return Response(broken_body())
        return jsonify({"id": user_id}), 200

    assert cli.run_openapi_app(app, "--phases=stateful", "--seed=4", "--checks=not_a_server_error") == snapshot_cli


@pytest.mark.snapshot(replace_test_cases=False, replace_reproduce_with=True)
def test_max_time_does_not_count_unsent_stateful_case(ctx, cli, snapshot_cli, monkeypatch):
    # The hook moves the engine clock past the deadline; restore the real one afterwards
    monkeypatch.setattr(control, "time", time)
    api = ctx.openapi.apps.linked_users()

    module = ctx.write_pymodule(
        """
import time

from schemathesis.engine import control

draws = 0


class ExpiredClock:
    @staticmethod
    def monotonic():
        return time.monotonic() + 3600


def expire_before_second_case(case):
    global draws
    draws += 1
    if draws == 2:
        control.time = ExpiredClock
    return case


@schemathesis.hook
def before_generate_case(context, strategy):
    return strategy.map(expire_before_second_case)
"""
    )

    assert (
        cli.run(
            api.schema_url,
            "--max-time=60",
            "--max-examples=5",
            "--generation-database=none",
            "--phases=stateful",
            "--checks=not_a_server_error",
            hooks=module,
        )
        == snapshot_cli
    )


@pytest.mark.snapshot(replace_test_cases=False, replace_generated=True, replace_reproduce_with=True)
def test_stateful_step_rejected_for_unresponsive_operation_is_not_counted(ctx, cli, snapshot_cli):
    app, _ = ctx.openapi.make_flask_app(linked_users())
    handlers.register_linked_users_create(app)

    @app.route("/users/<int:user_id>")
    def get_user(user_id):
        # Outlasts the request timeout every time, so the operation is flagged unresponsive.
        time.sleep(1)
        return jsonify({"id": user_id})

    assert (
        cli.run_openapi_app(
            app,
            "--max-examples=5",
            "--generation-deterministic",
            "--phases=stateful",
            "--checks=not_a_server_error",
            # Only the hanging operation gets a short timeout, so a slow runner never times out the answering one.
            config={"operations": [{"include-name": "GET /users/{userId}", "request-timeout": 0.2}]},
        )
        == snapshot_cli
    )


@pytest.mark.snapshot(replace_test_cases=False, replace_reproduce_with=True)
def test_stateful_step_skipped_as_already_passed_is_not_counted(ctx, cli, snapshot_cli):
    api = ctx.openapi.apps.linked_users()

    assert (
        cli.run(
            api.schema_url,
            "--max-examples=1",
            "--generation-deterministic",
            "--generation-unique-inputs",
            "--phases=stateful",
            "--checks=not_a_server_error",
        )
        == snapshot_cli
    )


# Every repeated stateful pass folds into the single block reported at the end.
@pytest.mark.snapshot(replace_cycle_metrics=True, replace_reproduce_with=True)
def test_max_time_repeats_stateful_phase(ctx, cli, snapshot_cli):
    api = ctx.openapi.apps.linked_users()

    assert (
        cli.run(
            api.schema_url,
            "--max-time=5",
            "--max-examples=5",
            "--generation-database=none",
            "--phases=stateful",
            "--checks=not_a_server_error",
        )
        == snapshot_cli
    )


@pytest.mark.parametrize("phase", ["fuzzing", "stateful"])
@pytest.mark.snapshot(replace_cycle_metrics=True, replace_reproduce_with=True)
def test_keyboard_interrupt_under_time_budget(ctx, cli, snapshot_cli, phase):
    api = ctx.openapi.apps.linked_users()

    module = ctx.write_pymodule(INTERRUPT_ON_SECOND_RESPONSE)
    assert (
        cli.run(
            api.schema_url,
            "--max-time=10",
            "--max-examples=5",
            f"--phases={phase}",
            "--checks=not_a_server_error",
            hooks=module,
        )
        == snapshot_cli
    )


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_custom_handler_error_under_time_budget(ctx, cli, snapshot_cli):
    @schemathesis.cli.handler()
    class BrokenHandler(schemathesis.cli.EventHandler):
        def handle_event(self, run_ctx, event) -> None:
            if isinstance(event, events.ScenarioFinished):
                raise AttributeError("oops")

    api = ctx.openapi.apps.success()
    assert cli.run(api.schema_url, "--max-time=10", "--phases=fuzzing") == snapshot_cli


INTERRUPT_ON_EVERY_EXAMPLES_SETUP = """
@schemathesis.hook
def before_add_examples(context, examples):
    raise KeyboardInterrupt
"""


@pytest.mark.parametrize("workers", ["1", "2"])
def test_interrupt_counts_each_operation_once(ctx, cli, workers):
    # Workers can report the same interrupt more than once.
    api = ctx.openapi.apps.success_and_failure()
    module = ctx.write_pymodule(INTERRUPT_ON_EVERY_EXAMPLES_SETUP)
    result = cli.main("run", api.schema_url, f"--workers={workers}", "--phases=examples", hooks=module)
    assert re.findall(r"⏭\s+(\d+) skipped", result.stdout) == ["2"]
