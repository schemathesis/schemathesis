from __future__ import annotations

import json
import re
import time
import uuid
from io import StringIO
from xml.etree import ElementTree

import pytest
import yaml
from _pytest.main import ExitCode
from flask import jsonify, request

import schemathesis
import schemathesis.cli
from schemathesis.cli.commands.fuzz import executor as fuzz_executor
from schemathesis.cli.commands.fuzz.context import FuzzExecutionContext
from schemathesis.cli.commands.run.handlers.allure import AllureHandler
from schemathesis.cli.commands.run.handlers.base import EventHandler
from schemathesis.cli.commands.run.handlers.junitxml import JunitXMLHandler
from schemathesis.config import SchemathesisConfig
from schemathesis.core.failures import ServerError
from schemathesis.core.statistic import ApiStatistic
from schemathesis.core.transport import Response
from schemathesis.engine import Status, StopReason, events
from schemathesis.engine.events import EngineFinished
from schemathesis.engine.fuzz._executor import FUZZ_TESTS_LABEL
from schemathesis.engine.recorder import ScenarioRecorder
from schemathesis.engine.statistic import Statistic
from schemathesis.engine.summary import SummaryData, WarningData
from test.apps.fragments import handlers
from test.apps.fragments.handlers import null_name_lookup
from test.apps.fragments.schemas import linked_users, null_name_response, null_name_responses
from test.utils import allure_results, assert_cli_snapshot


class _RaisingEngine:
    def fuzz(self, *args, **kwargs):
        raise RuntimeError("internal error")


USERS_OK_PATHS = {"/users": {"get": {"responses": {"200": {"description": "OK"}}}}}


def _parse_operations_summary(output):
    block = output.partition("API Operations:\n")[2].partition("\n\n")[0]
    counts = {
        label.lower(): int(value)
        for label, value in re.findall(r"^  (Selected|Tested|Skipped|Errored): (\d+)", block, re.MULTILINE)
    }
    reasons = set(re.findall(r"^    - (.+)", block, re.MULTILINE))
    return counts, reasons


def _make_fuzz_failure_app(ctx, app_runner):
    app, _ = ctx.openapi.make_flask_app(USERS_OK_PATHS)

    @app.route("/users")
    def users():
        return jsonify({}), 500

    return app_runner.openapi_url(app)


def _make_multi_operation_failure_app(ctx, app_runner):
    paths = {f"/failure/{idx}": {"get": {"responses": {"200": {"description": "OK"}}}} for idx in range(3)}
    app, _ = ctx.openapi.make_flask_app(paths)

    for idx in range(3):
        app.add_url_rule(f"/failure/{idx}", endpoint=f"failure_{idx}", view_func=lambda: ({}, 500))

    return app_runner.openapi_url(app)


def _make_slow_fuzz_app(ctx, app_runner):
    app, _ = ctx.openapi.make_flask_app(USERS_OK_PATHS)

    @app.route("/users")
    def users():
        time.sleep(0.1)
        return jsonify([])

    return app_runner.openapi_url(app)


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_fuzz_final_line_when_no_checks_ran(ctx, cli, snapshot_cli):
    api = ctx.openapi.apps.success()
    result = cli.main("fuzz", api.schema_url, "--max-time=30", config={"checks": {"enabled": False}})
    assert result == snapshot_cli
    counts, reasons = _parse_operations_summary(result.stdout)
    assert counts["selected"] == counts["tested"] + counts["skipped"] + counts.get("errored", 0), result.stdout
    assert "No checks ran" in reasons, result.stdout


def test_fuzz_operations_summary_accounts_for_failure_limit(ctx, cli, app_runner):
    url = _make_multi_operation_failure_app(ctx, app_runner)

    result = cli.main("fuzz", url, "--max-time=30", "--checks=not_a_server_error")
    counts, reasons = _parse_operations_summary(result.stdout)

    assert counts["skipped"] > 0, result.stdout
    assert counts["selected"] == counts["tested"] + counts["skipped"] + counts.get("errored", 0), result.stdout
    assert "Failure limit reached" in reasons, result.stdout


