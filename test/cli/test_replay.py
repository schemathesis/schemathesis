from __future__ import annotations

import contextlib
import io
import json
import re
from pathlib import Path

import pytest
import requests
from flask import Response, jsonify, request
from rich.console import Console

import schemathesis
from schemathesis.cli.commands.replay import executor
from schemathesis.cli.commands.replay.executor import (
    ReplayOutcome,
    ReplayStatus,
    StepOutcome,
    _build_case,
)
from schemathesis.cli.commands.replay.output import render_replay
from schemathesis.cli.output import make_console
from schemathesis.config import OutputConfig
from schemathesis.core import transport
from schemathesis.core.failures import ServerError
from schemathesis.reporting.crashes import MANIFEST_FILENAME, CrashCheck, CrashFile, CrashLink, CrashStep, CrashWriter
from test.apps.catalog.openapi.modifiers.stateful import IndependentInternalError
from test.fixtures.crashes import FailingCheck, Link, LinkParameter, Step


def _write_crash(
    directory: Path,
    crash_factory,
    *,
    method: str = "GET",
    url: str,
    schema_location: str,
    path_template: str,
    status: int,
    body: str,
    check: str = "not_a_server_error",
    case_id: str = "Ab1Cd2",
) -> Path:
    base_url = url.rsplit(path_template, 1)[0]
    crash = crash_factory.single(
        method=method,
        path=path_template,
        status=status,
        body=body.encode(),
        request_url=url,
        checks=[FailingCheck(name=check, message=f"{status} error")],
        code_sample=f"curl -X {method} {url}",
    )
    crash.fingerprint = "testtest"
    crash.case_id = case_id
    writer = CrashWriter(directory=directory)
    writer.open(schema_location=schema_location, base_url=base_url)
    writer.write(crash)
    return directory / crash.filename()


def _users_app(ctx, app_runner):
    # A healthy GET /users endpoint used as the replay target for crashes recorded against it.
    app, _ = ctx.openapi.make_flask_app({"/users": {"get": {"responses": {"200": {"description": "OK"}}}}})

    @app.route("/users")
    def users():
        return jsonify([])

    schema_url = app_runner.openapi_url(app)
    return schema_url, schema_url.rsplit("/", 1)[0]


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_replay_fixed(cli, app_runner, ctx, crash_factory, tmp_path, snapshot_cli):
    schema_url, base = _users_app(ctx, app_runner)
    crash_file = _write_crash(
        tmp_path,
        crash_factory,
        url=f"{base}/users",
        schema_location=schema_url,
        path_template="/users",
        status=500,
        body='{"error": "was broken"}',
    )

    assert cli.main("replay", str(crash_file)) == snapshot_cli
    assert not crash_file.exists()


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_replay_fixed_case_with_multiple_checks_shows_single_block(
    cli, app_runner, ctx, crash_factory, tmp_path, snapshot_cli
):
    schema_url, base = _users_app(ctx, app_runner)
    crash_dir = _crashes_dir(tmp_path)
    crash_dir.mkdir(parents=True, exist_ok=True)
    for check in ("not_a_server_error", "status_code_conformance"):
        _write_crash(
            crash_dir,
            crash_factory,
            url=f"{base}/users",
            schema_location=schema_url,
            path_template="/users",
            status=500,
            body='{"error": "was broken"}',
            check=check,
            case_id="Fx1Cd2",
        )

    assert cli.main("replay", "Fx1Cd2") == snapshot_cli


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_replay_still_failing(cli, app_runner, ctx, crash_factory, tmp_path, snapshot_cli):
    app, _ = ctx.openapi.make_flask_app({"/broken": {"get": {"responses": {"500": {"description": "Error"}}}}})

    @app.route("/broken")
    def broken():
        return jsonify({"error": "still broken"}), 500

    schema_url = app_runner.openapi_url(app)
    base = schema_url.rsplit("/", 1)[0]
    crash_file = _write_crash(
        tmp_path,
        crash_factory,
        url=f"{base}/broken",
        schema_location=schema_url,
        path_template="/broken",
        status=500,
        body='{"error": "still broken"}',
    )

    assert cli.main("replay", str(crash_file)) == snapshot_cli
    assert crash_file.exists()


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_replay_response_schema_conformance_fixed(cli, app_runner, ctx, crash_factory, tmp_path, snapshot_cli):
    # An unconstrained 200 response schema always passes on replay, so the crash is reported fixed.
    app, _ = ctx.openapi.make_flask_app({"/data": {"get": {"responses": {"200": {"description": "OK"}}}}})

    @app.route("/data")
    def data():
        return jsonify({"value": "new"}), 200

    schema_url = app_runner.openapi_url(app)
    base = schema_url.rsplit("/", 1)[0]
    crash_file = _write_crash(
        tmp_path,
        crash_factory,
        url=f"{base}/data",
        schema_location=schema_url,
        path_template="/data",
        status=200,
        body='{"value": "old"}',
        check="response_schema_conformance",
    )

    assert cli.main("replay", str(crash_file)) == snapshot_cli
    assert not crash_file.exists()


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_replay_base_url_override(cli, app_runner, ctx, crash_factory, tmp_path, snapshot_cli):
    schema_url, base = _users_app(ctx, app_runner)
    # Recorded against a dead host; the live schema still loads and --url redirects traffic to it.
    _write_crash(
        tmp_path,
        crash_factory,
        url="http://nonexistent-host:9999/users",
        schema_location=schema_url,
        path_template="/users",
        status=500,
        body='{"error": "broken"}',
    )

    assert cli.main("replay", str(tmp_path), "--url", base) == snapshot_cli


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_replay_uses_recorded_base_url_without_override(cli, app_runner, ctx, crash_factory, tmp_path, snapshot_cli):
    # Without --url, a crash from a file-based schema (no servers) must replay against the manifest's base URL.
    received = []
    app, _ = ctx.openapi.make_flask_app({"/boom": {"get": {"responses": {"200": {"description": "OK"}}}}})

    @app.route("/boom")
    def boom():
        received.append(True)
        return jsonify([])

    base = app_runner.openapi_url(app).rsplit("/", 1)[0]
    schema_file = tmp_path / "openapi.json"
    schema_file.write_text(
        json.dumps(ctx.openapi.build_schema({"/boom": {"get": {"responses": {"200": {"description": "OK"}}}}}))
    )
    crash_dir = _crashes_dir(tmp_path)
    crash_dir.mkdir(parents=True, exist_ok=True)
    _write_crash(
        crash_dir,
        crash_factory,
        url=f"{base}/boom",
        schema_location=str(schema_file),
        path_template="/boom",
        status=500,
        body='{"error": "boom"}',
    )

    result = cli.main("replay", "--keep")

    assert result == snapshot_cli
    assert received == [True], result.output


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_replay_keep_flag(cli, app_runner, ctx, crash_factory, tmp_path, snapshot_cli):
    schema_url, base = _users_app(ctx, app_runner)
    crash_file = _write_crash(
        tmp_path,
        crash_factory,
        url=f"{base}/users",
        schema_location=schema_url,
        path_template="/users",
        status=500,
        body='{"error": "was broken"}',
    )

    assert cli.main("replay", str(crash_file), "--keep") == snapshot_cli
    assert crash_file.exists()


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_replay_missing_path_is_errored_not_crash(cli, app_runner, ctx, crash_factory, tmp_path, snapshot_cli):
    # An endpoint removed since recording is reported as errored, not aborted with a traceback.
    schema_url, base = _users_app(ctx, app_runner)
    crash_file = _write_crash(
        tmp_path,
        crash_factory,
        url=f"{base}/gone",
        schema_location=schema_url,
        path_template="/gone",
        status=500,
        body='{"error": "boom"}',
    )

    assert cli.main("replay", str(crash_file)) == snapshot_cli


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_replay_corrupt_meta_is_errored_not_crash(cli, app_runner, ctx, tmp_path, snapshot_cli):
    # A crash file on disk that loads fine but carries metadata the current format can't decode is reported
    # errored and kept, rather than aborting the whole run with a traceback.
    schema_url, base = _users_app(ctx, app_runner)
    crash_dir = _crashes_dir(tmp_path)
    crash_dir.mkdir(parents=True, exist_ok=True)
    CrashWriter(directory=crash_dir).open(schema_location=schema_url, base_url=base)
    (crash_dir / "corrupt.json").write_text(
        json.dumps(
            {
                "operation": "GET /users",
                "method": "GET",
                "path_template": "/users",
                "fingerprint": "metadrift",
                "case_id": "Md1234",
                "code_sample": f"curl {base}/users",
                "sequence": [
                    {
                        "method": "GET",
                        "url": f"{base}/users",
                        "url_template": "/users",
                        "headers": {},
                        "response": {"status_code": 500, "headers": {"content-type": "application/json"}, "body": "{}"},
                        "link": None,
                        "checks": [{"name": "not_a_server_error", "status": "failure", "message": "boom"}],
                        "meta": {"bogus": 1},
                        "path": "/users",
                    }
                ],
            }
        )
    )

    assert cli.main("replay", "--keep") == snapshot_cli


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_replay_default_finds_named_project_crashes(cli, app_runner, ctx, crash_factory, tmp_path, snapshot_cli):
    # Replay with no path discovers crashes stored under any project directory, not only the unnamed default.
    schema_url, base = _users_app(ctx, app_runner)
    titled_dir = tmp_path / ".schemathesis" / "test" / "cache" / "crashes"
    titled_dir.mkdir(parents=True, exist_ok=True)
    crash_file = _write_crash(
        titled_dir,
        crash_factory,
        url=f"{base}/users",
        schema_location=schema_url,
        path_template="/users",
        status=500,
        body='{"error": "was broken"}',
    )

    assert cli.main("replay") == snapshot_cli
    assert not crash_file.exists()


