import pytest
from flask import jsonify

from schemathesis.cli.constants import ExitCode
from schemathesis.reporting.html.render import humanize_duration


@pytest.fixture
def report_dir(tmp_path):
    return tmp_path / "report"


def run_with_report(cli, report_dir, *args, exit_code=ExitCode.OK, phases="fuzzing", **kwargs):
    cli.run_and_assert(
        *args,
        "--max-examples=1",
        f"--phases={phases}",
        "--seed=42",
        exit_code=exit_code,
        env={"SCHEMATHESIS_HTML_REPORT_DIR": str(report_dir)},
        **kwargs,
    )
    return (report_dir / "index.html").read_text(encoding="utf-8")


def test_html_report_passed(ctx, cli, report_dir, snapshot_html):
    api = ctx.openapi.apps.success()
    assert run_with_report(cli, report_dir, api.schema_url) == snapshot_html
    assert (report_dir / "assets" / "report.css").is_file()


def test_html_report_failed(ctx, cli, report_dir, snapshot_html):
    api = ctx.openapi.apps.failure()
    assert run_with_report(cli, report_dir, api.schema_url, exit_code=ExitCode.FAILURES) == snapshot_html


def test_html_report_schema_not_loaded(ctx, cli, report_dir, snapshot_html):
    api = ctx.openapi.apps.success()
    # Serves JSON that is not an API schema.
    url = f"{api.base_url}/api/success"
    assert run_with_report(cli, report_dir, url, exit_code=ExitCode.ERROR) == snapshot_html


def test_html_report_no_operations_selected(ctx, cli, report_dir, snapshot_html):
    api = ctx.openapi.apps.success()
    html = run_with_report(
        cli,
        report_dir,
        api.schema_url,
        "--include-path=/unknown",
        "--include-method=PURGE",
        exit_code=ExitCode.ERROR,
    )
    assert html == snapshot_html


def test_html_report_stopped_at_failure_limit(ctx, cli, app_runner, report_dir, snapshot_html):
    app, _ = ctx.openapi.make_flask_app(
        {
            "/api/failure": {"get": {"responses": {"200": {"description": "OK"}}}},
            "/api/success": {"get": {"responses": {"200": {"description": "OK"}}}},
        }
    )

    @app.route("/api/failure")
    def failure():
        return "", 500

    @app.route("/api/success")
    def success():
        return "{}"

    html = run_with_report(
        cli, report_dir, app_runner.openapi_url(app), "--max-failures=1", exit_code=ExitCode.FAILURES
    )
    assert html == snapshot_html


def test_html_report_schema_without_operations(ctx, cli, app_runner, report_dir, snapshot_html):
    app, _ = ctx.openapi.make_flask_app({})
    assert run_with_report(cli, report_dir, app_runner.openapi_url(app), exit_code=ExitCode.ERROR) == snapshot_html


def test_html_report_schema_unreachable_after_waiting(cli, report_dir, snapshot_html):
    # The terminal drops the "wait for the schema" tip once the run already waited.
    html = run_with_report(
        cli, report_dir, "http://127.0.0.1:1/openapi.json", "--wait-for-schema=1", exit_code=ExitCode.ERROR
    )
    assert html == snapshot_html


def test_html_report_schema_loading_hook_error(ctx, cli, report_dir, snapshot_html):
    api = ctx.openapi.apps.success()
    module = ctx.write_pymodule(
        """
@schemathesis.hook
def before_load_schema(ctx, raw_schema):
    raise ValueError("Broken hook")
"""
    )
    assert run_with_report(cli, report_dir, api.schema_url, exit_code=ExitCode.ERROR, hooks=module) == snapshot_html


def test_html_report_interrupted_before_any_case(ctx, cli, report_dir, snapshot_html):
    api = ctx.openapi.apps.success()
    module = ctx.write_pymodule(
        """
@schemathesis.hook
def before_call(ctx, case, **kwargs):
    raise KeyboardInterrupt
"""
    )
    html = run_with_report(cli, report_dir, api.schema_url, exit_code=ExitCode.INTERRUPTED, hooks=module)
    assert html == snapshot_html