# A step left unanswered when the failure limit stops the scenario never had a chance to run checks.
def test_fuzz_operations_summary_unanswered_step_is_skipped_by_failure_limit(ctx, response_factory):
    ok = {"get": {"responses": {"200": {"description": "OK"}}}}
    schema = ctx.openapi.load_schema({"/answered": ok, "/unanswered": ok})
    answered = schema["/answered"]["GET"].Case()
    unanswered = schema["/unanswered"]["GET"].Case()
    recorder = ScenarioRecorder(label=FUZZ_TESTS_LABEL)
    recorder.record_case(parent_id=None, case=answered, transition=None, is_transition_applied=False)
    recorder.record_response(
        case_id=answered.id,
        response=response_factory.core(content_type=None, url="http://127.0.0.1/answered"),
    )
    recorder.record_check_success(name="not_a_server_error", case_id=answered.id)
    recorder.record_case(parent_id=answered.id, case=unanswered, transition=None, is_transition_applied=False)
    statistic = Statistic()
    statistic.on_scenario_finished(recorder, failure_label=lambda case: case.operation.label)
    api_statistic = ApiStatistic()
    api_statistic.operations.total = api_statistic.operations.selected = 2

    operations = SummaryData.from_run(
        api_statistic=api_statistic,
        statistic=statistic,
        errors=set(),
        phases={},
        skip_reasons={},
        stop_reason=StopReason.FAILURE_LIMIT,
        warnings=WarningData(),
        error_phases=None,
    ).operations

    assert (operations.tested, operations.skipped, operations.skip_reasons) == (1, 1, ["Failure limit reached"])


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_fuzz_filter_case_rejects_all(ctx, cli, snapshot_cli):
    api = ctx.openapi.apps.success()
    module = ctx.write_pymodule(
        """
@schemathesis.hook
def filter_case(context, case):
    return False
"""
    )
    assert cli.main("fuzz", api.schema_url, "--max-time=30", hooks=module) == snapshot_cli


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_fuzz_filter_case_rejects_after_first_accepted(ctx, cli, snapshot_cli):
    api = ctx.openapi.apps.success()
    module = ctx.write_pymodule(
        """
CALLS = []

@schemathesis.hook
def filter_case(context, case):
    CALLS.append(1)
    return len(CALLS) <= 1
"""
    )
    # The run completes once the hook stops accepting; the time limit only bounds slow runners.
    assert cli.main("fuzz", api.schema_url, "--max-time=30", hooks=module) == snapshot_cli


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_fuzz_after_run_check_failure(cli, app_runner, ctx, snapshot_cli, restore_checks):
    url = _make_fuzz_app(ctx, app_runner)
    module = ctx.write_pymodule(
        """
@schemathesis.check
class NoServerErrors:
    def after_run(self, ctx):
        raise AssertionError("after_run fired in fuzz")
        """
    )
    assert_cli_snapshot(cli.main("fuzz", url, "-c", "NoServerErrors", "--max-time=30", hooks=module), snapshot_cli)


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_fuzz_final_line_with_failure(cli, app_runner, ctx, tmp_path, snapshot_cli):
    url = _make_fuzz_failure_app(ctx, app_runner)
    xml_path = tmp_path / "junit.xml"
    allure_dir = tmp_path / "allure-results"
    result = cli.main(
        "fuzz", url, "--max-time=30", f"--report-junit-path={xml_path}", f"--report-allure-path={allure_dir}"
    )
    assert_cli_snapshot(result, snapshot_cli)
    testcase = next(tc for tc in ElementTree.parse(xml_path).getroot()[0] if tc.attrib["name"] == "GET /users")
    assert testcase[0].tag == "failure"
    assert "Server error" in ((testcase[0].text or "") + testcase[0].attrib.get("message", ""))
    results = allure_results(allure_dir)
    failure = next(item for item in results if item["name"] == "GET /users")
    assert failure["status"] == "failed"
    assert any("Server error" in step["statusDetails"]["message"] for step in failure.get("steps", []))


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_fuzz_final_line_with_error(cli, app_runner, ctx, snapshot_cli):
    url = _make_slow_fuzz_app(ctx, app_runner)
    assert_cli_snapshot(
        cli.main("fuzz", url, "--max-time=30", "--request-timeout=0.001"),
        snapshot_cli,
    )


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_fuzz_interrupted(cli, app_runner, ctx, snapshot_cli):
    app, _ = ctx.openapi.make_flask_app(
        {
            "/items": {
                "get": {
                    "parameters": [{"name": "q", "in": "query", "required": True, "schema": {"type": "string"}}],
                    "responses": {"200": {"description": "OK"}},
                }
            }
        }
    )

    @app.route("/items")
    def items():
        return jsonify({})

    module = ctx.write_pymodule(
        """
calls = 0

@schemathesis.hook
def after_call(context, case, response):
    global calls
    calls += 1
    if calls > 20:
        raise KeyboardInterrupt
"""
    )
    assert_cli_snapshot(
        cli.main(
            "fuzz",
            app_runner.openapi_url(app),
            "--checks=not_a_server_error",
            "--max-time=30",
            "--workers=1",
            hooks=module,
        ),
        snapshot_cli,
    )


@pytest.mark.parametrize(
    ("make_app", "args", "expected"),
    [
        (
            _make_fuzz_failure_app,
            [],
            r"\d+:\d{2}:\d{2} \* [\d.]+/s \* \d+ scenarios?\n\n    ❌ (?:1 unique failure|(?!1 )\d+ unique failures)\n    Last new failure: \d+\.\d{2}s ago\n",
        ),
        (
            _make_slow_fuzz_app,
            ["--request-timeout=0.001"],
            r"\d+:\d{2}:\d{2} \* [\d.]+/s \* \d+ scenarios?\n\n    🚫 1 error\n    Last new failure: none yet\n",
        ),
    ],
    ids=["failures", "errors"],
)
def test_fuzz_live_progress_on_terminal(cli, app_runner, ctx, make_app, args, expected):
    url = make_app(ctx, app_runner)
    result = cli.main("fuzz", url, "--max-time=30", *args, env={"PYTEST_VERSION": None, "FORCE_COLOR": "1"})
    assert re.search(expected, result.stdout)


