from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from flask import jsonify
from rich.console import Console

from schemathesis.cli.commands.replay.executor import CheckOutcome, ReplayOutcome, ReplayStatus, StepOutcome
from schemathesis.cli.commands.replay.output import render_replay
from schemathesis.config import OutputConfig
from schemathesis.reporting.crashes import MANIFEST_FILENAME, CrashCheck, CrashFile, CrashStep, CrashWriter
from test.apps.catalog.openapi.modifiers.stateful import IndependentInternalError


def _write_crash(
    directory: Path,
    *,
    method: str = "GET",
    url: str,
    status: int,
    body: str,
    check: str = "not_a_server_error",
    case_id: str = "Ab1Cd2",
) -> Path:
    writer = CrashWriter(directory=directory)
    writer.open(schema_location=url, base_url=url)
    step = CrashStep(
        method=method,
        url=url,
        url_template=url,
        request_headers={},
        request_body=None,
        response_status=status,
        response_headers={"content-type": "application/json"},
        response_body=body,
        link=None,
        checks=[CrashCheck(name=check, status="failure", message=f"{status} error")],
        meta=None,
    )
    path_part = url.split("/", 3)[-1] if "/" in url else url
    crash = CrashFile(
        operation=f"{method} /{path_part}",
        method=method,
        path_template=f"/{path_part}",
        fingerprint="testtest",
        case_id=case_id,
        code_sample=f"curl -X {method} {url}",
        sequence=[step],
    )
    writer.write(crash)
    return directory / crash.filename()


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_replay_fixed(cli, app_runner, ctx, tmp_path, snapshot_cli):
    app, _ = ctx.openapi.make_flask_app({"/users": {"get": {"responses": {"200": {"description": "OK"}}}}})

    @app.route("/users")
    def users():
        return jsonify([])

    port = app_runner.run_flask_app(app)
    crash_file = _write_crash(
        tmp_path,
        url=f"http://127.0.0.1:{port}/users",
        status=500,
        body='{"error": "was broken"}',
    )

    assert cli.main("replay", str(crash_file)) == snapshot_cli
    assert not crash_file.exists()


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_replay_still_failing(cli, app_runner, ctx, tmp_path, snapshot_cli):
    app, _ = ctx.openapi.make_flask_app({"/broken": {"get": {"responses": {"500": {"description": "Error"}}}}})

    @app.route("/broken")
    def broken():
        return jsonify({"error": "still broken"}), 500

    port = app_runner.run_flask_app(app)
    crash_file = _write_crash(
        tmp_path,
        url=f"http://127.0.0.1:{port}/broken",
        status=500,
        body='{"error": "still broken"}',
    )

    assert cli.main("replay", str(crash_file)) == snapshot_cli
    assert crash_file.exists()


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_replay_changed_response(cli, app_runner, ctx, tmp_path, snapshot_cli):
    app, _ = ctx.openapi.make_flask_app({"/data": {"get": {"responses": {"200": {"description": "OK"}}}}})

    @app.route("/data")
    def data():
        return jsonify({"value": "new"}), 200

    port = app_runner.run_flask_app(app)
    _write_crash(
        tmp_path,
        url=f"http://127.0.0.1:{port}/data",
        status=200,
        body='{"value": "old"}',
        check="response_schema_conformance",
    )

    assert cli.main("replay", str(tmp_path)) == snapshot_cli


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_replay_base_url_override(cli, app_runner, ctx, tmp_path, snapshot_cli):
    app, _ = ctx.openapi.make_flask_app({"/users": {"get": {"responses": {"200": {"description": "OK"}}}}})

    @app.route("/users")
    def users():
        return jsonify([])

    port = app_runner.run_flask_app(app)
    _write_crash(
        tmp_path,
        url="http://nonexistent-host:9999/users",
        status=500,
        body='{"error": "broken"}',
    )

    assert cli.main("replay", str(tmp_path), "--url", f"http://127.0.0.1:{port}") == snapshot_cli


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_replay_keep_flag(cli, app_runner, ctx, tmp_path, snapshot_cli):
    app, _ = ctx.openapi.make_flask_app({"/users": {"get": {"responses": {"200": {"description": "OK"}}}}})

    @app.route("/users")
    def users():
        return jsonify([])

    port = app_runner.run_flask_app(app)
    crash_file = _write_crash(
        tmp_path,
        url=f"http://127.0.0.1:{port}/users",
        status=500,
        body='{"error": "was broken"}',
    )

    assert cli.main("replay", str(crash_file), "--keep") == snapshot_cli
    assert crash_file.exists()