def test_html_report_interrupted_with_results(ctx, cli, report_dir, snapshot_html):
    api = ctx.openapi.apps.success_and_failure()
    module = ctx.write_pymodule(
        """
CALLS = []


@schemathesis.hook
def before_call(ctx, case, **kwargs):
    # The first operation completes; Ctrl-C lands on the second.
    CALLS.append(case)
    if len(CALLS) > 1:
        raise KeyboardInterrupt
"""
    )
    html = run_with_report(cli, report_dir, api.schema_url, exit_code=ExitCode.INTERRUPTED, hooks=module)
    assert html == snapshot_html


def test_html_report_failure_outside_tested_operations(ctx, cli, app_runner, report_dir, snapshot_html):
    app, _ = ctx.openapi.make_flask_app({"/api/items": {"get": {"responses": {"200": {"description": "OK"}}}}})

    # Accepting a method the schema does not declare fails on that method, not on a tested operation.
    @app.route("/api/items", methods=["GET", "TRACE"])
    def items():
        return jsonify({})

    html = run_with_report(
        cli,
        report_dir,
        app_runner.openapi_url(app),
        "--checks=unsupported_method",
        phases="coverage",
        exit_code=ExitCode.FAILURES,
    )
    assert html == snapshot_html


INVALID_BODY = {
    "post": {
        "requestBody": {
            "required": True,
            "content": {"application/json": {"schema": {"type": "integer", "minimum": "abc"}}},
        },
        "responses": {"200": {"description": "OK"}},
    }
}


def test_html_report_every_operation_errored(ctx, cli, app_runner, report_dir, snapshot_html):
    app, _ = ctx.openapi.make_flask_app({"/api/broken": INVALID_BODY})
    assert run_with_report(cli, report_dir, app_runner.openapi_url(app), exit_code=ExitCode.FAILURES) == snapshot_html


def test_html_report_some_operations_errored(ctx, cli, app_runner, report_dir, snapshot_html):
    app, _ = ctx.openapi.make_flask_app(
        {"/api/broken": INVALID_BODY, "/api/success": {"get": {"responses": {"200": {"description": "OK"}}}}}
    )

    @app.route("/api/success")
    def success():
        return "{}"

    assert run_with_report(cli, report_dir, app_runner.openapi_url(app), exit_code=ExitCode.FAILURES) == snapshot_html


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [(1.23, "1.2s"), (59.96, "1m 0s"), (125.0, "2m 5s"), (7325.0, "2h 2m")],
    ids=["seconds", "rounds-into-minutes", "minutes", "hours"],
)
def test_humanize_duration(seconds, expected):
    assert humanize_duration(seconds) == expected


def test_html_report_fuzz_interrupted(ctx, cli, report_dir, snapshot_html):
    api = ctx.openapi.apps.success()
    module = ctx.write_pymodule(
        """
@schemathesis.hook
def before_call(ctx, case, **kwargs):
    raise KeyboardInterrupt
"""
    )
    result = cli.main(
        "fuzz",
        api.schema_url,
        "--max-time=2",
        "--seed=42",
        hooks=module,
        env={"SCHEMATHESIS_HTML_REPORT_DIR": str(report_dir)},
    )
    assert result.exit_code == ExitCode.INTERRUPTED, result.stdout
    assert (report_dir / "index.html").read_text(encoding="utf-8") == snapshot_html


def test_html_report_baseline_not_written(ctx, cli, tmp_path, report_dir, snapshot_html):
    api = ctx.openapi.apps.success()
    # The baseline's parent is a regular file, so saving the baseline fails after the run.
    parent = tmp_path / "file"
    parent.write_text("", encoding="utf-8")
    baseline = str(parent / "baseline.yaml")
    html = run_with_report(
        cli, report_dir, api.schema_url, "--baseline-update", exit_code=ExitCode.ERROR, config={"baseline": baseline}
    )
    # The message quotes the path with `repr`, which doubles Windows backslashes.
    assert html.replace(repr(baseline)[1:-1], "<BASELINE>") == snapshot_html


def test_html_report_no_checks_ran(ctx, cli, report_dir, snapshot_html):
    api = ctx.openapi.apps.success()
    html = run_with_report(cli, report_dir, api.schema_url, config={"checks": {"enabled": False}})
    assert html == snapshot_html
