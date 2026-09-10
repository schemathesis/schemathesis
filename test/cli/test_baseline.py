import json
from datetime import date

import pytest
from _pytest.main import ExitCode

from schemathesis.baseline import BaselineEntry

BASELINE = "schemathesis-baseline.json"
WITH_BASELINE = {"baseline": BASELINE}
SERVER_ERROR = {
    "operation": "GET /api/failure",
    "check": "not_a_server_error",
    "failure": "ServerError",
    "signature": "500",
}


def write_baseline(tmp_path, *entries):
    (tmp_path / BASELINE).write_text(json.dumps({"format_version": 1, "entries": list(entries)}), encoding="utf-8")


def read_entries(tmp_path):
    return json.loads((tmp_path / BASELINE).read_text())["entries"]


def recorded_operations(tmp_path):
    return [entry["operation"] for entry in read_entries(tmp_path)]


def run(cli, api, *flags, **kwargs):
    return cli.run(api.schema_url, "--checks=not_a_server_error", "--max-examples=1", *flags, **kwargs)


def test_recorded_failure_does_not_fail_the_run(ctx, cli, tmp_path):
    write_baseline(tmp_path, SERVER_ERROR)

    result = run(cli, ctx.openapi.apps.failure(), config=WITH_BASELINE)

    assert result.exit_code == ExitCode.OK, result.stdout


def test_failure_outside_the_baseline_still_fails(ctx, cli, tmp_path):
    write_baseline(tmp_path, {**SERVER_ERROR, "signature": "503"})

    result = run(cli, ctx.openapi.apps.failure(), config=WITH_BASELINE)

    assert result.exit_code == ExitCode.TESTS_FAILED, result.stdout


def test_expired_entry_stops_suppressing(ctx, cli, tmp_path):
    write_baseline(tmp_path, {**SERVER_ERROR, "expires": "2020-01-01"})

    result = run(cli, ctx.openapi.apps.failure(), config=WITH_BASELINE)

    assert result.exit_code == ExitCode.TESTS_FAILED, result.stdout


def test_entry_naming_another_check_does_not_suppress(ctx, cli, tmp_path):
    write_baseline(tmp_path, {**SERVER_ERROR, "check": "status_code_conformance"})

    result = run(cli, ctx.openapi.apps.failure(), config=WITH_BASELINE)

    assert result.exit_code == ExitCode.TESTS_FAILED, result.stdout


def test_no_baseline_configured_is_unchanged(ctx, cli):
    result = run(cli, ctx.openapi.apps.failure())

    assert result.exit_code == ExitCode.TESTS_FAILED, result.stdout


def test_recorded_failure_does_not_fail_the_run_with_continue_on_failure(ctx, cli, tmp_path):
    write_baseline(tmp_path, SERVER_ERROR)

    result = run(cli, ctx.openapi.apps.failure(), "--continue-on-failure", config=WITH_BASELINE)

    assert result.exit_code == ExitCode.OK, result.stdout


def test_new_failure_fails_alongside_a_known_one(ctx, cli, tmp_path):
    write_baseline(tmp_path, SERVER_ERROR)

    result = run(
        cli,
        ctx.openapi.apps.multiple_failures(),
        "--max-examples=10",
        "--continue-on-failure",
        config=WITH_BASELINE,
    )

    assert result.exit_code == ExitCode.TESTS_FAILED, result.stdout


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_known_failure_output(ctx, cli, tmp_path, snapshot_cli):
    write_baseline(tmp_path, SERVER_ERROR, {**SERVER_ERROR, "operation": "GET /api/never-tested"})

    assert run(cli, ctx.openapi.apps.failure(), "--phases=fuzzing", config=WITH_BASELINE) == snapshot_cli


def test_json_report_includes_baseline(ctx, cli, tmp_path):
    unobserved = {**SERVER_ERROR, "operation": "GET /api/never-tested"}
    write_baseline(tmp_path, SERVER_ERROR, unobserved)

    run(cli, ctx.openapi.apps.failure(), "--phases=fuzzing", "--report=json", config=WITH_BASELINE)

    report = json.loads(next((tmp_path / "schemathesis-report").glob("json-*.json")).read_text())
    assert report["baseline"] == {
        "known": 1,
        "new": 0,
        "recorded": None,
        "pruned_ids": None,
        "unobserved": 1,
        "known_ids": [BaselineEntry(**SERVER_ERROR).id],
        "unobserved_ids": [BaselineEntry(**unobserved).id],
        "expired_ids": [],
    }


def test_update_captures_the_failures_it_saw(ctx, cli, tmp_path):
    result = run(cli, ctx.openapi.apps.failure(), "--phases=fuzzing", "--baseline-update", config=WITH_BASELINE)

    # Updating captures what the run found; it does not pass judgement on it.
    assert result.exit_code == ExitCode.TESTS_FAILED, result.stdout
    assert [(entry["operation"], entry["failure"], entry["signature"]) for entry in read_entries(tmp_path)] == [
        ("GET /api/failure", "ServerError", "500")
    ]