def test_fuzz_unreachable_server_has_no_passed_scenarios(cli, ctx, snapshot_cli):
    api = ctx.openapi.apps.success()

    result = cli.main("fuzz", api.schema_url, "--url=http://127.0.0.1:1/api", "--max-time=30")
    assert_cli_snapshot(result, snapshot_cli)
    assert re.search(r"Test cases:\n  (\d+) generated, \1 errored\n", result.stdout), result.stdout


@pytest.mark.snapshot(replace_test_cases=False, replace_traceback=True)
def test_fuzz_crashed_check_cases_are_reported_as_errored(cli, ctx, tmp_path, snapshot_cli):
    api = ctx.openapi.apps.success()
    xml_path = tmp_path / "junit.xml"
    allure_dir = tmp_path / "allure-results"
    with ctx.check(
        """
@schemathesis.check
def broken_check(ctx, response, case):
    raise ZeroDivisionError("boom")
"""
    ) as module:
        result = cli.main(
            "fuzz",
            api.schema_url,
            "--checks=broken_check",
            f"--report-junit-path={xml_path}",
            f"--report-allure-path={allure_dir}",
            hooks=module,
        )
        assert result == snapshot_cli
    testcases = list(ElementTree.parse(xml_path).getroot()[0])
    assert [(testcase.attrib["name"], testcase[0].tag) for testcase in testcases] == [("Fuzz tests", "error")]
    results = allure_results(allure_dir)
    assert [(item["name"], item["status"]) for item in results] == [("Fuzz tests", "broken")]


def test_fuzz_budget_too_small_for_startup_still_runs_a_scenario(cli, app_runner, ctx):
    # A budget already spent by the time fuzzing starts should still buy one scenario, not an empty run.
    url = _make_fuzz_app(ctx, app_runner)

    result = cli.main("fuzz", url, "--max-time=0")

    assert "No scenarios were run" not in result.stdout, result.stdout
    assert "Tested: 1" in result.stdout, result.stdout


@pytest.mark.parametrize("max_time", ["0", "30"], ids=["time-limit-reached", "time-left"])
@pytest.mark.snapshot(replace_reproduce_with=True, replace_traceback=True)
def test_fuzz_reports_error_that_does_not_repeat(cli, app_runner, ctx, snapshot_cli, max_time):
    url = _make_fuzz_app(ctx, app_runner)
    with ctx.check(
        """
calls = 0

@schemathesis.check
def crashes_once(ctx, response, case):
    global calls
    calls += 1
    if calls == 1:
        raise RuntimeError("First call crashed")
"""
    ) as module:
        assert (
            cli.main("fuzz", url, f"--max-time={max_time}", "--workers=1", "--checks=crashes_once", hooks=module)
            == snapshot_cli
        )


def test_fuzz_single_scenario_is_singular(cli, app_runner, ctx):
    url = _make_fuzz_app(ctx, app_runner)

    result = cli.main("fuzz", url, "--max-time=0", "--workers=1")

    assert [line for line in result.stdout.splitlines() if "✅" in line and "scenario" in line] == [
        "     ✅ 1 scenario"
    ]


# Steps after the first failure in a scenario were already sent, so their responses are checked too.
def test_fuzz_checks_steps_after_a_failure(cli, app_runner, ctx):
    app, _ = ctx.openapi.make_flask_app(
        {f"/{name}": {"get": {"responses": {"200": {"description": "OK"}}}} for name in ("failure", "a", "b", "c")}
    )
    requested = set()

    @app.route("/<name>")
    def endpoint(name):
        requested.add(name)
        return (jsonify({}), 500) if name == "failure" else jsonify({})

    result = cli.main("fuzz", app_runner.openapi_url(app), "--max-time=30")

    assert "skipped" not in result.stdout, result.stdout
    assert f"Tested: {len(requested)}\n" in result.stdout, result.stdout