def _crashes_dir(tmp_path: Path) -> Path:
    return tmp_path / ".schemathesis" / "default" / "cache" / "crashes"


def _list_crash_files(directory: Path) -> list[Path]:
    return sorted(f for f in directory.glob("*.json") if f.name != MANIFEST_FILENAME)


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


def test_replay_unknown_case_id(cli, ctx, tmp_path):
    api = ctx.openapi.apps.failure()

    cli.run(api.schema_url)

    result = cli.main("replay", "ZZZZZZ")

    assert result.exit_code == 2
    assert "no crash file found for case ID" in result.output


def test_replay_kitchen_sink_at_scale(cli, ctx, tmp_path):
    api = ctx.openapi.apps.kitchen_sink()

    # /api/flaky returns 5xx on first hit, 2xx after — non-deterministic by design,
    # so exclude it from the roundtrip assertion (replay would report it as FIXED).
    cli.run(api.schema_url, "--max-examples=2", "--phases=fuzzing", "--exclude-path=/api/flaky")

    crash_files = _list_crash_files(_crashes_dir(tmp_path))
    assert len(crash_files) >= 10, f"Expected many crashes from kitchen sink, got {len(crash_files)}"

    result = cli.main("replay", "--keep")

    assert result.exit_code == 1
    assert "ERROR" not in result.output
    # Most recordings should reproduce against the same server; tolerate the occasional
    # CHANGED from server-side ordering/serialization noise across the wide surface.
    match = re.search(r"(\d+) failed", result.output)
    assert match is not None
    failing = int(match.group(1))
    assert failing >= len(crash_files) - 4, f"{failing}/{len(crash_files)} reproduced; output:\n{result.output}"


