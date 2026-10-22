import json

import click
import pytest
from _pytest.main import ExitCode

import schemathesis
from schemathesis.cli.commands.fuzz.handlers.output import FuzzOutputHandler
from schemathesis.cli.events import LoadingFinished
from schemathesis.cli.executor import is_built_in_handler
from schemathesis.cli.json_report import JsonReportHandler
from schemathesis.engine.events import ScenarioFinished


@pytest.fixture
def json_path(tmp_path):
    return tmp_path / "report.json"


def load_report(path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_report_shape(ctx, cli, json_path):
    api = ctx.openapi.apps.success()
    result = cli.run_and_assert(
        api.schema_url,
        f"--report-json-path={json_path}",
        "--max-examples=1",
        "--phases=fuzzing",
        "--seed=42",
    )
    assert f"- JSON: {json_path}" in result.stdout
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
        "test_cases": {"with_failures": 0, "unique_failures": 0, "without_checks": 0, "errored": 0},
        "failures": [],
        "errors": [],
        "warnings": {
            "missing_auth": [],
            "base_url_mismatch": [],
            "missing_test_data": [],
            "validation_mismatch": [],
            "missing_deserializer": [],
            "unused_openapi_auth": [],
            "unsupported_regex": [],
            "method_not_allowed": [],
            "constants_extraction": [],
            "unmatched_filter": [],
            "unresolvable_reference": [],
            "low_valid_rate": [],
            "rate_limited": [],
            "dictionary_mismatch": [],
        },
        "valid_rates": {
            "GET /api/success": {"fuzzing": {"accepted": 1, "unreachable": 0, "conflicts": 0, "rejected": 0}}
        },
        "baseline": None,
        "filtered": 0,
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
            "code": "ST101",
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


def test_report_for_filters_matching_nothing(ctx, cli, json_path):
    api = ctx.openapi.apps.success()
    cli.run_and_assert(
        api.schema_url,
        f"--report-json-path={json_path}",
        "--include-path=/does-not-exist",
        exit_code=2,
    )
    report = load_report(json_path)
    assert (report["exit_code"], report["complete"], report["operations"]) == (
        2,
        True,
        {"total": 1, "selected": 0, "tested": 0, "errored": 0, "skipped": 0, "skip_reasons": []},
    )


LOADING_ERRORS = [{"title": "Schema Loading Error", "count": 1}]


@pytest.fixture
def schema_files(ctx, tmp_path):
    malformed = tmp_path / "broken.json"
    malformed.write_text('{"openapi": "3.0.0", "info": {', encoding="utf-8")
    valid = ctx.openapi.write_schema({"/users": {"get": {"responses": {"200": {"description": "OK"}}}}})
    return {"malformed": str(malformed), "valid": str(valid)}


@pytest.mark.parametrize("command", ["run", "fuzz"])
@pytest.mark.parametrize(
    ("args", "errors"),
    [
        pytest.param(("{malformed}", "--url=http://127.0.0.1:1"), LOADING_ERRORS, id="malformed-schema"),
        pytest.param(("http://127.0.0.1:1/openapi.json",), LOADING_ERRORS, id="unreachable-schema"),
        # A usage error, not a run error: the terminal prints it without an error title.
        pytest.param(("{valid}",), [], id="missing-base-url"),
    ],
)
def test_fatal_error_records_process_exit_code(cli, json_path, schema_files, command, args, errors):
    args = [arg.format(**schema_files) for arg in args]
    result = cli.main(command, *args, f"--report-json-path={json_path}")
    report = load_report(json_path)
    assert (result.exit_code, report["exit_code"], report["stop_reason"], report["errors"]) == (
        2,
        2,
        "error",
        errors,
    ), result.stdout
    # Schema loading fails, so there is no engine timing to report - not a zero-length run.
    assert (report["started_at"], report["running_time"], report["complete"], report["operations"]) == (
        None,
        None,
        False,
        None,
    )


@pytest.mark.parametrize("command", [("run", "--max-examples=1"), ("fuzz", "--max-time=1")], ids=["run", "fuzz"])
def test_handler_error_records_process_exit_code(ctx, cli, json_path, command):
    @schemathesis.cli.handler()
    class Broken(schemathesis.cli.EventHandler):
        def handle_event(self, run_ctx, event) -> None:
            if isinstance(event, LoadingFinished):
                raise RuntimeError("oops")

    api = ctx.openapi.apps.success()
    result = cli.main(command[0], api.schema_url, command[1], f"--report-json-path={json_path}")
    assert "CLI Handler Error" in result.stdout
    report = load_report(json_path)
    assert (result.exit_code, report["exit_code"], report["stop_reason"]) == (2, 2, "error")


def test_handler_error_mid_run_records_running_time(ctx, cli, json_path):
    @schemathesis.cli.handler()
    class BrokenOnScenario(schemathesis.cli.EventHandler):
        def handle_event(self, run_ctx, event) -> None:
            if isinstance(event, ScenarioFinished):
                raise RuntimeError("oops")

    api = ctx.openapi.apps.success()
    result = cli.main("run", api.schema_url, "--max-examples=1", f"--report-json-path={json_path}")
    report = load_report(json_path)
    assert (
        result.exit_code,
        report["exit_code"],
        report["stop_reason"],
        isinstance(report["running_time"], float),
    ) == (
        2,
        2,
        "error",
        True,
    ), result.stdout


@pytest.mark.parametrize("command", [("run", "--max-examples=1"), ("fuzz", "--max-time=1")], ids=["run", "fuzz"])
def test_handler_abort_on_start_records_process_exit_code(ctx, cli, json_path, command):
    @schemathesis.cli.handler()
    class AbortOnStart(schemathesis.cli.EventHandler):
        def start(self, run_ctx) -> None:
            raise click.Abort

    api = ctx.openapi.apps.success()
    result = cli.main(command[0], api.schema_url, command[1], f"--report-json-path={json_path}")
    report = load_report(json_path)
    assert (result.exit_code, report["exit_code"], report["stop_reason"], result.stdout) == (2, 2, "error", "")


def test_hook_error_records_process_exit_code(ctx, cli, json_path):
    module = ctx.write_pymodule(
        """
@schemathesis.hook
def after_load_schema(context, schema):
    raise ZeroDivisionError("oops")
"""
    )
    api = ctx.openapi.apps.success()
    result = cli.main("run", api.schema_url, f"--report-json-path={json_path}", hooks=module)
    report = load_report(json_path)
    assert (result.exit_code, report["exit_code"], report["stop_reason"], report["errors"]) == (
        2,
        2,
        "error",
        [{"title": "Hook Error", "count": 1}],
    ), result.stdout


def test_json_and_fuzz_output_handlers_are_built_in(json_path):
    assert (is_built_in_handler(JsonReportHandler(json_path)), is_built_in_handler(FuzzOutputHandler())) == (True, True)


def test_unique_inputs_are_counted_by_every_phase_that_generates_them(ctx, cli, json_path):
    api = ctx.openapi.apps.success()
    cli.run_and_assert(
        api.schema_url,
        f"--report-json-path={json_path}",
        "--phases=coverage,fuzzing",
        "--max-examples=10",
        "--generation-unique-inputs",
    )
    # The operation takes no input, so both phases generate the same request. Each phase sends it once,
    # and the fuzzing phase records it even though the coverage phase already sent it.
    valid_rates = load_report(json_path)["valid_rates"]["GET /api/success"]
    assert (valid_rates["coverage"]["accepted"], valid_rates["fuzzing"]["accepted"]) == (1, 1)