def test_recorded_failures_are_green_on_the_next_run(ctx, cli, tmp_path):
    api = ctx.openapi.apps.failure()
    run(cli, api, "--phases=fuzzing", "--baseline-update", config=WITH_BASELINE)

    assert run(cli, api, "--phases=fuzzing", config=WITH_BASELINE).exit_code == ExitCode.OK


def test_update_keeps_user_annotations(ctx, cli, tmp_path):
    write_baseline(tmp_path, {**SERVER_ERROR, "reason": "tracked in API-1", "ticket": "API-1"})

    run(cli, ctx.openapi.apps.failure(), "--phases=fuzzing", "--baseline-update", config=WITH_BASELINE)

    entry = read_entries(tmp_path)[0]
    assert (entry["reason"], entry["ticket"]) == ("tracked in API-1", "API-1")


def test_update_stamps_dates(ctx, cli, tmp_path):
    run(cli, ctx.openapi.apps.failure(), "--phases=fuzzing", "--baseline-update", config=WITH_BASELINE)

    today = date.today().isoformat()
    entry = read_entries(tmp_path)[0]
    assert (entry["first_seen"], entry["last_seen"]) == (today, today)


def test_update_keeps_first_seen_and_refreshes_last_seen(ctx, cli, tmp_path):
    write_baseline(tmp_path, {**SERVER_ERROR, "first_seen": "2020-01-01", "last_seen": "2020-01-02"})

    run(cli, ctx.openapi.apps.failure(), "--phases=fuzzing", "--baseline-update", config=WITH_BASELINE)

    entry = read_entries(tmp_path)[0]
    assert (entry["first_seen"], entry["last_seen"]) == ("2020-01-01", date.today().isoformat())


def test_update_without_a_configured_baseline_is_an_error(ctx, cli):
    result = run(cli, ctx.openapi.apps.failure(), "--baseline-update")

    assert result.exit_code == 2, result.output
    assert "needs a baseline file" in result.output, result.output


def test_update_skips_response_time_failures(ctx, cli, tmp_path):
    # Response time flaps with machine load, so an entry for it would never settle.
    cli.run(
        ctx.openapi.apps.slow().schema_url,
        "--max-response-time=0.001",
        "--max-examples=1",
        "--phases=fuzzing",
        "--baseline-update",
        config=WITH_BASELINE,
    )

    assert read_entries(tmp_path) == []


def test_prune_removes_entries_the_run_disproved(ctx, cli, tmp_path):
    write_baseline(tmp_path, {**SERVER_ERROR, "operation": "GET /api/success"})

    run(cli, ctx.openapi.apps.success(), "--phases=fuzzing", "--baseline-prune", config=WITH_BASELINE)

    assert read_entries(tmp_path) == []


def test_prune_keeps_entries_for_operations_the_run_never_tested(ctx, cli, tmp_path):
    write_baseline(tmp_path, {**SERVER_ERROR, "operation": "GET /api/elsewhere"})

    run(cli, ctx.openapi.apps.success(), "--phases=fuzzing", "--baseline-prune", config=WITH_BASELINE)

    assert recorded_operations(tmp_path) == ["GET /api/elsewhere"]


def test_missing_baseline_is_created_from_the_run(ctx, cli, tmp_path):
    run(cli, ctx.openapi.apps.failure(), f"--baseline={BASELINE}", "--phases=fuzzing")

    assert recorded_operations(tmp_path) == ["GET /api/failure"]


def test_existing_baseline_is_left_alone_without_a_flag(ctx, cli, tmp_path):
    # Absorbing a regression into a committed baseline has to be something you ask for.
    write_baseline(tmp_path, {**SERVER_ERROR, "operation": "GET /api/elsewhere"})
    before = (tmp_path / BASELINE).read_text()

    run(cli, ctx.openapi.apps.failure(), f"--baseline={BASELINE}", "--phases=fuzzing")

    assert (tmp_path / BASELINE).read_text() == before


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_expired_entry_output(ctx, cli, tmp_path, snapshot_cli):
    write_baseline(tmp_path, {**SERVER_ERROR, "expires": "2020-01-01"})

    assert run(cli, ctx.openapi.apps.failure(), "--phases=fuzzing", config=WITH_BASELINE) == snapshot_cli


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_update_run_output(ctx, cli, tmp_path, snapshot_cli):
    assert (
        run(cli, ctx.openapi.apps.failure(), "--phases=fuzzing", "--baseline-update", config=WITH_BASELINE)
        == snapshot_cli
    )


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_prune_run_output(ctx, cli, tmp_path, snapshot_cli):
    write_baseline(tmp_path, {**SERVER_ERROR, "operation": "GET /api/success"})

    assert (
        run(cli, ctx.openapi.apps.success(), "--phases=fuzzing", "--baseline-prune", config=WITH_BASELINE)
        == snapshot_cli
    )