# Confirming that auth is enforced sends probe requests that join the scenario while its steps are checked.
@pytest.mark.snapshot(replace_reproduce_with=True)
def test_fuzz_with_enforced_auth(cli, app_runner, ctx, snapshot_cli):
    app, _ = ctx.openapi.make_flask_app(
        {
            "/items": {
                "get": {
                    "security": [{"key": []}],
                    "responses": {"200": {"description": "OK"}, "401": {"description": "Unauthorized"}},
                }
            }
        },
        components={"securitySchemes": {"key": {"type": "apiKey", "in": "header", "name": "X-Key"}}},
    )

    @app.route("/items")
    def items():
        if request.headers.get("X-Key") != "secret":
            return jsonify({"detail": "unauthorized"}), 401
        return jsonify([])

    result = cli.main("fuzz", app_runner.openapi_url(app), "-H", "X-Key: secret", "--max-time=0", "--workers=1")
    assert_cli_snapshot(result, snapshot_cli)


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_fuzz_final_line_empty_test_suite(cli, app_runner, ctx, snapshot_cli):
    url = _make_fuzz_app(ctx, app_runner)
    assert_cli_snapshot(cli.main("fuzz", url, "--include-path=/nonexistent"), snapshot_cli)


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_fuzz_unmatched_filter_suggests_the_operation_it_nearly_names(cli, ctx, snapshot_cli):
    api = ctx.openapi.apps.success()
    assert_cli_snapshot(cli.main("fuzz", api.schema_url, "--include-path=/api/succes"), snapshot_cli)


@pytest.mark.parametrize(
    ("config", "expected", "final_line"),
    [
        ({"warnings": {"fail-on": ["unmatched_filter"]}}, ExitCode.TESTS_FAILED, "Warnings fail the run in"),
        ({}, ExitCode.OK, "1 warning in"),
    ],
    ids=["fail-on", "default"],
)
def test_fuzz_unmatched_filter_with_fail_on(cli, ctx, config, expected, final_line):
    api = ctx.openapi.apps.success()
    result = cli.main(
        "fuzz", api.schema_url, "--include-path=/api/success", "--include-path=/api/nope", "--max-time=1", config=config
    )
    assert (result.exit_code, "Unmatched filters" in result.stdout, final_line in result.stdout.splitlines()[-1]) == (
        expected,
        True,
        True,
    ), result.stdout


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_fuzz_fatal_error_loader(cli, tmp_path, snapshot_cli):
    assert_cli_snapshot(
        cli.main("fuzz", "http://127.0.0.1:1/openapi.json", f"--report-junit-path={tmp_path / 'junit.xml'}"),
        snapshot_cli,
    )


def test_fuzz_fatal_error_loader_omits_wait_for_schema_tip_when_set(cli):
    result = cli.main("fuzz", "http://127.0.0.1:1/openapi.json", "--wait-for-schema=1")

    assert "Connection failed" in result.stdout, result.stdout
    assert "Tip:" not in result.stdout, result.stdout


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_fuzz_rejects_conflicting_color_flags(cli, snapshot_cli):
    assert_cli_snapshot(
        cli.main("fuzz", "http://127.0.0.1:1/openapi.json", "--no-color", "--force-color"), snapshot_cli
    )


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_fuzz_schema_file_with_malformed_server_asks_for_url(cli, ctx, snapshot_cli):
    # A server URL that cannot be read gives no target to suggest.
    schema_path = ctx.openapi.write_schema(USERS_OK_PATHS, servers=[{"url": 5}])
    assert_cli_snapshot(cli.main("fuzz", str(schema_path)), snapshot_cli)


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_fuzz_fatal_error_internal(cli, app_runner, ctx, snapshot_cli, monkeypatch):
    url = _make_fuzz_app(ctx, app_runner)
    monkeypatch.setattr(fuzz_executor, "from_schema", lambda schema: _RaisingEngine())

    assert_cli_snapshot(cli.main("fuzz", url), snapshot_cli)


def _make_fuzz_app(ctx, app_runner):
    app, _ = ctx.openapi.make_flask_app(USERS_OK_PATHS)

    @app.route("/users")
    def users():
        return jsonify([])

    return app_runner.openapi_url(app)


def _make_unsatisfiable_fuzz_app(ctx, app_runner):
    app, _ = ctx.openapi.make_flask_app(
        {
            "/users": {
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {"schema": {"allOf": [{"type": "integer"}, {"type": "string"}]}}
                        },
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            }
        }
    )

    @app.route("/users", methods=["POST"])
    def users_post():
        return jsonify({"ok": True})

    return app_runner.openapi_url(app)


def _make_fuzz_failure_event(operation, response_factory):
    case = operation.Case()
    recorder = ScenarioRecorder(label=FUZZ_TESTS_LABEL)
    recorder.record_case(parent_id=None, case=case, transition=None, is_transition_applied=False)
    recorder.record_response(
        case_id=case.id, response=Response.from_requests(response_factory.requests(status_code=500), True)
    )
    recorder.record_check_failure(
        name="not_a_server_error",
        case_id=case.id,
        code_sample="curl 127.0.0.1",
        failure=ServerError(operation=operation.label, status_code=500, case_id=case.id),
    )
    return events.FuzzScenarioFinished(
        id=uuid.uuid4(),
        suite_id=uuid.uuid4(),
        worker_id=0,
        recorder=recorder,
        status=Status.FAILURE,
        elapsed_time=0.1,
    )