def test_replay_unsupported_method_crash(cli, ctx, tmp_path):
    # A crash whose method has no declared operation (e.g. TRACE) is re-sent via another operation on the path.
    app, _ = ctx.openapi.make_flask_app(
        {
            "/api/thing/{id}": {
                "get": {
                    "parameters": [{"name": "id", "in": "path", "required": True, "schema": {"type": "integer"}}],
                    "responses": {"200": {"description": "OK"}},
                }
            }
        }
    )

    @app.route("/api/thing/<id>", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "TRACE"])
    def thing(id):
        return jsonify({"ok": True})

    run = cli.run_openapi_app(app, "--phases=coverage", "-c", "unsupported_method")
    case_id = re.search(r"st replay (\S+)", run.stdout).group(1)

    result = cli.main("replay", case_id)

    assert result.exit_code == 1, result.output
    assert "Unsupported methods" in result.output
    assert "operation not found" not in result.output
    assert "TRACE /api/thing/{id}" in result.output, result.output
    assert "GET /api/thing/{id}" not in result.output, result.output


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_replay_by_case_id_removes_fixed_crash(cli, app_runner, ctx, crash_factory, tmp_path, snapshot_cli):
    # Replaying by bare case ID removes the fixed crash from its real directory, not the current one.
    schema_url, base = _users_app(ctx, app_runner)
    crash_dir = _crashes_dir(tmp_path)
    crash_dir.mkdir(parents=True, exist_ok=True)
    crash_file = _write_crash(
        crash_dir,
        crash_factory,
        url=f"{base}/users",
        schema_location=schema_url,
        path_template="/users",
        status=500,
        body='{"error": "was broken"}',
        case_id="Cd7Xy9",
    )

    assert cli.main("replay", "Cd7Xy9") == snapshot_cli
    assert not crash_file.exists()


def test_replay_applies_per_operation_headers(cli, app_runner, ctx, crash_factory, tmp_path):
    received = []
    app, _ = ctx.openapi.make_flask_app({"/protected": {"get": {"responses": {"500": {"description": "Error"}}}}})

    @app.route("/protected")
    def protected():
        received.append(request.headers.get("X-Op-Key"))
        return jsonify({"error": "boom"}), 500

    schema_url = app_runner.openapi_url(app)
    base = schema_url.rsplit("/", 1)[0]
    crash_file = _write_crash(
        tmp_path,
        crash_factory,
        url=f"{base}/protected",
        schema_location=schema_url,
        path_template="/protected",
        status=500,
        body='{"error": "boom"}',
    )

    cli.main(
        "replay",
        str(crash_file),
        "--keep",
        config={"operations": [{"include-name": "GET /protected", "headers": {"X-Op-Key": "secret"}}]},
    )

    assert received == ["secret"]


def test_build_case_drops_stale_framing_headers(ctx, crash_factory):
    # Stale framing headers (Content-Length, Host) are dropped so the re-sent request isn't misframed.
    schema = ctx.openapi.load_schema({"/users": {"get": {"responses": {"200": {"description": "OK"}}}}})
    operation = schema["/users"]["GET"]
    crash = crash_factory.single(
        path="/users",
        status=500,
        request_headers={"Content-Length": "999", "Host": "old-host", "X-Trace": "keep"},
    )

    case = _build_case(operation, crash.sequence[0])

    lowered = {k.lower() for k in case.headers}
    assert "x-trace" in lowered
    assert "content-length" not in lowered
    assert "host" not in lowered


def test_build_case_drops_stale_cookie_header(ctx, crash_factory):
    # A recorded Cookie header would override the structured cookies, so it is dropped.
    schema = ctx.openapi.load_schema({"/users": {"get": {"responses": {"200": {"description": "OK"}}}}})
    operation = schema["/users"]["GET"]
    crash = crash_factory.single(
        path="/users",
        status=500,
        request_headers={"Cookie": "session=stale", "X-Trace": "keep"},
        cookies={"session": "fresh"},
    )

    case = _build_case(operation, crash.sequence[0])

    assert "cookie" not in {k.lower() for k in case.headers}
    assert case.cookies == {"session": "fresh"}


def _crashes_dir(tmp_path: Path) -> Path:
    return tmp_path / ".schemathesis" / "default" / "cache" / "crashes"


def _list_crash_files(directory: Path) -> list[Path]:
    return sorted(f for f in directory.glob("*.json") if f.name != MANIFEST_FILENAME)


