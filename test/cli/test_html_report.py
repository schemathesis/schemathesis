import pytest
from _pytest.main import ExitCode
from flask import jsonify


def read_index(directory):
    return (directory / "index.html").read_text(encoding="utf-8")


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_html_report_reports_block_lists_html_path(ctx, cli, tmp_path, snapshot_cli):
    api = ctx.openapi.apps.success()
    report = tmp_path / "report"
    assert cli.run(api.schema_url, "--report-html-path", str(report), "--phases=coverage") == snapshot_cli


def test_html_report_generated_for_failing_run(ctx, cli, tmp_path):
    api = ctx.openapi.apps.success_and_failure()
    report = tmp_path / "report"
    cli.run(api.schema_url, "--report-html-path", str(report), "--max-examples=5")
    index = read_index(report)
    assert (report / "assets" / "report.css").is_file()
    assert '<h1 class="hero-status-label">Failed</h1>' in index
    assert '<span class="path">/api/failure</span>' in index
    assert "Server error" in index


def test_html_report_engine_never_started_reports_errored(cli, tmp_path):
    report = tmp_path / "report"
    cli.run("http://127.0.0.1:1/openapi.json", "--report-html-path", str(report))
    index = read_index(report)
    assert '<h1 class="hero-status-label">Errored</h1>' in index
    assert "hs-stop-note" not in index
    assert "Failed to load specification" in index
    assert '<span class="tk">Spec</span>' in index
    assert "http://127.0.0.1:1/openapi.json" in index


def test_html_report_all_passed(ctx, cli, tmp_path):
    api = ctx.openapi.apps.success()
    report = tmp_path / "report"
    cli.run(api.schema_url, "--report-html-path", str(report), "--max-examples=5")
    index = read_index(report)
    assert '<h1 class="hero-status-label">Passed</h1>' in index
    assert "all passing" in index


def test_html_report_via_report_format_flag(ctx, cli, tmp_path):
    api = ctx.openapi.apps.success()
    result = cli.run(api.schema_url, "--report", "html", "--report-dir", str(tmp_path / "reports"), "--max-examples=5")
    assert len(list((tmp_path / "reports").glob("html-*/index.html"))) == 1
    assert "HTML:" in result.stdout


def test_html_report_fuzz_command(ctx, cli, tmp_path):
    api = ctx.openapi.apps.success_and_slow()
    report = tmp_path / "report"
    # The half-second endpoint cannot be exhausted inside the budget, so the run always stops on the clock.
    cli.main("fuzz", api.schema_url, "--report-html-path", str(report), "--max-time=1")
    index = read_index(report)
    assert '<div class="hs-stop-note">Time limit reached</div>' in index
    assert "Failed</h1>" not in index


def test_html_report_warning_driven_failure_shows_run_failed(ctx, cli, tmp_path):
    api = ctx.openapi.apps.basic()
    report = tmp_path / "report"
    result = cli.run(
        api.schema_url,
        "--report-html-path",
        str(report),
        "-c not_a_server_error",
        config={"warnings": {"fail-on": ["missing_auth"]}},
    )
    assert result.exit_code == ExitCode.TESTS_FAILED
    index = read_index(report)
    assert '<h1 class="hero-status-label">Failed</h1>' in index
    assert "Run failed" in index
    assert "failures-section" not in index
    assert "Authentication failed" in index


def test_html_report_escapes_malicious_schema(ctx, cli, tmp_path):
    app, _ = ctx.openapi.make_flask_app(
        {"/x<img src=x onerror=alert(1)>": {"get": {"responses": {"200": {"description": "OK"}}}}}
    )

    @app.route("/<path:anything>")
    def boom(anything):
        return jsonify({}), 500

    report = tmp_path / "report"
    cli.run_openapi_app(app, "--report-html-path", str(report), "--max-examples=5", "-c not_a_server_error")
    index = read_index(report)
    assert "<img src=x" not in index
    assert "&lt;img src=x" in index


def test_html_report_sanitizes_schema_location_and_base_url(ctx, cli, tmp_path):
    api = ctx.openapi.apps.success()
    report = tmp_path / "report"
    cli.run(
        api.schema_url.replace("http://", "http://user:pass@"),
        "--url",
        api.base_url.replace("http://", "http://user:pass@"),
        "--report-html-path",
        str(report),
        "--max-examples=1",
    )
    index = read_index(report)
    assert "user:pass@" not in index
    assert "[Filtered]@127.0.0.1" in index


def test_html_report_write_failure_does_not_mask_exit_code(ctx, cli, tmp_path):
    # `assets` exists as a file, so the writer fails at shutdown; the run's verdict must still win.
    api = ctx.openapi.apps.success()
    report = tmp_path / "report"
    report.mkdir()
    (report / "assets").write_text("not a directory")
    result = cli.run(api.schema_url, "--report-html-path", str(report), "--max-examples=1")
    assert result.exit_code == ExitCode.OK, result.stdout
    assert "Internal Error" in result.stdout