def _make_fuzz_multi_operation_event(ctx, response_factory):
    schema = ctx.openapi.load_schema(
        {
            "/users": {"get": {"responses": {"200": {"description": "OK"}}}},
            "/orders": {"get": {"responses": {"200": {"description": "OK"}}}},
        }
    )
    recorder = ScenarioRecorder(label=FUZZ_TESTS_LABEL)
    elapsed_by_label = {"GET /users": 0.2, "GET /orders": 0.3}

    for path in ("/users", "/orders"):
        operation = schema[path]["GET"]
        case = operation.Case()
        recorder.record_case(parent_id=None, case=case, transition=None, is_transition_applied=False)
        recorder.record_response(
            case_id=case.id,
            response=response_factory.core(
                url=f"http://127.0.0.1{path}", elapsed=elapsed_by_label[operation.label], verify=True, message="OK"
            ),
        )

    event = events.FuzzScenarioFinished(
        id=uuid.uuid4(),
        suite_id=uuid.uuid4(),
        worker_id=0,
        recorder=recorder,
        status=Status.SUCCESS,
        elapsed_time=1.0,
    )
    return event, elapsed_by_label


def test_fuzz_junit_report_does_not_duplicate_old_failures(ctx, response_factory):
    schema = ctx.openapi.load_schema(USERS_OK_PATHS)
    operation = schema["/users"]["GET"]
    execution_ctx = FuzzExecutionContext(config=SchemathesisConfig().projects.get_default())
    stream = StringIO()
    handler = JunitXMLHandler(stream)

    first_event = _make_fuzz_failure_event(operation, response_factory)
    execution_ctx.on_event(first_event)
    handler.handle_event(execution_ctx, first_event)

    second_event = _make_fuzz_failure_event(operation, response_factory)
    execution_ctx.on_event(second_event)
    handler.handle_event(execution_ctx, second_event)
    handler.shutdown(execution_ctx)

    tree = ElementTree.fromstring(stream.getvalue())
    testcase = next(testcase for testcase in tree[0] if testcase.attrib["name"] == "GET /users")

    assert len([child for child in testcase if child.tag == "failure"]) == 1


def test_fuzz_junit_report_uses_recorder_elapsed_per_operation(ctx, response_factory):
    event, elapsed_by_label = _make_fuzz_multi_operation_event(ctx, response_factory)
    execution_ctx = FuzzExecutionContext(config=SchemathesisConfig().projects.get_default())
    stream = StringIO()
    handler = JunitXMLHandler(stream)

    handler.handle_event(execution_ctx, event)
    handler.shutdown(execution_ctx)

    tree = ElementTree.fromstring(stream.getvalue())
    testcases = {testcase.attrib["name"]: float(testcase.attrib["time"]) for testcase in tree[0]}

    assert testcases == pytest.approx(elapsed_by_label)


def test_fuzz_allure_report_uses_recorder_elapsed_per_operation(ctx, response_factory, tmp_path):
    event, elapsed_by_label = _make_fuzz_multi_operation_event(ctx, response_factory)
    execution_ctx = FuzzExecutionContext(config=SchemathesisConfig().projects.get_default())
    handler = AllureHandler(output_dir=tmp_path / "allure-results", config=execution_ctx.config.output)

    handler.handle_event(execution_ctx, event)
    handler.shutdown(execution_ctx)

    results = allure_results(tmp_path / "allure-results")
    durations = {item["name"]: (item["stop"] - item["start"]) / 1000 for item in results}

    assert durations == pytest.approx(elapsed_by_label)


def test_fuzz_non_fatal_errors_fail_exit_code(cli, ctx, app_runner):
    url = _make_unsatisfiable_fuzz_app(ctx, app_runner)

    result = cli.main("fuzz", url, "--mode=positive")

    assert result.exit_code == 1, result.output


BAD_PARAMETERS_PATHS = {
    "/bad-params": {"get": {"parameters": {"name": "q"}, "responses": {"200": {"description": "OK"}}}}
}


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_fuzz_reports_operations_with_schema_errors(cli, app_runner, ctx, snapshot_cli):
    app, _ = ctx.openapi.make_flask_app({**USERS_OK_PATHS, **BAD_PARAMETERS_PATHS})

    @app.route("/users")
    def users():
        return jsonify([])

    url = app_runner.openapi_url(app)
    assert_cli_snapshot(cli.main("fuzz", url, "--max-time=30"), snapshot_cli)


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_fuzz_all_operations_have_schema_errors(cli, app_runner, ctx, snapshot_cli):
    app, _ = ctx.openapi.make_flask_app(BAD_PARAMETERS_PATHS)
    url = app_runner.openapi_url(app)
    assert_cli_snapshot(cli.main("fuzz", url, "--max-time=30"), snapshot_cli)