def test_replay_applies_terminal_step_link(cli, app_runner, ctx, tmp_path, crash_factory):
    # A linked step's path parameter is re-extracted from the previous response, not the stale recorded value.
    received = []
    app, _ = ctx.openapi.make_flask_app(
        {
            "/tokens": {"get": {"responses": {"200": {"description": "OK"}}}},
            "/resource/{id}": {
                "get": {
                    "parameters": [{"name": "id", "in": "path", "required": True, "schema": {"type": "string"}}],
                    "responses": {"500": {"description": "Error"}},
                }
            },
        }
    )

    @app.route("/tokens")
    def tokens():
        return jsonify({"id": "fresh-123"})

    @app.route("/resource/<id>")
    def resource(id):
        received.append(id)
        return jsonify({"error": "boom"}), 500

    schema_url = app_runner.openapi_url(app)
    base = schema_url.rsplit("/", 1)[0]

    crash_dir = _crashes_dir(tmp_path)
    crash_dir.mkdir(parents=True, exist_ok=True)
    writer = CrashWriter(directory=crash_dir)
    writer.open(schema_location=schema_url, base_url=base)
    crash = crash_factory.chain(
        steps=[
            Step(method="GET", path="/tokens", status=200, body=b'{"id": "fresh-123"}'),
            Step(
                method="GET",
                path="/resource/{id}",
                status=500,
                body=b'{"error": "boom"}',
                path_parameters={"id": "stale-999"},
                link=Link(
                    operation_id="getResource",
                    parameters=[LinkParameter(location="path", name="id", expression="$response.body#/id")],
                ),
                checks=[FailingCheck(name="not_a_server_error")],
            ),
        ]
    )
    writer.write(crash)

    cli.main("replay")

    assert received == ["fresh-123"]


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_replay_applies_non_path_link_parameter(cli, app_runner, ctx, tmp_path, crash_factory, snapshot_cli):
    # A link feeding a query parameter (not a path one) is re-extracted from the previous response.
    received = []
    app, _ = ctx.openapi.make_flask_app(
        {
            "/tokens": {"get": {"responses": {"200": {"description": "OK"}}}},
            "/resource": {
                "get": {
                    "parameters": [{"name": "token", "in": "query", "required": True, "schema": {"type": "string"}}],
                    "responses": {"500": {"description": "Error"}},
                }
            },
        }
    )

    @app.route("/tokens")
    def tokens():
        return jsonify({"token": "fresh-123"})

    @app.route("/resource")
    def resource():
        received.append(request.args.get("token"))
        return jsonify({"error": "boom"}), 500

    schema_url = app_runner.openapi_url(app)
    base = schema_url.rsplit("/", 1)[0]

    crash_dir = _crashes_dir(tmp_path)
    crash_dir.mkdir(parents=True, exist_ok=True)
    writer = CrashWriter(directory=crash_dir)
    writer.open(schema_location=schema_url, base_url=base)
    crash = crash_factory.chain(
        steps=[
            Step(method="GET", path="/tokens", status=200, body=b'{"token": "fresh-123"}'),
            Step(
                method="GET",
                path="/resource",
                status=500,
                body=b'{"error": "boom"}',
                query={"token": "stale-999"},
                link=Link(
                    operation_id="getResource",
                    parameters=[LinkParameter(location="query", name="token", expression="$response.body#/token")],
                ),
                checks=[FailingCheck(name="not_a_server_error")],
            ),
        ]
    )
    writer.write(crash)

    result = cli.main("replay")

    assert result == snapshot_cli
    assert received == ["fresh-123"]


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_replay_re_extracts_request_body_link(cli, app_runner, ctx, tmp_path, crash_factory, snapshot_cli):
    # A link feeding a request body field is re-extracted fresh, not re-sent from the stale recorded body.
    received = []
    app, _ = ctx.openapi.make_flask_app(
        {
            "/source": {"get": {"responses": {"200": {"description": "OK"}}}},
            "/target": {
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {"application/json": {"schema": {"type": "object"}}},
                    },
                    "responses": {"500": {"description": "Error"}},
                }
            },
        }
    )

    @app.route("/source")
    def source():
        return jsonify({"token": "fresh"})

    @app.route("/target", methods=["POST"])
    def target():
        received.append(request.get_json().get("token"))
        return jsonify({"error": "boom"}), 500

    schema_url = app_runner.openapi_url(app)
    base = schema_url.rsplit("/", 1)[0]
    crash_dir = _crashes_dir(tmp_path)
    crash_dir.mkdir(parents=True, exist_ok=True)
    writer = CrashWriter(directory=crash_dir)
    writer.open(schema_location=schema_url, base_url=base)
    crash = crash_factory.chain(
        steps=[
            Step(method="GET", path="/source", status=200, body=b'{"token": "fresh"}'),
            Step(
                method="POST",
                path="/target",
                status=500,
                body=b'{"error": "boom"}',
                case_body={"token": "stale"},
                media_type="application/json",
                link=Link(operation_id="target", request_body={"token": "$response.body#/token"}),
                checks=[FailingCheck(name="not_a_server_error")],
            ),
        ]
    )
    writer.write(crash)

    result = cli.main("replay", "--keep")

    assert result == snapshot_cli
    assert received == ["fresh"]


def test_replay_reproduces_recorded_unit_failures(cli, ctx, tmp_path):
    api = ctx.openapi.apps.failure_multiple_failures_unsatisfiable()

    cli.run(api.schema_url, "--max-examples=3")

    crash_files = _list_crash_files(_crashes_dir(tmp_path))
    assert crash_files, "Expected `schemathesis run` to record at least one crash"

    result = cli.main("replay", "--keep")

    assert result.exit_code == 1
    assert f"{len(crash_files)} failed" in result.output
    assert "FIXED" not in result.output
    assert "CHANGED" not in result.output
    assert "ERROR" not in result.output


def test_replay_by_case_id(cli, ctx, tmp_path):
    api = ctx.openapi.apps.failure()

    cli.run(api.schema_url)

    crash_files = _list_crash_files(_crashes_dir(tmp_path))
    assert crash_files

    data = json.loads(crash_files[0].read_text())
    case_id = data["case_id"]

    result = cli.main("replay", case_id, "--keep")

    assert result.exit_code == 1
    assert "1 case" in result.output


def test_replay_by_case_id_replays_all_checks(cli, ctx, tmp_path):
    # A case failing several checks is merged into a single block with one ID and all checks, not one per check.
    api = ctx.openapi.apps.multiple_failures()

    cli.run(api.schema_url)

    crash_files = _list_crash_files(_crashes_dir(tmp_path))
    case_ids = [json.loads(f.read_text())["case_id"] for f in crash_files]
    shared = next((cid for cid in case_ids if case_ids.count(cid) > 1), None)
    assert shared is not None, "Expected a case that failed multiple checks"
    count = case_ids.count(shared)

    result = cli.main("replay", shared, "--keep")

    assert result.exit_code == 1
    assert "1 case" in result.output
    assert f"{count} failed" in result.output
    assert result.output.count(f"Test Case ID: {shared}") == 1


def test_replay_failure_order_matches_run(cli, app_runner, ctx, tmp_path):
    # Replay orders a case's checks by severity like a run, not by crash-file name.
    app, _ = ctx.openapi.make_flask_app(
        {
            "/boom": {
                "get": {
                    "responses": {
                        "500": {"description": "Error", "content": {"application/json": {"schema": {"type": "object"}}}}
                    }
                }
            }
        }
    )

    @app.route("/boom")
    def boom():
        return Response('{"error": "boom"}', status=500, content_type="text/plain")

    run = cli.run(app_runner.openapi_url(app))

    crash_files = _list_crash_files(_crashes_dir(tmp_path))
    case_ids = [json.loads(f.read_text())["case_id"] for f in crash_files]
    shared = next((cid for cid in case_ids if case_ids.count(cid) > 1), None)
    assert shared is not None, run.output

    def titles(output: str) -> list[str]:
        block = output.split(f"Test Case ID: {shared}", 1)[1].split("\n[", 1)[0]
        return re.findall(r"^- (\S.*?)(?:  \(\d+ violations\))?$", block, re.MULTILINE)

    run_titles = titles(run.output)
    assert run_titles[0] == "Server error", run.output

    result = cli.main("replay", shared, "--keep")

    assert titles(result.output) == run_titles, result.output