def test_replay_stateful_chain(cli, ctx, tmp_path):
    api = ctx.openapi.apps.stateful_users(IndependentInternalError())

    cli.run(api.schema_url, "--max-examples=5", "--phases=stateful")

    crash_files = _list_crash_files(_crashes_dir(tmp_path))
    assert crash_files, "Expected the stateful run to record at least one crash"

    multi_step = [f for f in crash_files if len(json.loads(f.read_text())["sequence"]) > 1]
    assert multi_step, "Expected at least one multi-step crash"

    result = cli.main("replay", "--keep")

    assert "Stateful replay is not yet supported" not in result.output
    assert result.exit_code in (0, 1)
    assert "->" in result.output


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_replay_keyboard_interrupt(cli, app_runner, ctx, tmp_path, snapshot_cli, monkeypatch):
    app, _ = ctx.openapi.make_flask_app({"/a": {"get": {"responses": {"500": {"description": "Error"}}}}})

    @app.route("/a")
    def endpoint_a():
        return jsonify({"error": "boom"}), 500

    port = app_runner.run_flask_app(app)
    for name in ("a", "b"):
        _write_crash(
            tmp_path,
            url=f"http://127.0.0.1:{port}/{name}",
            status=500,
            body='{"error": "boom"}',
        )

    call_count = 0
    original = __import__("schemathesis.cli.commands.replay.executor", fromlist=["replay_crash_file"]).replay_crash_file

    def interrupt_on_second(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count > 1:
            raise KeyboardInterrupt
        return original(*args, **kwargs)

    monkeypatch.setattr("schemathesis.cli.commands.replay.replay_crash_file", interrupt_on_second)

    assert cli.main("replay", str(tmp_path), "--keep") == snapshot_cli


def _make_crash(
    *,
    operation: str,
    sequence: list[CrashStep],
    case_id: str = "Xt9Kp2",
    code_sample: str = "",
) -> CrashFile:
    terminal = sequence[-1]
    return CrashFile(
        operation=operation,
        method=terminal.method,
        path_template=terminal.url_template or terminal.url,
        fingerprint="aabbccdd",
        case_id=case_id,
        code_sample=code_sample or f"curl -X {terminal.method} {terminal.url}",
        sequence=sequence,
    )


def _make_step(
    *,
    method: str = "GET",
    url: str = "",
    url_template: str = "",
    response_status: int = 200,
    response_body: str = "{}",
    checks: list[CrashCheck] | None = None,
    link: dict | None = None,
) -> CrashStep:
    return CrashStep(
        method=method,
        url=url,
        url_template=url_template,
        request_headers={},
        request_body=None,
        response_status=response_status,
        response_headers={},
        response_body=response_body,
        link=link,
        checks=checks or [],
        meta=None,
    )


def _capture_render(
    *,
    crashes: list[CrashFile],
    outcomes: list[ReplayOutcome],
    source: str = ".schemathesis/crashes/",
    base_url: str | None = None,
    total_checks: int = 1,
    duration_ms: int = 142,
    removal_count: int = 0,
) -> str:
    console = Console(width=100, force_terminal=False, color_system=None, record=True)
    render_replay(
        crashes=crashes,
        outcomes=outcomes,
        source=source,
        base_url=base_url,
        total_checks=total_checks,
        duration_ms=duration_ms,
        output_config=OutputConfig(),
        removal_count=removal_count,
        console=console,
    )
    return console.export_text()


def test_render_unit_fixed(snapshot):
    crashes = [
        _make_crash(
            operation="GET /users",
            sequence=[
                _make_step(
                    method="GET",
                    url="http://x/users",
                    url_template="http://x/users",
                    response_status=500,
                    response_body='{"error": "boom"}',
                    checks=[CrashCheck(name="not_a_server_error", status="failure", message="")],
                )
            ],
        )
    ]
    outcomes = [ReplayOutcome(status=ReplayStatus.FIXED, actual_status=200, actual_body="[]", duration_ms=142)]
    assert _capture_render(crashes=crashes, outcomes=outcomes) == snapshot


def test_render_unit_failing_multi_check(snapshot):
    crashes = [
        _make_crash(
            operation="POST /orders",
            sequence=[
                _make_step(
                    method="POST",
                    url="http://x/orders",
                    url_template="http://x/orders",
                    response_status=500,
                    response_body='{"error": "unhandled exception"}',
                    checks=[
                        CrashCheck(name="response_schema_conformance", status="failure", message=""),
                        CrashCheck(name="not_a_server_error", status="failure", message=""),
                        CrashCheck(name="positive_data_acceptance", status="failure", message=""),
                    ],
                )
            ],
        )
    ]
    outcomes = [
        ReplayOutcome(
            status=ReplayStatus.FAILED,
            actual_status=500,
            actual_body='{"error": "unhandled exception"}',
            duration_ms=85,
            check_outcomes=[
                CheckOutcome(name="response_schema_conformance", status=ReplayStatus.FAILED),
                CheckOutcome(name="not_a_server_error", status=ReplayStatus.FAILED),
                CheckOutcome(name="positive_data_acceptance", status=ReplayStatus.FAILED),
            ],
        )
    ]
    assert _capture_render(crashes=crashes, outcomes=outcomes, total_checks=3) == snapshot


def test_render_stateful_changed_with_chain(snapshot):
    crashes = [
        _make_crash(
            operation="POST /users -> DELETE /users/{user_id}",
            sequence=[
                _make_step(method="POST", url="http://x/auth/login", url_template="http://x/auth/login"),
                _make_step(
                    method="POST",
                    url="http://x/users",
                    url_template="http://x/users",
                    response_status=201,
                    response_body='{"id": 1}',
                ),
                _make_step(
                    method="GET",
                    url="http://x/users/1/profile",
                    url_template="http://x/users/{user_id}/profile",
                ),
                _make_step(
                    method="DELETE",
                    url="http://x/users/1",
                    url_template="http://x/users/{user_id}",
                    response_status=500,
                    response_body='{"error": "crash"}',
                    checks=[CrashCheck(name="not_a_server_error", status="failure", message="")],
                ),
            ],
        )
    ]
    outcomes = [
        ReplayOutcome(
            status=ReplayStatus.CHANGED,
            actual_status=503,
            actual_body='{"error": "service unavailable"}',
            duration_ms=312,
            check_outcomes=[
                CheckOutcome(name="not_a_server_error", status=ReplayStatus.CHANGED, note="500 -> 503"),
            ],
            step_outcomes=[
                StepOutcome(200, "{}"),
                StepOutcome(201, '{"id": 2}'),
                StepOutcome(200, "{}"),
                StepOutcome(503, '{"error": "service unavailable"}'),
            ],
        )
    ]
    assert _capture_render(crashes=crashes, outcomes=outcomes) == snapshot


def test_render_error_outcome(snapshot):
    crashes = [
        _make_crash(
            operation="GET /accounts/{id}/verify",
            sequence=[
                _make_step(
                    method="GET",
                    url="http://x/accounts/1/verify",
                    url_template="http://x/accounts/{id}/verify",
                    response_status=500,
                    checks=[CrashCheck(name="positive_data_acceptance", status="failure", message="")],
                )
            ],
        )
    ]
    outcomes = [
        ReplayOutcome(
            status=ReplayStatus.ERRORED,
            actual_status=None,
            actual_body="",
            duration_ms=62,
            error_message="extraction failed at step 2 - $response.body#/id not found",
            check_outcomes=[
                CheckOutcome(name="positive_data_acceptance", status=ReplayStatus.ERRORED, note="extraction failed"),
            ],
        )
    ]
    assert _capture_render(crashes=crashes, outcomes=outcomes) == snapshot


def test_render_full_report_with_url_override(snapshot):
    crashes = [
        _make_crash(
            operation="GET /users",
            sequence=[
                _make_step(
                    method="GET",
                    url="http://x/users",
                    url_template="http://x/users",
                    response_status=500,
                    response_body='{"error": "boom"}',
                    checks=[CrashCheck(name="not_a_server_error", status="failure", message="")],
                )
            ],
        ),
        _make_crash(
            operation="POST /orders",
            sequence=[
                _make_step(
                    method="POST",
                    url="http://x/orders",
                    url_template="http://x/orders",
                    response_status=500,
                    response_body='{"error": "unhandled exception"}',
                    checks=[
                        CrashCheck(name="response_schema_conformance", status="failure", message=""),
                        CrashCheck(name="not_a_server_error", status="failure", message=""),
                    ],
                )
            ],
        ),
    ]
    outcomes = [
        ReplayOutcome(status=ReplayStatus.FIXED, actual_status=200, actual_body="[]", duration_ms=142),
        ReplayOutcome(
            status=ReplayStatus.FAILED,
            actual_status=500,
            actual_body='{"error": "unhandled exception"}',
            duration_ms=85,
            check_outcomes=[
                CheckOutcome(name="response_schema_conformance", status=ReplayStatus.FAILED),
                CheckOutcome(name="not_a_server_error", status=ReplayStatus.FAILED),
            ],
        ),
    ]
    assert (
        _capture_render(crashes=crashes, outcomes=outcomes, base_url="https://staging.example.com", total_checks=3)
        == snapshot
    )


def test_render_partial_fix(snapshot):
    crashes = [
        _make_crash(
            operation="POST /orders",
            sequence=[
                _make_step(
                    method="POST",
                    url="http://x/orders",
                    url_template="http://x/orders",
                    response_status=500,
                    response_body='{"error": "unhandled"}',
                    checks=[
                        CrashCheck(name="not_a_server_error", status="failure", message=""),
                        CrashCheck(name="response_schema_conformance", status="failure", message=""),
                        CrashCheck(name="positive_data_acceptance", status="failure", message=""),
                    ],
                )
            ],
        )
    ]
    outcomes = [
        ReplayOutcome(
            status=ReplayStatus.FAILED,
            actual_status=200,
            actual_body='{"data": "ok"}',
            duration_ms=95,
            check_outcomes=[
                CheckOutcome(name="not_a_server_error", status=ReplayStatus.FIXED, note="500 -> 200"),
                CheckOutcome(name="response_schema_conformance", status=ReplayStatus.FAILED),
                CheckOutcome(name="positive_data_acceptance", status=ReplayStatus.CHANGED, note="500 -> 200"),
            ],
        )
    ]
    assert _capture_render(crashes=crashes, outcomes=outcomes, total_checks=3) == snapshot


def test_render_removal_notice(snapshot):
    crashes = [
        _make_crash(
            operation="GET /users",
            sequence=[
                _make_step(
                    method="GET",
                    url="http://x/users",
                    url_template="http://x/users",
                    response_status=500,
                    response_body='{"error": "boom"}',
                    checks=[CrashCheck(name="not_a_server_error", status="failure", message="")],
                )
            ],
        )
    ]
    outcomes = [ReplayOutcome(status=ReplayStatus.FIXED, actual_status=200, actual_body="[]", duration_ms=142)]
    assert _capture_render(crashes=crashes, outcomes=outcomes, removal_count=5) == snapshot
