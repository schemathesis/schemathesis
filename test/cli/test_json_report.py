import json

import pytest
from _pytest.main import ExitCode


@pytest.fixture
def json_path(tmp_path):
    return tmp_path / "report.json"


def load_report(path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_report_shape(ctx, cli, json_path):
    api = ctx.openapi.apps.success()
    cli.run_and_assert(
        api.schema_url,
        f"--report-json-path={json_path}",
        "--max-examples=1",
        "--phases=fuzzing",
        "--seed=42",
    )
    report = load_report(json_path)
    # Wall-clock and generated-case counts are not stable across runs; everything else is.
    generated = report["test_cases"].pop("generated")
    assert generated >= 1
    assert isinstance(report.pop("running_time"), float)
    assert report.pop("started_at").endswith("+00:00")
    # In-process runs have no `st` argv; the real formatting is covered by the subprocess test in test_ndjson.py.
    assert isinstance(report.pop("command"), str)
    assert report.pop("schemathesis_version")

    assert report == {
        "seed": 42,
        "stop_reason": "completed",
        "complete": True,
        "exit_code": 0,
        "operations": {
            "total": 1,
            "selected": 1,
            "tested": 1,
            "errored": 0,
            "skipped": 0,
            "skip_reasons": [],
        },
        "phases": {
            "examples": {"status": "skip", "skip_reason": "disabled"},
            "coverage": {"status": "skip", "skip_reason": "disabled"},
            "fuzzing": {"status": "success", "skip_reason": None},
            "stateful": {"status": "skip", "skip_reason": "disabled"},
        },
        "test_cases": {"with_failures": 0, "unique_failures": 0, "without_checks": 0},
        "failures": [],
        "errors": [],
        "warnings": {
            "missing_auth": [],
            "missing_test_data": [],
            "validation_mismatch": [],
            "missing_deserializer": [],
            "unused_openapi_auth": [],
            "unsupported_regex": [],
            "method_not_allowed": [],
            "constants_extraction": [],
            "unmatched_filter": [],
        },
        "auth": {"reauth_count": 0, "reauth_broke": False},
    }


def test_report_records_failures(ctx, cli, json_path):
    api = ctx.openapi.apps.failure()
    cli.run_and_assert(
        api.schema_url,
        f"--report-json-path={json_path}",
        "--max-examples=1",
        "--phases=fuzzing",
        "--checks=not_a_server_error",
        exit_code=ExitCode.TESTS_FAILED,
    )
    report = load_report(json_path)
    assert report["exit_code"] == 1
    assert report["failures"] == [
        {
            "type": "ServerError",
            "title": "Server error",
            "severity": "critical",
            "count": 1,
            "operations": ["GET /api/failure"],
        }
    ]


def test_missing_parent_directory(ctx, cli, tmp_path):
    api = ctx.openapi.apps.success()
    target = tmp_path / "missing" / "report.json"
    cli.run_and_assert(
        api.schema_url,
        f"--report-json-path={target}",
        "--max-examples=1",
        "--phases=fuzzing",
    )
    assert load_report(target)["operations"]["tested"] == 1


def test_fuzz_report(ctx, cli, json_path):
    api = ctx.openapi.apps.success()
    cli.main("fuzz", api.schema_url, f"--report-json-path={json_path}", "--max-time=1")
    report = load_report(json_path)
    # `st fuzz` has no phase concept, so the key is present but carries nothing.
    assert report["phases"] is None
    assert report["complete"] is True
    assert report["operations"]["selected"] == 1
    assert report["warnings"]["missing_auth"] == []


def test_engine_never_started(cli, json_path, tmp_path):
    # Schema loading fails, so there is no engine timing to report - not a zero-length run.
    schema = tmp_path / "schema.yaml"
    schema.write_text("{{{ not yaml", encoding="utf-8")
    cli.run(str(schema), f"--report-json-path={json_path}")
    report = load_report(json_path)
    assert report["started_at"] is None
    assert report["running_time"] is None
    assert report["complete"] is False
    assert report["operations"] is None