def test_timing_only_failure_not_recorded_or_hinted(cli, ctx, tmp_path):
    # A timing-only failure is not recorded as a crash and the run output adds no replay hint for a missing file.
    api = ctx.openapi.apps.success()

    result = cli.run(api.schema_url, "--max-response-time=0.0001", "--max-examples=1")

    assert result.exit_code == 1
    assert "Response time" in result.output
    assert "st replay" not in result.output
    assert not _list_crash_files(_crashes_dir(tmp_path))


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_replay_unknown_case_id(cli, ctx, tmp_path, snapshot_cli):
    api = ctx.openapi.apps.failure()

    cli.run(api.schema_url)

    assert cli.main("replay", "ZZZZZZ") == snapshot_cli


def test_replay_kitchen_sink_at_scale(cli, ctx, tmp_path):
    api = ctx.openapi.apps.kitchen_sink()

    # /api/flaky is non-deterministic by design, so it is excluded to keep the roundtrip assertion stable.
    cli.run(api.schema_url, "--max-examples=2", "--phases=fuzzing", "--exclude-path=/api/flaky")

    crash_files = _list_crash_files(_crashes_dir(tmp_path))
    assert len(crash_files) >= 10, f"Expected many crashes from kitchen sink, got {len(crash_files)}"

    result = cli.main("replay", "--keep")

    assert result.exit_code == 1
    assert "ERROR" not in result.output
    # Most recordings reproduce; a few may drift from server-side ordering/serialization noise.
    match = re.search(r"(\d+) failed", result.output)
    assert match is not None
    failing = int(match.group(1))
    assert failing >= len(crash_files) - 4, f"{failing}/{len(crash_files)} reproduced; output:\n{result.output}"


def test_replay_stateful_chain(cli, ctx, tmp_path):
    api = ctx.openapi.apps.stateful_users(IndependentInternalError())

    # Enough examples to reliably build a multi-step linked failing scenario.
    cli.run(api.schema_url, "--max-examples=30", "--phases=stateful")

    crash_files = _list_crash_files(_crashes_dir(tmp_path))
    assert crash_files, "Expected the stateful run to record at least one crash"

    multi_step = [json.loads(f.read_text()) for f in crash_files if len(json.loads(f.read_text())["sequence"]) > 1]
    assert multi_step, "Expected at least one multi-step crash"

    result = cli.main("replay", "--keep")

    # The server always returns 500, so the chain reproduces as a server error rather than bailing.
    assert "Stateful replay is not yet supported" not in result.output
    assert "Traceback" not in result.output
    assert result.exit_code == 1, result.output
    assert "Server error" in result.output, result.output


def test_replay_re_evaluates_history_dependent_stateful_check(cli, app_runner, ctx, tmp_path, crash_factory):
    # use_after_free must re-evaluate against the replayed history, or a still-broken crash is wrongly reported fixed.
    app, _ = ctx.openapi.make_flask_app(
        {
            "/items": {"post": {"responses": {"201": {"description": "Created"}}}},
            "/items/{item_id}": {
                "delete": {
                    "parameters": [{"name": "item_id", "in": "path", "required": True, "schema": {"type": "string"}}],
                    "responses": {"200": {"description": "OK"}},
                },
                "get": {
                    "parameters": [{"name": "item_id", "in": "path", "required": True, "schema": {"type": "string"}}],
                    "responses": {"200": {"description": "OK"}},
                },
            },
        }
    )

    @app.route("/items", methods=["POST"])
    def create():
        return jsonify({"id": "x1"}), 201

    @app.route("/items/<item_id>", methods=["DELETE"])
    def delete(item_id):
        return jsonify({}), 200

    @app.route("/items/<item_id>")
    def read(item_id):
        # The bug: a deleted resource stays readable (200) instead of returning 404.
        return jsonify({"id": item_id}), 200

    schema_url = app_runner.openapi_url(app)
    crash_dir = _crashes_dir(tmp_path)
    crash_dir.mkdir(parents=True, exist_ok=True)
    writer = CrashWriter(directory=crash_dir)
    writer.open(schema_location=schema_url, base_url=schema_url.rsplit("/", 1)[0])
    extract_id = LinkParameter(location="path", name="item_id", expression="$response.body#/id")
    crash = crash_factory.chain(
        steps=[
            Step(method="POST", path="/items", status=201, body=b'{"id": "x1"}'),
            Step(
                method="DELETE",
                path="/items/{item_id}",
                status=200,
                body=b"{}",
                path_parameters={"item_id": "x1"},
                link=Link(operation_id="delete", parameters=[extract_id]),
            ),
            Step(
                method="GET",
                path="/items/{item_id}",
                status=200,
                body=b'{"id": "x1"}',
                path_parameters={"item_id": "x1"},
                link=Link(operation_id="read", parameters=[extract_id]),
                checks=[FailingCheck(name="use_after_free")],
            ),
        ]
    )
    writer.write(crash)

    result = cli.main("replay", "--keep")

    # The bug is still present, so the replayed sequence must re-trigger use_after_free, not report it fixed.
    assert result.exit_code == 1, result.output
    assert "FIXED" not in result.output, result.output


