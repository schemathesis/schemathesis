import pytest
from _pytest.main import ExitCode
from flask import jsonify


def read_report(directory):
    index = (directory / "index.html").read_text()
    pages = {page.name: page.read_text() for page in (directory / "operations").glob("*.html")}
    return index, pages


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_html_report_reports_block_lists_html_path(ctx, cli, tmp_path, snapshot_cli):
    api = ctx.openapi.apps.success()
    report = tmp_path / "report"
    assert (
        cli.main(
            "run",
            api.schema_url,
            "--report-html-path",
            str(report),
            "--phases=coverage",
        )
        == snapshot_cli
    )


def test_html_report_generated_for_failing_run(ctx, cli, tmp_path):
    api = ctx.openapi.apps.success_and_failure()
    report = tmp_path / "report"
    cli.run(api.schema_url, "--report-html-path", str(report), "--max-examples=5", "--checks=all")
    index, pages = read_report(report)
    assert (report / "assets" / "report.css").is_file()
    assert (report / "assets" / "app.js").is_file()
    assert '<div class="hero-status-label">Failed</div>' in index
    assert "/api/failure" in index
    assert any("case-card" in page for page in pages.values())


def test_html_report_fatal_error_does_not_report_passed(cli, tmp_path):
    report = tmp_path / "report"
    cli.run("http://127.0.0.1:1/openapi.json", "--report-html-path", str(report))
    index, _ = read_report(report)
    assert 'hero-status-label">Passed' not in index


def test_html_report_all_passed(ctx, cli, tmp_path):
    api = ctx.openapi.apps.success()
    report = tmp_path / "report"
    cli.run(api.schema_url, "--report-html-path", str(report), "--max-examples=5")
    index, pages = read_report(report)
    assert '<div class="hero-status-label">Passed</div>' in index
    assert all("pass-banner" in page for page in pages.values())


def test_html_report_graphql_labels_render_readably(ctx, cli, tmp_path):
    api = ctx.graphql.apps.books()
    report = tmp_path / "report"
    cli.run(api.schema_url, "--report-html-path", str(report), "--max-examples=5", "--mode=positive")
    index, _ = read_report(report)
    # GraphQL labels ("Query.getBooks") have no HTTP method; they render as a readable path, not a method badge.
    assert "Query.getBooks" in index
    assert "QUERY.GETBOOKS" not in index


def test_html_report_via_report_format_flag(ctx, cli, tmp_path):
    api = ctx.openapi.apps.success()
    result = cli.run(api.schema_url, "--report", "html", "--report-dir", str(tmp_path / "reports"), "--max-examples=5")
    generated = list((tmp_path / "reports").glob("html-*/index.html"))
    assert len(generated) == 1
    assert "HTML:" in result.stdout


def test_html_report_includes_stateful_failures(ctx, cli, tmp_path):
    api = ctx.openapi.apps.users_crud()
    report = tmp_path / "report"
    # Stateful chain discovery needs a larger budget to reliably reach the planted CRUD bug.
    cli.run_and_assert(
        api.schema_url,
        "--phases=stateful",
        "--no-shrink",
        "--max-examples=80",
        "--max-failures=1",
        "-c not_a_server_error",
        "--report-html-path",
        str(report),
        exit_code=ExitCode.TESTS_FAILED,
    )
    index, pages = read_report(report)
    assert '<div class="hero-status-label">Failed</div>' in index
    assert "STATEFUL_tests.html" in pages
    assert "case-card" in pages["STATEFUL_tests.html"]


def test_html_report_warnings_respect_project_scoped_config(ctx, cli, tmp_path):
    # A title-matched [[project]] section governs warning rules; stale top-level `fail-on`
    # must not flip the exit code just because the HTML report is enabled.
    api = ctx.openapi.apps.basic()
    report = tmp_path / "report"
    result = cli.run(
        api.schema_url,
        "--report-html-path",
        str(report),
        "-c not_a_server_error",
        "--phases=fuzzing",
        "--mode=positive",
        "-n 10",
        config={
            "warnings": {"fail-on": ["missing_auth"]},
            "project": [{"title": "Test", "warnings": False}],
        },
    )
    assert result.exit_code == ExitCode.OK, result.stdout
    index, _ = read_report(report)
    assert "Missing authentication" not in index


def test_html_report_failed_exit_code_does_not_report_passed(ctx, cli, tmp_path):
    api = ctx.openapi.apps.basic()
    report = tmp_path / "report"
    result = cli.run(
        api.schema_url,
        "--report-html-path",
        str(report),
        config={"warnings": {"fail-on": ["missing_auth"]}},
    )
    assert result.exit_code == ExitCode.TESTS_FAILED
    index, _ = read_report(report)
    assert '<div class="hero-status-label">Failed</div>' in index
    assert '<div class="hero-status-label">Passed</div>' not in index


def test_html_report_escapes_malicious_schema(ctx, cli, tmp_path):
    app, _ = ctx.openapi.make_flask_app(
        {
            "/x<img src=x onerror=alert(1)>": {
                "get": {"summary": '<script>alert("xss")</script>', "responses": {"200": {"description": "OK"}}}
            }
        }
    )
    report = tmp_path / "report"
    cli.run_openapi_app(app, "--report-html-path", str(report), "--max-examples=5")
    index, pages = read_report(report)
    for document in (index, *pages.values()):
        assert "<img src=x" not in document
        assert '<script>alert("xss")</script>' not in document


def test_html_report_coverage_unspecified_method_failure_visible(ctx, cli, tmp_path):
    # UNSPECIFIED_HTTP_METHOD coverage failures are stored under the actual method+path tested,
    # not the recorder's original operation label (GH-3699); the report must still surface them.
    report = tmp_path / "report"
    app, _ = ctx.openapi.make_flask_app({"/users": {"get": {"responses": {"200": {"description": "OK"}}}}})

    @app.route("/users", methods=["GET", "DELETE", "POST", "PUT", "PATCH", "HEAD"])
    def users():
        return jsonify([])

    result = cli.run_openapi_app(
        app,
        "--phases=coverage",
        "--report-html-path",
        str(report),
        "--checks=unsupported_method",
    )
    assert result.exit_code == ExitCode.TESTS_FAILED

    _, pages = read_report(report)
    assert any("unsupported_method" in page for page in pages.values())
