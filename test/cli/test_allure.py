from _pytest.main import ExitCode
from flask import jsonify

from test.utils import allure_results


def test_allure_result_files_written(ctx, cli, tmp_path):
    api = ctx.openapi.apps.success_and_failure()
    allure_dir = tmp_path / "allure-results"
    cli.run_and_assert(
        api.schema_url,
        f"--report-allure-path={allure_dir}",
        "--checks=not_a_server_error",
        exit_code=ExitCode.TESTS_FAILED,
    )
    for data in allure_results(allure_dir):
        assert "name" in data
        assert data["status"] in ("passed", "failed", "broken", "skipped")
        assert data["testCaseId"] == data["historyId"]
        assert any(lbl["name"] == "framework" and lbl["value"] == "schemathesis" for lbl in data["labels"])


def test_allure_report_via_config(ctx, cli, tmp_path):
    api = ctx.openapi.apps.success_and_failure()
    allure_dir = tmp_path / "allure-results"
    cli.run_and_assert(
        api.schema_url,
        exit_code=ExitCode.TESTS_FAILED,
        config={"reports": {"allure": {"path": str(allure_dir)}}},
    )
    assert any(allure_dir.glob("*-result.json"))


def test_allure_failure_has_reproduce_attachment(ctx, cli, tmp_path):
    api = ctx.openapi.apps.failure()
    allure_dir = tmp_path / "allure-results"
    cli.run_and_assert(
        api.schema_url,
        f"--report-allure-path={allure_dir}",
        "--checks=not_a_server_error",
        exit_code=ExitCode.TESTS_FAILED,
    )
    results = allure_results(allure_dir)
    failed = [r for r in results if r["status"] == "failed"]
    assert failed, "Expected at least one failed result"
    step_messages = [s["statusDetails"]["message"] for r in failed for s in r.get("steps", [])]
    assert step_messages
    assert any("curl" in m.lower() for m in step_messages)


def test_coverage_unspecified_method_in_allure(ctx, cli, tmp_path):
    allure_dir = tmp_path / "allure-results"
    app, _ = ctx.openapi.make_flask_app({"/users": {"get": {"responses": {"200": {"description": "OK"}}}}})

    @app.route("/users", methods=["GET", "DELETE", "POST", "PUT", "PATCH", "HEAD"])
    def users():
        return jsonify([])

    assert (
        cli.run_openapi_app(
            app,
            "--phases=coverage",
            f"--report-allure-path={allure_dir}",
            "--checks=unsupported_method",
        ).exit_code
        == ExitCode.TESTS_FAILED
    )

    results = allure_results(allure_dir)
    messages = [step["statusDetails"]["message"] for result in results for step in result.get("steps", [])]
    assert any("Wrong response to undeclared method" in message for message in messages)


def test_allure_no_report_without_flag(ctx, cli, tmp_path):
    api = ctx.openapi.apps.success()
    cli.run_and_assert(api.schema_url)
    assert not list(tmp_path.glob("**/*-result.json"))


def test_allure_broken_status_on_non_fatal_error(ctx, cli, tmp_path):
    api = ctx.openapi.apps.invalid()
    allure_dir = tmp_path / "allure-results"
    cli.run_and_assert(api.schema_url, f"--report-allure-path={allure_dir}", exit_code=ExitCode.TESTS_FAILED)
    results = allure_results(allure_dir)
    broken = [r for r in results if r["status"] == "broken"]
    assert broken
    assert broken[0]["statusDetails"]["message"]


def test_allure_results_in_timestamped_dir_when_no_explicit_path(ctx, cli, tmp_path):
    api = ctx.openapi.apps.success()
    cli.run_and_assert(api.schema_url, "--report=allure", f"--report-dir={tmp_path}")
    allure_dirs = [d for d in tmp_path.iterdir() if d.is_dir() and d.name.startswith("allure-")]
    assert len(allure_dirs) == 1
    assert list(allure_dirs[0].glob("*-result.json"))


def test_allure_stateful_phase_produces_result(ctx, cli, tmp_path):
    api = ctx.openapi.apps.users_crud()
    allure_dir = tmp_path / "allure-results"
    cli.run_and_assert(
        api.schema_url,
        f"--report-allure-path={allure_dir}",
        # This one won't fail
        "--checks=use_after_free",
        "--phases=stateful",
        "--max-examples=5",
    )
    for data in allure_results(allure_dir):
        assert data["status"] in ("passed", "failed", "broken", "skipped")
        assert not any(lbl["name"] == "feature" for lbl in data["labels"])


def test_examples_phase_skip_cleared_when_coverage_runs(ctx, cli, tmp_path):
    # When the Examples phase skips an operation (schema has no inline examples)
    # but the Coverage phase subsequently runs and produces real results,
    # the Allure report must NOT mark the result as skipped and must not
    # populate statusDetails with the skip reason.
    api = ctx.openapi.apps.success()
    allure_dir = tmp_path / "allure-results"
    cli.run_and_assert(
        api.schema_url,
        f"--report-allure-path={allure_dir}",
        "--phases=examples,coverage",
        "--checks=all",
    )
    for data in allure_results(allure_dir):
        # Coverage ran real requests — status must not be skipped
        assert data["status"] != "skipped"
        # And the skip reason must not leak into statusDetails
        assert data.get("statusDetails", {}).get("message") != "No examples in schema"


def test_allure_layer_label(ctx, cli, tmp_path):
    api = ctx.openapi.apps.success()
    allure_dir = tmp_path / "allure-results"
    cli.run_and_assert(api.schema_url, f"--report-allure-path={allure_dir}")
    data = allure_results(allure_dir)[0]
    assert any(lbl["name"] == "layer" and lbl["value"] == "API" for lbl in data["labels"])


def test_allure_path_with_null_byte_reports_clean_error(ctx, cli, snapshot_cli):
    api = ctx.openapi.apps.success()
    assert cli.run(api.schema_url, config={"reports": {"allure": {"path": "d\x00"}}}) == snapshot_cli


def test_allure_path_displayed(ctx, cli, tmp_path, snapshot_cli):
    api = ctx.openapi.apps.success()
    assert (
        cli.run(
            api.schema_url,
            f"--report-allure-path={tmp_path / 'allure-results'}",
            f"--report-junit-path={tmp_path / 'junit.xml'}",
            "--max-examples=1",
        )
        == snapshot_cli
    )


def test_allure_labels_include_api_title_and_severity(ctx, cli, tmp_path):
    api = ctx.openapi.apps.failure()
    allure_dir = tmp_path / "allure-results"
    cli.run_and_assert(
        api.schema_url,
        f"--report-allure-path={allure_dir}",
        "--checks=not_a_server_error",
        "--phases=fuzzing",
        "--max-examples=1",
        exit_code=ExitCode.TESTS_FAILED,
    )
    results = allure_results(allure_dir)
    assert [{(lbl["name"], lbl["value"]) for lbl in result["labels"]} for result in results] == [
        {
            ("story", "GET /api/failure"),
            ("framework", "schemathesis"),
            ("layer", "API"),
            ("epic", api.spec["info"]["title"]),
            ("severity", "blocker"),
        }
    ]