def test_replay_links_resolve_against_recorded_parent_not_previous_step(cli, app_runner, ctx, tmp_path, crash_factory):
    # A step's link must re-extract from its recorded parent, not the previous step, once a sibling is spliced in.
    created = set()
    counter = {"value": 0}
    app, _ = ctx.openapi.make_flask_app(
        {
            "/items": {"post": {"responses": {"201": {"description": "Created"}}}},
            "/items/{item_id}": {
                "delete": {
                    "parameters": [{"name": "item_id", "in": "path", "required": True, "schema": {"type": "string"}}],
                    "responses": {"200": {"description": "OK"}},
                },
                "get": {
                    "parameters": [{"name": "item_id", "in": "path", "required": True, "schema": {"type": "string"}}],
                    "responses": {"200": {"description": "OK"}},
                },
            },
        }
    )

    @app.route("/items", methods=["POST"])
    def create():
        counter["value"] += 1
        item_id = f"id-{counter['value']}"
        created.add(item_id)
        return jsonify({"id": item_id}), 201

    @app.route("/items/<item_id>", methods=["DELETE"])
    def delete(item_id):
        return (jsonify({}), 200) if item_id in created else (jsonify({}), 404)

    @app.route("/items/<item_id>")
    def read(item_id):
        # The bug: a created-then-deleted resource stays readable; an id never created returns 404.
        return (jsonify({"id": item_id}), 200) if item_id in created else (jsonify({}), 404)

    schema_url = app_runner.openapi_url(app)
    crash_dir = _crashes_dir(tmp_path)
    crash_dir.mkdir(parents=True, exist_ok=True)
    writer = CrashWriter(directory=crash_dir)
    writer.open(schema_location=schema_url, base_url=schema_url.rsplit("/", 1)[0])
    extract_id = LinkParameter(location="path", name="item_id", expression="$response.body#/id")
    crash = crash_factory.chain(
        steps=[
            Step(method="POST", path="/items", status=201, body=b'{"id": "stale-id"}'),
            Step(
                method="DELETE",
                path="/items/{item_id}",
                status=200,
                body=b"{}",
                path_parameters={"item_id": "stale-id"},
                parent=0,
                link=Link(operation_id="delete", parameters=[extract_id]),
            ),
            Step(
                method="GET",
                path="/items/{item_id}",
                status=200,
                body=b'{"id": "stale-id"}',
                path_parameters={"item_id": "stale-id"},
                parent=0,
                link=Link(operation_id="read", parameters=[extract_id]),
                checks=[FailingCheck(name="use_after_free", related_step=1)],
            ),
        ]
    )
    writer.write(crash)

    result = cli.main("replay", "--keep")

    assert result.exit_code == 1, result.output
    assert "FIXED" not in result.output, result.output


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_replay_keyboard_interrupt(cli, app_runner, ctx, crash_factory, tmp_path, snapshot_cli, monkeypatch):
    app, _ = ctx.openapi.make_flask_app({"/a": {"get": {"responses": {"500": {"description": "Error"}}}}})

    @app.route("/a")
    def endpoint_a():
        return jsonify({"error": "boom"}), 500

    schema_url = app_runner.openapi_url(app)
    base = schema_url.rsplit("/", 1)[0]
    for name in ("a", "b"):
        _write_crash(
            tmp_path,
            crash_factory,
            url=f"{base}/{name}",
            schema_location=schema_url,
            path_template=f"/{name}",
            status=500,
            body='{"error": "boom"}',
            case_id=f"Ab1Cd{name}",
        )

    # Inject the interrupt at the per-crash boundary, the only point where a real Ctrl-C surfaces faithfully.
    real_replay = executor.replay_crash_file
    call_count = 0

    def interrupt_on_second(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count > 1:
            raise KeyboardInterrupt
        return real_replay(*args, **kwargs)

    monkeypatch.setattr(executor, "replay_crash_file", interrupt_on_second)

    assert cli.main("replay", str(tmp_path), "--keep") == snapshot_cli


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_replay_interrupt_before_first_outcome(
    cli, app_runner, ctx, crash_factory, tmp_path, snapshot_cli, monkeypatch
):
    # Ctrl-C before the first crash finishes exits without rendering a partial report.
    schema_url, base = _users_app(ctx, app_runner)
    crash_file = _write_crash(
        tmp_path,
        crash_factory,
        url=f"{base}/users",
        schema_location=schema_url,
        path_template="/users",
        status=500,
        body='{"error": "boom"}',
    )

    def interrupt_immediately(*args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(executor, "replay_crash_file", interrupt_immediately)

    assert cli.main("replay", str(crash_file), "--keep") == snapshot_cli
    assert crash_file.exists()


def test_summary_not_green_when_no_outcomes_ran():
    console = Console(file=io.StringIO(), force_terminal=True, color_system="standard", width=100)
    render_replay(
        crashes=[],
        outcomes=[],
        source="x",
        base_url=None,
        duration_ms=10,
        output_config=OutputConfig(),
        incompatible_count=2,
        console=console,
    )

    assert "\x1b[1;32m" not in console.file.getvalue()


def _capture_render(
    *,
    crashes: list[CrashFile],
    outcomes: list[ReplayOutcome],
    source: str = ".schemathesis/crashes/",
    base_url: str | None = None,
    duration_ms: int = 142,
    removal_count: int = 0,
) -> str:
    # Capture both the Rich console and the `click.echo` failure output into one buffer, in order,
    # so the snapshot mirrors what the terminal actually shows.
    buffer = io.StringIO()
    console = make_console(file=buffer)
    with contextlib.redirect_stdout(buffer):
        render_replay(
            crashes=crashes,
            outcomes=outcomes,
            source=source,
            base_url=base_url,
            duration_ms=duration_ms,
            output_config=OutputConfig(),
            removal_count=removal_count,
            console=console,
        )
    return buffer.getvalue()


def test_render_unit_fixed(snapshot, crash_factory):
    crashes = [
        crash_factory.single(
            path="/users",
            status=500,
            body=b'{"error": "boom"}',
            checks=[FailingCheck(name="not_a_server_error")],
        )
    ]
    outcomes = [ReplayOutcome(status=ReplayStatus.FIXED, duration_ms=142)]
    assert _capture_render(crashes=crashes, outcomes=outcomes) == snapshot


def test_render_error_outcome(snapshot, crash_factory):
    crashes = [
        crash_factory.single(
            path="/accounts/{id}/verify",
            status=500,
            path_parameters={"id": "1"},
            checks=[FailingCheck(name="positive_data_acceptance")],
        )
    ]
    outcomes = [
        ReplayOutcome(
            status=ReplayStatus.ERRORED,
            duration_ms=62,
            error_message="extraction failed at step 2 - $response.body#/id not found",
        )
    ]
    assert _capture_render(crashes=crashes, outcomes=outcomes) == snapshot


def test_render_removal_notice(snapshot, crash_factory):
    crashes = [
        crash_factory.single(
            path="/users",
            status=500,
            body=b'{"error": "boom"}',
            checks=[FailingCheck(name="not_a_server_error")],
        )
    ]
    outcomes = [ReplayOutcome(status=ReplayStatus.FIXED, duration_ms=142)]
    assert _capture_render(crashes=crashes, outcomes=outcomes, removal_count=5) == snapshot


def test_render_step_chain(snapshot, crash_factory):
    # Step rows colour each status and flag a body change even when the recorded body is invalid JSON.
    crash = crash_factory.chain(
        steps=[
            Step(method="GET", path="/a", status=200, body=b'{"ok": 1}'),
            Step(method="GET", path="/b", status=404, body=b"{not valid json"),
            Step(method="GET", path="/d", status=200, body=b"old text", content_type="text/plain"),
            Step(
                method="GET",
                path="/c",
                status=500,
                body=b'{"error": "boom"}',
                checks=[FailingCheck(name="not_a_server_error")],
            ),
        ]
    )
    # Pin the ephemeral case id so the captured `Test Case ID` / `st replay` lines are deterministic.
    crash.case_id = "Cd1234"
    crashes = [crash]
    prepared = requests.Request(method="GET", url="http://127.0.0.1/c").prepare()
    response = transport.Response(
        status_code=503,
        headers={"content-type": ["application/json"]},
        content=b'{"error": "down"}',
        request=prepared,
        elapsed=0.1,
        verify=False,
    )
    outcomes = [
        ReplayOutcome(
            status=ReplayStatus.FAILED,
            duration_ms=120,
            failures=[ServerError(operation="GET /c", status_code=503)],
            transport_response=response,
            step_outcomes=[
                StepOutcome(302, '{"ok": 1}'),
                StepOutcome(404, '{"y": 2}'),
                StepOutcome(200, "new text"),
                StepOutcome(503, '{"error": "down"}'),
            ],
        )
    ]
    assert _capture_render(crashes=crashes, outcomes=outcomes) == snapshot


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_replay_unrunnable_check_is_not_deleted(cli, app_runner, ctx, tmp_path, crash_factory, snapshot_cli):
    schema_url, base = _users_app(ctx, app_runner)

    crash_dir = _crashes_dir(tmp_path)
    crash_dir.mkdir(parents=True, exist_ok=True)
    writer = CrashWriter(directory=crash_dir)
    writer.open(schema_location=schema_url, base_url=base)
    crash = crash_factory.single(
        path="/users",
        status=500,
        body=b'{"error": "boom"}',
        request_url=f"{base}/users",
        checks=[FailingCheck(name="check_defined_in_an_extension_we_did_not_load")],
    )
    writer.write(crash)
    crash_path = crash_dir / crash.filename()

    # An unrunnable check is reported as errored, not silently treated as fixed.
    assert cli.main("replay") == snapshot_cli
    assert crash_path.exists(), "A check we cannot re-run must never be auto-deleted"


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_replay_without_schema_bails(cli, app_runner, ctx, tmp_path, crash_factory, snapshot_cli):
    # An unloadable schema with a live 200 server would falsely mark the crash fixed and delete it without a bail.
    _, base = _users_app(ctx, app_runner)

    crash_dir = _crashes_dir(tmp_path)
    crash_dir.mkdir(parents=True, exist_ok=True)
    writer = CrashWriter(directory=crash_dir)
    # Point the schema location at a URL that serves non-schema content.
    writer.open(schema_location=f"{base}/users", base_url=base)
    crash = crash_factory.single(
        path="/users",
        status=500,
        body=b'{"error": "boom"}',
        request_url=f"{base}/users",
    )
    writer.write(crash)
    crash_path = crash_dir / crash.filename()

    assert cli.main("replay") == snapshot_cli
    assert crash_path.exists()


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_replay_applies_per_operation_check_config(cli, app_runner, ctx, crash_factory, tmp_path, snapshot_cli):
    # Per-operation expected statuses are honored on replay: a 500 the config allows is reported fixed.
    app, _ = ctx.openapi.make_flask_app({"/boom": {"get": {"responses": {"500": {"description": "Error"}}}}})

    @app.route("/boom")
    def boom():
        return jsonify({"error": "boom"}), 500

    schema_url = app_runner.openapi_url(app)
    base = schema_url.rsplit("/", 1)[0]
    crash_file = _write_crash(
        tmp_path,
        crash_factory,
        url=f"{base}/boom",
        schema_location=schema_url,
        path_template="/boom",
        status=500,
        body='{"error": "boom"}',
    )

    assert (
        cli.main(
            "replay",
            str(crash_file),
            config={
                "operations": [
                    {
                        "include-name": "GET /boom",
                        "checks": {"not_a_server_error": {"expected-statuses": ["2xx", "4xx", "500"]}},
                    }
                ]
            },
        )
        == snapshot_cli
    )
    assert not crash_file.exists()


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_replay_empty_sequence_marked_incompatible(cli, app_runner, ctx, tmp_path, snapshot_cli):
    # Foreign file the writer never emits: an empty sequence, treated as incompatible rather than crashing.
    schema_url, _ = _users_app(ctx, app_runner)
    crash_dir = _crashes_dir(tmp_path)
    crash_dir.mkdir(parents=True, exist_ok=True)
    CrashWriter(directory=crash_dir).open(schema_location=schema_url, base_url=schema_url.rsplit("/", 1)[0])
    (crash_dir / "broken_x.json").write_text(
        json.dumps(
            {
                "operation": "GET /users",
                "method": "GET",
                "path_template": "/users",
                "fingerprint": "x",
                "case_id": "y",
                "code_sample": "",
                "sequence": [],
            }
        )
    )

    assert cli.main("replay") == snapshot_cli
    # An unreadable crash file is never auto-deleted; another version may still reproduce it.
    assert (crash_dir / "broken_x.json").exists()


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_replay_unreadable_crash_file_is_skipped(cli, app_runner, ctx, tmp_path, snapshot_cli):
    # A crash file that errors on read (permissions, or vanished mid-scan) is skipped and kept, not aborted.
    schema_url, _ = _users_app(ctx, app_runner)
    crash_dir = _crashes_dir(tmp_path)
    crash_dir.mkdir(parents=True, exist_ok=True)
    CrashWriter(directory=crash_dir).open(schema_location=schema_url, base_url=schema_url.rsplit("/", 1)[0])
    # A path that matches the `*.json` scan but raises OSError on read.
    (crash_dir / "unreadable.json").mkdir()

    assert cli.main("replay") == snapshot_cli
    assert (crash_dir / "unreadable.json").exists()


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_replay_query_link_param_falls_back_to_recorded_when_unresolvable(
    cli, app_runner, ctx, tmp_path, crash_factory, snapshot_cli
):
    # When a query link can't re-extract from a fresh response, the recorded value is reused instead of bailing.
    received = []
    app, _ = ctx.openapi.make_flask_app(
        {
            "/first": {"get": {"responses": {"200": {"description": "OK"}}}},
            "/second": {
                "get": {
                    "parameters": [{"name": "token", "in": "query", "required": True, "schema": {"type": "string"}}],
                    "responses": {"500": {"description": "Error"}},
                }
            },
        }
    )

    @app.route("/first")
    def first():
        return jsonify({"other": "no token here"})

    @app.route("/second")
    def second():
        received.append(request.args.get("token"))
        return jsonify({"error": "boom"}), 500

    schema_url = app_runner.openapi_url(app)
    base = schema_url.rsplit("/", 1)[0]
    crash_dir = _crashes_dir(tmp_path)
    crash_dir.mkdir(parents=True, exist_ok=True)
    writer = CrashWriter(directory=crash_dir)
    writer.open(schema_location=schema_url, base_url=base)
    crash = crash_factory.chain(
        steps=[
            Step(method="GET", path="/first", status=200, body=b'{"other": "no token here"}'),
            Step(
                method="GET",
                path="/second",
                status=500,
                body=b'{"error": "boom"}',
                query={"token": "recorded"},
                link=Link(
                    operation_id="second",
                    parameters=[LinkParameter(location="query", name="token", expression="$response.body#/token")],
                ),
                checks=[FailingCheck(name="not_a_server_error")],
            ),
        ]
    )
    writer.write(crash)

    result = cli.main("replay", "--keep")

    assert result == snapshot_cli
    assert received == ["recorded"]


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_replay_reruns_recorded_check_disabled_in_config(cli, app_runner, ctx, crash_factory, tmp_path, snapshot_cli):
    # A recorded check is re-run on replay even when the current config disables it.
    app, _ = ctx.openapi.make_flask_app({"/boom": {"get": {"responses": {"500": {"description": "Error"}}}}})

    @app.route("/boom")
    def boom():
        return jsonify({"error": "boom"}), 500

    schema_url = app_runner.openapi_url(app)
    base = schema_url.rsplit("/", 1)[0]
    crash_file = _write_crash(
        tmp_path,
        crash_factory,
        url=f"{base}/boom",
        schema_location=schema_url,
        path_template="/boom",
        status=500,
        body='{"error": "boom"}',
    )

    assert (
        cli.main("replay", str(crash_file), "--keep", config={"checks": {"not_a_server_error": {"enabled": False}}})
        == snapshot_cli
    )


@pytest.mark.snapshot(replace_reproduce_with=True)
@pytest.mark.usefixtures("restore_checks")
def test_replay_check_raising_unexpected_error_is_errored_not_crash(
    cli, app_runner, ctx, crash_factory, tmp_path, snapshot_cli
):
    @schemathesis.check
    def boom_check(ctx, response, case):
        raise ValueError("boom in check")

    schema_url, base = _users_app(ctx, app_runner)
    crash_file = _write_crash(
        tmp_path,
        crash_factory,
        url=f"{base}/users",
        schema_location=schema_url,
        path_template="/users",
        status=500,
        body='{"error": "boom"}',
        check="boom_check",
    )

    assert cli.main("replay", str(crash_file), "--keep") == snapshot_cli


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_replay_intermediate_step_operation_removed_is_errored(
    cli, app_runner, ctx, tmp_path, snapshot_cli, crash_factory
):
    schema_url, base = _users_app(ctx, app_runner)
    crash_dir = _crashes_dir(tmp_path)
    crash_dir.mkdir(parents=True, exist_ok=True)
    writer = CrashWriter(directory=crash_dir)
    writer.open(schema_location=schema_url, base_url=base)
    crash = crash_factory.chain(
        steps=[
            Step(method="GET", path="/gone", status=200, body=b"{}"),
            Step(
                method="GET",
                path="/users",
                status=500,
                body=b'{"error": "boom"}',
                checks=[FailingCheck(name="not_a_server_error")],
            ),
        ]
    )
    writer.write(crash)

    assert cli.main("replay", "--keep") == snapshot_cli


def test_replay_connection_failure_is_errored(cli, app_runner, ctx, crash_factory, tmp_path):
    # The connection error wording is OS-specific (and line-wrapped), so assert behavior, not the exact output.
    schema_url, base = _users_app(ctx, app_runner)
    crash_file = _write_crash(
        tmp_path,
        crash_factory,
        url=f"{base}/users",
        schema_location=schema_url,
        path_template="/users",
        status=500,
        body='{"error": "boom"}',
    )

    result = cli.main("replay", str(crash_file), "--url", "http://127.0.0.1:1")

    assert "Traceback" not in result.output, result.output
    assert result.exit_code == 2, result.output
    assert crash_file.exists()


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_replay_non_utf8_response_body_does_not_abort(cli, app_runner, ctx, crash_factory, tmp_path, snapshot_cli):
    # A replayed response with non-UTF-8 bytes must decode lossily, not abort the command.
    app, _ = ctx.openapi.make_flask_app({"/boom": {"get": {"responses": {"500": {"description": "Error"}}}}})

    @app.route("/boom")
    def boom():
        return Response(b"\xff\xfe\xfd", status=500, content_type="application/octet-stream")

    schema_url = app_runner.openapi_url(app)
    base = schema_url.rsplit("/", 1)[0]
    crash_file = _write_crash(
        tmp_path,
        crash_factory,
        url=f"{base}/boom",
        schema_location=schema_url,
        path_template="/boom",
        status=500,
        body='{"error": "boom"}',
    )

    assert cli.main("replay", str(crash_file), "--keep") == snapshot_cli


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_replay_unresolvable_request_body_link_keeps_recorded_body(
    cli, app_runner, ctx, tmp_path, crash_factory, snapshot_cli
):
    received = []
    app, _ = ctx.openapi.make_flask_app(
        {
            "/source": {"get": {"responses": {"200": {"description": "OK"}}}},
            "/target": {
                "post": {
                    "requestBody": {"required": True, "content": {"application/json": {"schema": {"type": "object"}}}},
                    "responses": {"500": {"description": "Error"}},
                }
            },
        }
    )

    @app.route("/source")
    def source():
        return jsonify({"other": "no token here"})

    @app.route("/target", methods=["POST"])
    def target():
        received.append(request.get_json())
        return jsonify({"error": "boom"}), 500

    schema_url = app_runner.openapi_url(app)
    base = schema_url.rsplit("/", 1)[0]
    crash_dir = _crashes_dir(tmp_path)
    crash_dir.mkdir(parents=True, exist_ok=True)
    writer = CrashWriter(directory=crash_dir)
    writer.open(schema_location=schema_url, base_url=base)
    crash = crash_factory.chain(
        steps=[
            Step(method="GET", path="/source", status=200, body=b'{"other": "no token here"}'),
            Step(
                method="POST",
                path="/target",
                status=500,
                body=b'{"error": "boom"}',
                case_body={"token": "recorded"},
                media_type="application/json",
                link=Link(operation_id="target", request_body="$response.body#/token"),
                checks=[FailingCheck(name="not_a_server_error")],
            ),
        ]
    )
    writer.write(crash)

    result = cli.main("replay", "--keep")

    assert result == snapshot_cli
    assert received == [{"token": "recorded"}]


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_replay_request_body_link_installs_extracted_value_wholesale(
    cli, app_runner, ctx, tmp_path, crash_factory, snapshot_cli
):
    received = []
    app, _ = ctx.openapi.make_flask_app(
        {
            "/source": {"get": {"responses": {"200": {"description": "OK"}}}},
            "/target": {
                "post": {
                    "requestBody": {"required": True, "content": {"application/json": {"schema": {"type": "object"}}}},
                    "responses": {"500": {"description": "Error"}},
                }
            },
        }
    )

    @app.route("/source")
    def source():
        return jsonify({"token": "fresh"})

    @app.route("/target", methods=["POST"])
    def target():
        received.append(request.get_json())
        return jsonify({"error": "boom"}), 500

    schema_url = app_runner.openapi_url(app)
    base = schema_url.rsplit("/", 1)[0]
    crash_dir = _crashes_dir(tmp_path)
    crash_dir.mkdir(parents=True, exist_ok=True)
    writer = CrashWriter(directory=crash_dir)
    writer.open(schema_location=schema_url, base_url=base)
    crash = crash_factory.chain(
        steps=[
            Step(method="GET", path="/source", status=200, body=b'{"token": "fresh"}'),
            Step(
                method="POST",
                path="/target",
                status=500,
                body=b'{"error": "boom"}',
                media_type="application/json",
                link=Link(operation_id="target", request_body="$response.body"),
                checks=[FailingCheck(name="not_a_server_error")],
            ),
        ]
    )
    writer.write(crash)

    result = cli.main("replay", "--keep")

    assert result == snapshot_cli
    assert received == [{"token": "fresh"}]


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_replay_invalid_link_location_is_errored(cli, app_runner, ctx, tmp_path, snapshot_cli):
    # Foreign file the writer never emits: a link parameter with an unrecognized location.
    app, _ = ctx.openapi.make_flask_app(
        {
            "/source": {"get": {"responses": {"200": {"description": "OK"}}}},
            "/target": {
                "get": {
                    "parameters": [{"name": "id", "in": "query", "required": True, "schema": {"type": "string"}}],
                    "responses": {"500": {"description": "Error"}},
                }
            },
        }
    )

    @app.route("/source")
    def source():
        return jsonify({"id": "x"})

    @app.route("/target")
    def target():
        return jsonify({"error": "boom"}), 500

    schema_url = app_runner.openapi_url(app)
    base = schema_url.rsplit("/", 1)[0]
    crash_dir = _crashes_dir(tmp_path)
    crash_dir.mkdir(parents=True, exist_ok=True)
    writer = CrashWriter(directory=crash_dir)
    writer.open(schema_location=schema_url, base_url=base)
    step1 = CrashStep(
        method="GET",
        url=f"{base}/source",
        url_template="/source",
        request_headers={},
        response_status=200,
        response_headers={"content-type": "application/json"},
        response_body='{"id": "x"}',
        link=None,
        checks=[],
        meta=None,
        path="/source",
    )
    step2 = CrashStep(
        method="GET",
        url=f"{base}/target?id=stale",
        url_template="/target",
        request_headers={},
        response_status=500,
        response_headers={"content-type": "application/json"},
        response_body='{"error": "boom"}',
        link=CrashLink(operation_id="target", parameters={"bogus.id": "$response.body#/id"}),
        checks=[CrashCheck(name="not_a_server_error", status="failure", message="boom")],
        meta=None,
        path="/target",
        query={"id": "stale"},
    )
    writer.write(
        CrashFile(
            operation="GET /source -> GET /target",
            method="GET",
            path_template="/target",
            fingerprint="badloc",
            case_id="Bl4Lk8",
            code_sample="",
            sequence=[step1, step2],
        )
    )

    assert cli.main("replay", "--keep") == snapshot_cli


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_replay_multi_step_terminal_without_link(cli, app_runner, ctx, tmp_path, snapshot_cli, crash_factory):
    app, _ = ctx.openapi.make_flask_app(
        {
            "/first": {"get": {"responses": {"200": {"description": "OK"}}}},
            "/second": {"get": {"responses": {"500": {"description": "Error"}}}},
        }
    )

    @app.route("/first")
    def first():
        return jsonify({"ok": True})

    @app.route("/second")
    def second():
        return jsonify({"error": "boom"}), 500

    schema_url = app_runner.openapi_url(app)
    base = schema_url.rsplit("/", 1)[0]
    crash_dir = _crashes_dir(tmp_path)
    crash_dir.mkdir(parents=True, exist_ok=True)
    writer = CrashWriter(directory=crash_dir)
    writer.open(schema_location=schema_url, base_url=base)
    crash = crash_factory.chain(
        steps=[
            Step(method="GET", path="/first", status=200, body=b'{"ok": true}'),
            Step(
                method="GET",
                path="/second",
                status=500,
                body=b'{"error": "boom"}',
                checks=[FailingCheck(name="not_a_server_error")],
            ),
        ]
    )
    writer.write(crash)

    assert cli.main("replay", "--keep") == snapshot_cli


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_replay_honors_configured_cache_directory(cli, app_runner, ctx, crash_factory, tmp_path, snapshot_cli):
    schema_url, base = _users_app(ctx, app_runner)
    cache_dir = tmp_path / "custom_cache"
    crash_dir = cache_dir / "crashes"
    crash_dir.mkdir(parents=True)
    crash_file = _write_crash(
        crash_dir,
        crash_factory,
        url=f"{base}/users",
        schema_location=schema_url,
        path_template="/users",
        status=500,
        body='{"error": "was broken"}',
    )

    assert cli.main("replay", config={"cache": {"directory": str(cache_dir)}}) == snapshot_cli
    assert not crash_file.exists()


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_replay_configured_cache_without_crashes_reports_none(cli, tmp_path, snapshot_cli):
    assert cli.main("replay", config={"cache": {"directory": str(tmp_path / "fresh")}}) == snapshot_cli


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_replay_no_crashes_reports_none_found(cli, tmp_path, snapshot_cli):
    assert cli.main("replay") == snapshot_cli


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_replay_empty_directory_reports_none_found(cli, tmp_path, snapshot_cli):
    empty = tmp_path / "empty"
    empty.mkdir()

    assert cli.main("replay", str(empty)) == snapshot_cli


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_replay_nonexistent_path_errors(cli, tmp_path, snapshot_cli):
    assert cli.main("replay", str(tmp_path / "missing.json")) == snapshot_cli


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_replay_without_manifest_or_schema_location_bails(cli, app_runner, ctx, crash_factory, tmp_path, snapshot_cli):
    schema_url, base = _users_app(ctx, app_runner)
    crash_dir = _crashes_dir(tmp_path)
    crash_dir.mkdir(parents=True, exist_ok=True)
    crash_file = _write_crash(
        crash_dir,
        crash_factory,
        url=f"{base}/users",
        schema_location=schema_url,
        path_template="/users",
        status=500,
        body='{"error": "boom"}',
    )
    (crash_dir / MANIFEST_FILENAME).unlink()

    assert cli.main("replay") == snapshot_cli
    assert crash_file.exists()


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_replay_case_id_scan_skips_unreadable_files(cli, tmp_path, snapshot_cli):
    # Foreign file the writer never emits: garbage JSON the case-ID scan must skip without aborting.
    crash_dir = _crashes_dir(tmp_path)
    crash_dir.mkdir(parents=True, exist_ok=True)
    (crash_dir / "garbage.json").write_text("{not valid json")

    assert cli.main("replay", "Zz9Qx2") == snapshot_cli


def test_replay_case_id_scan_skips_non_object_json(cli, tmp_path, snapshot_cli):
    # Foreign file the writer never emits: valid JSON that isn't an object must be skipped, not abort the scan.
    crash_dir = _crashes_dir(tmp_path)
    crash_dir.mkdir(parents=True, exist_ok=True)
    (crash_dir / "list.json").write_text("[]")

    assert cli.main("replay", "Nb7Yt3") == snapshot_cli


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_replay_no_recorded_checks_is_errored(cli, app_runner, ctx, tmp_path, snapshot_cli):
    # Foreign file the writer never emits: a crash with no recorded checks, reported as an error.
    schema_url, base = _users_app(ctx, app_runner)
    crash_dir = _crashes_dir(tmp_path)
    crash_dir.mkdir(parents=True, exist_ok=True)
    writer = CrashWriter(directory=crash_dir)
    writer.open(schema_location=schema_url, base_url=base)
    step = CrashStep(
        method="GET",
        url=f"{base}/users",
        url_template="/users",
        request_headers={},
        response_status=500,
        response_headers={"content-type": "application/json"},
        response_body='{"error": "boom"}',
        link=None,
        checks=[],
        meta=None,
        path="/users",
    )
    writer.write(
        CrashFile(
            operation="GET /users",
            method="GET",
            path_template="/users",
            fingerprint="nochecks",
            case_id="Nk1234",
            code_sample=f"curl {base}/users",
            sequence=[step],
        )
    )

    assert cli.main("replay", "--keep") == snapshot_cli


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_replay_partial_fix_shows_per_check_breakdown(cli, app_runner, ctx, crash_factory, tmp_path, snapshot_cli):
    # With only some checks now passing, the case stays failed, the breakdown marks fixed ones, only those removed.
    app, _ = ctx.openapi.make_flask_app(
        {
            "/data": {
                "get": {
                    "responses": {
                        "200": {
                            "description": "OK",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "required": ["value"],
                                        "properties": {"value": {"type": "string"}},
                                    }
                                }
                            },
                        }
                    }
                }
            }
        }
    )

    @app.route("/data")
    def data():
        # 200 fixes the server error, but the body omits a required field so schema conformance still fails.
        return jsonify({"wrong": "shape"}), 200

    schema_url = app_runner.openapi_url(app)
    base = schema_url.rsplit("/", 1)[0]
    crash_dir = _crashes_dir(tmp_path)
    crash_dir.mkdir(parents=True, exist_ok=True)
    for check in ("not_a_server_error", "response_schema_conformance"):
        _write_crash(
            crash_dir,
            crash_factory,
            url=f"{base}/data",
            schema_location=schema_url,
            path_template="/data",
            status=500,
            body='{"error": "boom"}',
            check=check,
            case_id="Pf1Cd2",
        )

    assert cli.main("replay") == snapshot_cli
    assert len(_list_crash_files(crash_dir)) == 1