def test_fuzz_section_max_time_overrides_root(cli, ctx, app_runner, tmp_path):
    app, _ = ctx.openapi.make_flask_app(
        {
            "/users": {
                "get": {
                    "parameters": [{"name": "q", "in": "query", "required": True, "schema": {"type": "string"}}],
                    "responses": {"200": {"description": "OK"}},
                }
            }
        }
    )

    @app.route("/users")
    def users():
        return jsonify([])

    report_path = tmp_path / "report.json"
    result = cli.main(
        "fuzz",
        app_runner.openapi_url(app),
        "--mode=positive",
        f"--report-json-path={report_path}",
        config={"max-time": 60, "fuzz": {"max-time": 1}},
    )
    report = json.loads(report_path.read_text())
    assert (result.exit_code, report["stop_reason"], report["running_time"] < 30) == (0, "max_time", True), (
        result.output
    )


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_fuzz_custom_handler_error(cli, app_runner, ctx, snapshot_cli):
    @schemathesis.cli.handler()
    class BrokenHandler(EventHandler):
        def handle_event(self, run_ctx, event) -> None:
            raise AttributeError("oops")

    url = _make_fuzz_app(ctx, app_runner)
    assert_cli_snapshot(cli.main("fuzz", url, "--max-time=30"), snapshot_cli)


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_fuzz_custom_handler_and_reports(cli, app_runner, ctx, tmp_path, snapshot_cli):
    group = schemathesis.cli.add_group("Fuzz custom group")
    group.add_option("--fuzz-counter", type=int, default=0)

    @schemathesis.cli.handler()
    class SummaryHandler(EventHandler):
        def __init__(self, *args, **params):
            self.counter = params["fuzz_counter"]

        def handle_event(self, run_ctx, event):
            if isinstance(event, schemathesis.cli.LoadingFinished):
                run_ctx.add_initialization_line("custom: fuzzing initialized")
            elif isinstance(event, EngineFinished):
                run_ctx.add_summary_line(f"custom: fuzzing done, counter {self.counter}")
                run_ctx.add_report("Schema coverage", "coverage.html")

    url = _make_fuzz_app(ctx, app_runner)
    result = cli.main(
        "fuzz",
        url,
        "--max-time=30",
        "--fuzz-counter=42",
        f"--report-junit-path={tmp_path / 'junit.xml'}",
        f"--report-vcr-path={tmp_path / 'cassette.yaml'}",
        f"--report-har-path={tmp_path / 'recording.har'}",
        f"--report-ndjson-path={tmp_path / 'events.ndjson'}",
        f"--report-allure-path={tmp_path / 'allure-results'}",
        f"--report-html-path={tmp_path / 'html'}",
    )
    assert_cli_snapshot(result, snapshot_cli)
    ElementTree.parse(tmp_path / "junit.xml")
    assert "http_interactions" in yaml.safe_load((tmp_path / "cassette.yaml").read_text())
    assert "log" in json.loads((tmp_path / "recording.har").read_text())
    assert [json.loads(line) for line in (tmp_path / "events.ndjson").read_text().splitlines() if line]
    assert any((tmp_path / "allure-results").glob("*-result.json"))


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_fuzz_chains_post_with_get_via_link(cli, app_runner, ctx, snapshot_cli):
    # Server-generated productId means GET path can only match if it comes from POST's response.
    paths = {
        "/products": {
            "post": {
                "operationId": "createProduct",
                "responses": {
                    "201": {
                        "description": "Created",
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "required": ["productId"],
                                    "properties": {"productId": {"type": "string"}},
                                }
                            }
                        },
                    }
                },
            }
        },
        "/products/{productId}": {
            "get": {
                "operationId": "getProduct",
                "parameters": [
                    {
                        "name": "productId",
                        "in": "path",
                        "required": True,
                        "schema": {"type": "string"},
                    }
                ],
                "responses": null_name_responses(),
            }
        },
    }
    app, _ = ctx.openapi.make_flask_app(paths)

    products: set[str] = set()

    @app.route("/products", methods=["POST"])
    def create_product():
        product_id = uuid.uuid4().hex
        products.add(product_id)
        return jsonify({"productId": product_id}), 201

    @app.route("/products/<product_id>", methods=["GET"])
    def get_product(product_id):
        return null_name_lookup(products, product_id)

    # The run ends at the first failure; the time limit only stops a regression from hanging the test.
    assert_cli_snapshot(
        cli.main(
            "fuzz",
            app_runner.openapi_url(app),
            "--max-time=120",
            "-c",
            "response_schema_conformance",
        ),
        snapshot_cli,
    )


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_fuzz_chains_via_request_body_link(cli, app_runner, ctx, snapshot_cli):
    # POST /products returns a fixed high-entropy (productId, secret) pair; only when /audit
    # body carries that exact pair does the planted schema violation surface. Random fuzz
    # has no way to guess the pair, so without body-link forwarding the bug is invisible.
    valid_product_id = "alpha-product-7af3-bbfd-4b2d-9b5d"
    valid_secret = "bravo-secret-9c11-c209-1cac-a29f"
    paths = {
        "/products": {
            "post": {
                "operationId": "createProduct",
                "responses": {
                    "201": {
                        "description": "Created",
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "required": ["productId", "secret"],
                                    "properties": {
                                        "productId": {"type": "string"},
                                        "secret": {"type": "string"},
                                    },
                                }
                            }
                        },
                        "links": {
                            "AuditProduct": {
                                "operationId": "auditProduct",
                                "requestBody": "$response.body",
                            }
                        },
                    }
                },
            }
        },
        "/audit": {
            "post": {
                "operationId": "auditProduct",
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "required": ["productId", "secret"],
                                "properties": {
                                    "productId": {"type": "string"},
                                    "secret": {"type": "string"},
                                },
                            }
                        }
                    },
                },
                "responses": {
                    "200": null_name_response(),
                    "400": {"description": "Bad request"},
                },
            }
        },
    }
    app, _ = ctx.openapi.make_flask_app(paths)

    @app.route("/products", methods=["POST"])
    def create_product():
        return jsonify({"productId": valid_product_id, "secret": valid_secret}), 201

    @app.route("/audit", methods=["POST"])
    def audit_product():
        data = request.get_json(silent=True) or {}
        if not isinstance(data, dict):
            return "", 400
        if (data.get("productId"), data.get("secret")) != (valid_product_id, valid_secret):
            return "", 400
        # Planted bug: required `name` is null for valid pairs.
        return jsonify({"name": None}), 200

    assert_cli_snapshot(
        cli.main(
            "fuzz",
            app_runner.openapi_url(app),
            "--max-time=30",
            "-c",
            "response_schema_conformance",
        ),
        snapshot_cli,
    )


