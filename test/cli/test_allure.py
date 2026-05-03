import json

import pytest
from _pytest.main import ExitCode


def test_allure_result_files_written(ctx, cli, tmp_path):
    api = ctx.openapi.apps.success_and_failure()
    allure_dir = tmp_path / "allure-results"
    cli.run_and_assert(
        api.schema_url,
        f"--report-allure-path={allure_dir}",
        "--checks=not_a_server_error",
        exit_code=ExitCode.TESTS_FAILED,
    )
    result_files = list(allure_dir.glob("*-result.json"))
    assert len(result_files) >= 1
    for f in result_files:
        data = json.loads(f.read_text())
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
    results = [json.loads(f.read_text()) for f in allure_dir.glob("*-result.json")]
    failed = [r for r in results if r["status"] == "failed"]
    assert failed, "Expected at least one failed result"
    step_messages = [s["statusDetails"]["message"] for r in failed for s in r.get("steps", [])]
    assert step_messages
    assert any("curl" in m.lower() for m in step_messages)


def test_allure_no_report_without_flag(ctx, cli, tmp_path):
    api = ctx.openapi.apps.success()
    cli.run_and_assert(api.schema_url)
    assert not list(tmp_path.glob("**/*-result.json"))


@pytest.mark.operations("invalid")
def test_allure_broken_status_on_non_fatal_error(cli, schema_url, tmp_path):
    allure_dir = tmp_path / "allure-results"
    cli.run_and_assert(schema_url, f"--report-allure-path={allure_dir}", exit_code=ExitCode.TESTS_FAILED)
    results = [json.loads(f.read_text()) for f in allure_dir.glob("*-result.json")]
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
    )
    result_files = list(allure_dir.glob("*-result.json"))
    assert result_files
    for data in (json.loads(f.read_text()) for f in result_files):
        assert data["status"] in ("passed", "failed", "broken", "skipped")
        assert not any(lbl["name"] == "feature" for lbl in data["labels"])


def test_allure_layer_label(ctx, cli, tmp_path):
    api = ctx.openapi.apps.success()
    allure_dir = tmp_path / "allure-results"
    cli.run_and_assert(api.schema_url, f"--report-allure-path={allure_dir}")
    result_files = list(allure_dir.glob("*-result.json"))
    assert result_files
    data = json.loads(result_files[0].read_text())
    assert any(lbl["name"] == "layer" and lbl["value"] == "API" for lbl in data["labels"])