def test_fuzz_deadline_does_not_flake_strategy(cli, app_runner, ctx):
    # Time limit tripping mid-scenario must not surface as a Runtime Error.
    paths = {
        "/products": {
            "post": {
                "operationId": "createProduct",
                "responses": {
                    "201": {
                        "description": "Created",
                        "content": {"application/json": {"schema": {"type": "object", "properties": {}}}},
                        "links": {
                            "GetProduct": {
                                "operationId": "getProduct",
                                "parameters": {"productId": "$response.body#/productId"},
                            }
                        },
                    }
                },
            }
        },
        "/products/{productId}": {
            "get": {
                "operationId": "getProduct",
                "parameters": [
                    {"name": "productId", "in": "path", "required": True, "schema": {"type": "string"}},
                ],
                "responses": {"200": {"description": "OK"}, "404": {"description": "Not found"}},
            }
        },
    }
    app, _ = ctx.openapi.make_flask_app(paths)

    @app.route("/products", methods=["POST"])
    def create_product():
        return jsonify({}), 201

    @app.route("/products/<product_id>", methods=["GET"])
    def get_product(product_id):
        return "", 404

    result = cli.main(
        "fuzz",
        app_runner.openapi_url(app),
        "--max-time=5",
        "-c",
        "not_a_server_error",
    )
    assert "FlakyStrategyDefinition" not in result.stdout, result.stdout
    assert "Runtime Error" not in result.stdout, result.stdout
    assert result.exit_code == 0


def test_fuzz_inherits_the_root_time_limit(cli, app_runner, ctx, tmp_path):
    # The root budget covers both commands, so fuzzing stops before the failure this app starts serving.
    app, _ = ctx.openapi.make_flask_app(linked_users())
    started = time.monotonic()
    handlers.register_linked_users_create(app)

    @app.route("/users/<int:user_id>")
    def get_user(user_id):
        return ("", 500) if time.monotonic() - started > 5 else jsonify({"id": user_id})

    config_file = tmp_path / "schemathesis.toml"
    config_file.write_text("max-time = 1")
    result = cli.main(
        f"--config-file={config_file}",
        "fuzz",
        app_runner.openapi_url(app),
        "--max-failures=1",
        "--checks=not_a_server_error",
    )

    assert "Server error" not in result.stdout, result.stdout


@pytest.mark.parametrize(
    ("args", "config", "expected"),
    [
        (("--max-time=10",), None, "Stop reason: Failure limit reached"),
        (("--max-time=10", "--continue-on-failure", "--max-failures=1"), None, "Stop reason: Failure limit reached"),
        (("--max-time=1",), {"continue-on-failure": True}, "Stop reason: Time limit reached"),
    ],
    ids=["first-failure", "max-failures", "continue-on-failure-from-config"],
)
def test_fuzz_stop_reason_after_failure(cli, ctx, args, config, expected):
    api = ctx.openapi.apps.multiple_failures()

    result = cli.main("fuzz", api.schema_url, *args, config=config)

    assert expected in result.stdout, result.stdout


def test_fuzz_generation_database_replays_failure(cli, app_runner, ctx, tmp_path):
    app, _ = ctx.openapi.make_flask_app(
        {
            "/items": {
                "get": {
                    "parameters": [{"name": "id", "in": "query", "required": True, "schema": {"type": "integer"}}],
                    "responses": {"200": {"description": "OK"}},
                }
            }
        }
    )
    received = []
    failing = []

    @app.route("/items")
    def items():
        value = request.args.get("id", type=int)
        received.append(value)
        if value is not None and value > 10_000:
            failing.append(value)
            return jsonify({}), 500
        return jsonify({})

    url = app_runner.openapi_url(app)
    # Each run ends at the first failure; the time limit only stops a regression from hanging the test.
    args = ("--max-time=120", "--checks=not_a_server_error", f"--generation-database={tmp_path / 'examples'}")
    # The run cache replays failures on its own; disabling it leaves the example database as the only source.
    config = {"cache": {"enabled": False}}
    cli.main("fuzz", url, *args, config=config)
    stored_failure = failing[-1]
    received.clear()
    cli.main("fuzz", url, *args, config=config)
    # The replayed scenario may send other steps before the one that failed.
    assert stored_failure in received


def test_fuzz_first_failure_stops_every_worker(cli, ctx, restore_checks):
    # Only the first worker fails; the others would otherwise keep fuzzing until the time limit.
    api = ctx.openapi.apps.payload()
    module = ctx.write_pymodule(
        """
import threading

@schemathesis.check
def fails_in_first_worker(ctx, response, case):
    if threading.current_thread().name == "schemathesis_fuzz_0":
        raise AssertionError("First worker failure")
        """
    )

    result = cli.main(
        "fuzz", api.schema_url, "--workers=4", "--max-time=60", "-c", "fails_in_first_worker", hooks=module
    )

    assert "Stop reason: Failure limit reached" in result.stdout, result.stdout


def test_fuzz_keyboard_interrupt_is_named_in_the_final_line(ctx, cli):
    api = ctx.openapi.apps.success()
    module = ctx.write_pymodule(
        """
@schemathesis.hook
def after_call(context, case, response):
    raise KeyboardInterrupt
"""
    )
    result = cli.main("fuzz", api.schema_url, "--max-time=30", hooks=module)
    assert re.fullmatch(r"=+ Interrupted after \d+\.\d{2}s =+", result.stdout.strip().splitlines()[-1])


PING_PATHS = {"/ping": {"get": {"responses": {"200": {"description": "OK"}}}}}


def test_fuzz_negative_skips_operations_without_negatable_parts(cli, app_runner, ctx):
    app, _ = ctx.openapi.make_flask_app(
        {
            **PING_PATHS,
            "/items": {
                "get": {
                    "parameters": [{"name": "id", "in": "query", "required": True, "schema": {"type": "integer"}}],
                    "responses": {"200": {"description": "OK"}, "400": {"description": "Bad"}},
                }
            },
        }
    )

    @app.route("/items")
    def items():
        return jsonify({}), 400

    result = cli.main("fuzz", app_runner.openapi_url(app), "--mode=negative", "--max-time=0", "--workers=1")

    assert _parse_operations_summary(result.stdout) == (
        {"selected": 2, "tested": 1, "skipped": 1},
        {"Impossible to generate negative test cases"},
    ), result.stdout


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_fuzz_negative_without_negatable_operations(cli, app_runner, ctx, snapshot_cli):
    app, _ = ctx.openapi.make_flask_app(PING_PATHS)
    assert_cli_snapshot(cli.main("fuzz", app_runner.openapi_url(app), "--mode=negative"), snapshot_cli)


# The link fills the only parameter of the follow-up step, so that step has nothing left to negate.
def test_fuzz_negative_link_leaves_nothing_to_negate(cli, app_runner, ctx):
    paths = linked_users()
    paths["/users"]["post"]["parameters"] = [
        {"name": "name", "in": "query", "required": True, "schema": {"type": "integer"}}
    ]
    app, _ = ctx.openapi.make_flask_app(paths)

    @app.route("/users", methods=["POST"])
    def create():
        return jsonify({"id": 1}), 201

    @app.route("/users/<user_id>")
    def get(user_id):
        return jsonify({}), 500

    result = cli.main(
        "fuzz",
        app_runner.openapi_url(app),
        "--mode=negative",
        "--checks=not_a_server_error",
        "--max-time=60",
        "--workers=1",
    )

    assert "Stop reason: Failure limit reached" in result.stdout, result.stdout
    assert "Errored" not in result.stdout, result.stdout
    assert "GET /users/{userId}" in result.stdout, result.stdout


def test_fuzz_negative_skips_graphql_operations_without_negatable_parts(cli, ctx):
    api = ctx.graphql.apps.books()

    result = cli.main("fuzz", api.schema_url, "--mode=negative", "--max-time=0", "--workers=1")

    assert _parse_operations_summary(result.stdout) == (
        {"selected": 4, "tested": 1, "skipped": 3},
        {"Impossible to generate negative test cases for this GraphQL operation", "Time limit reached"},
    ), result.stdout
