from __future__ import annotations

import json
import time
import uuid
from io import StringIO
from xml.etree import ElementTree

import pytest
import requests
import yaml
from flask import jsonify

import schemathesis
import schemathesis.cli
from schemathesis.cli.commands.fuzz import executor as fuzz_executor
from schemathesis.cli.commands.fuzz.context import FuzzExecutionContext
from schemathesis.cli.commands.run.handlers.allure import AllureHandler
from schemathesis.cli.commands.run.handlers.base import EventHandler
from schemathesis.cli.commands.run.handlers.junitxml import JunitXMLHandler
from schemathesis.config import SchemathesisConfig
from schemathesis.core.failures import ServerError
from schemathesis.core.transport import Response
from schemathesis.engine import Status, events
from schemathesis.engine.events import EngineFinished
from schemathesis.engine.fuzz._executor import FUZZ_TESTS_LABEL
from schemathesis.engine.recorder import ScenarioRecorder


class _RaisingEngine:
    def fuzz(self, *args, **kwargs):
        raise RuntimeError("internal error")


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_fuzz_basic(cli, ctx, app_runner, snapshot_cli):
    app, _ = ctx.openapi.make_flask_app({"/users": {"get": {"responses": {"200": {"description": "OK"}}}}})

    @app.route("/users")
    def users():
        return jsonify([])

    port = app_runner.run_flask_app(app)
    assert cli.main("fuzz", f"http://127.0.0.1:{port}/openapi.json", "--max-time=2") == snapshot_cli


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_fuzz_final_line_with_failure(cli, ctx, app_runner, snapshot_cli):
    app, _ = ctx.openapi.make_flask_app({"/users": {"get": {"responses": {"200": {"description": "OK"}}}}})

    @app.route("/users")
    def users():
        return jsonify({}), 500

    port = app_runner.run_flask_app(app)
    assert cli.main("fuzz", f"http://127.0.0.1:{port}/openapi.json", "--max-time=2", "--seed=42") == snapshot_cli


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_fuzz_final_line_with_error(cli, ctx, app_runner, snapshot_cli):
    app, _ = ctx.openapi.make_flask_app({"/users": {"get": {"responses": {"200": {"description": "OK"}}}}})

    @app.route("/users")
    def users():
        time.sleep(0.1)
        return jsonify([])

    port = app_runner.run_flask_app(app)
    assert (
        cli.main(
            "fuzz", f"http://127.0.0.1:{port}/openapi.json", "--max-time=2", "--request-timeout=0.001", "--seed=42"
        )
        == snapshot_cli
    )


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_fuzz_final_line_empty_test_suite(cli, ctx, app_runner, snapshot_cli):
    app, _ = ctx.openapi.make_flask_app({"/users": {"get": {"responses": {"200": {"description": "OK"}}}}})

    @app.route("/users")
    def users():
        return jsonify([])

    port = app_runner.run_flask_app(app)
    assert cli.main("fuzz", f"http://127.0.0.1:{port}/openapi.json", "--include-path=/nonexistent") == snapshot_cli


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_fuzz_fatal_error_loader(cli, snapshot_cli):
    assert cli.main("fuzz", "http://127.0.0.1:1/openapi.json") == snapshot_cli


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_fuzz_fatal_error_internal(cli, ctx, app_runner, snapshot_cli, monkeypatch):
    app, _ = ctx.openapi.make_flask_app({"/users": {"get": {"responses": {"200": {"description": "OK"}}}}})

    @app.route("/users")
    def users():
        return jsonify([])

    monkeypatch.setattr(fuzz_executor, "from_schema", lambda schema: _RaisingEngine())

    port = app_runner.run_flask_app(app)
    assert cli.main("fuzz", f"http://127.0.0.1:{port}/openapi.json") == snapshot_cli


def _make_fuzz_app(ctx, app_runner):
    app, _ = ctx.openapi.make_flask_app({"/users": {"get": {"responses": {"200": {"description": "OK"}}}}})

    @app.route("/users")
    def users():
        return jsonify([])

    return f"http://127.0.0.1:{app_runner.run_flask_app(app)}/openapi.json"


def _make_fuzz_failure_app(ctx, app_runner):
    app, _ = ctx.openapi.make_flask_app({"/users": {"get": {"responses": {"200": {"description": "OK"}}}}})

    @app.route("/users")
    def users():
        return jsonify({}), 500

    return f"http://127.0.0.1:{app_runner.run_flask_app(app)}/openapi.json"


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

    return f"http://127.0.0.1:{app_runner.run_flask_app(app)}/openapi.json"


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


def _make_fuzz_multi_operation_event(ctx):
    schema = schemathesis.openapi.from_dict(
        ctx.openapi.build_schema(
            {
                "/users": {"get": {"responses": {"200": {"description": "OK"}}}},
                "/orders": {"get": {"responses": {"200": {"description": "OK"}}}},
            }
        )
    )
    recorder = ScenarioRecorder(label=FUZZ_TESTS_LABEL)
    elapsed_by_label = {"GET /users": 0.2, "GET /orders": 0.3}

    for path in ("/users", "/orders"):
        operation = schema[path]["GET"]
        case = operation.Case()
        recorder.record_case(parent_id=None, case=case, transition=None, is_transition_applied=False)
        request = requests.Request("GET", f"http://127.0.0.1{path}").prepare()
        recorder.record_response(
            case_id=case.id,
            response=Response(
                status_code=200,
                headers={"content-type": ["application/json"]},
                content=b"{}",
                request=request,
                elapsed=elapsed_by_label[operation.label],
                verify=True,
                message="OK",
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


def test_fuzz_report_junit(cli, ctx, app_runner, tmp_path):
    url = _make_fuzz_app(ctx, app_runner)
    xml_path = tmp_path / "junit.xml"
    result = cli.main("fuzz", url, "--max-time=2", f"--report-junit-path={xml_path}")
    assert result.exit_code == 0, result.output
    assert xml_path.exists()
    ElementTree.parse(xml_path)


def test_fuzz_report_junit_uses_operation_labels_for_failures(cli, ctx, app_runner, tmp_path):
    url = _make_fuzz_failure_app(ctx, app_runner)
    xml_path = tmp_path / "junit.xml"
    result = cli.main("fuzz", url, "--max-time=2", "--seed=42", f"--report-junit-path={xml_path}")
    assert result.exit_code == 1, result.output

    tree = ElementTree.parse(xml_path)
    testcases = list(tree.getroot()[0])
    testcase = next(tc for tc in testcases if tc.attrib["name"] == "GET /users")
    assert testcase[0].tag == "failure"
    assert "Server error" in ((testcase[0].text or "") + testcase[0].attrib.get("message", ""))


def test_fuzz_report_vcr(cli, ctx, app_runner, tmp_path):
    url = _make_fuzz_app(ctx, app_runner)
    vcr_path = tmp_path / "cassette.yaml"
    result = cli.main("fuzz", url, "--max-time=2", f"--report-vcr-path={vcr_path}")
    assert result.exit_code == 0, result.output
    assert vcr_path.exists()
    cassette = yaml.safe_load(vcr_path.read_text())
    assert "http_interactions" in cassette


def test_fuzz_report_har(cli, ctx, app_runner, tmp_path):
    url = _make_fuzz_app(ctx, app_runner)
    har_path = tmp_path / "recording.har"
    result = cli.main("fuzz", url, "--max-time=2", f"--report-har-path={har_path}")
    assert result.exit_code == 0, result.output
    assert har_path.exists()
    har = json.loads(har_path.read_text())
    assert "log" in har


def test_fuzz_report_ndjson(cli, ctx, app_runner, tmp_path):
    url = _make_fuzz_app(ctx, app_runner)
    ndjson_path = tmp_path / "events.ndjson"
    result = cli.main("fuzz", url, "--max-time=2", f"--report-ndjson-path={ndjson_path}")
    assert result.exit_code == 0, result.output
    assert ndjson_path.exists()
    events = [json.loads(line) for line in ndjson_path.read_text().splitlines() if line]
    assert len(events) > 0


def test_fuzz_report_allure(cli, ctx, app_runner, tmp_path):
    url = _make_fuzz_app(ctx, app_runner)
    allure_dir = tmp_path / "allure-results"
    result = cli.main("fuzz", url, "--max-time=2", f"--report-allure-path={allure_dir}")
    assert result.exit_code == 0, result.output
    assert any(allure_dir.glob("*-result.json"))


def test_fuzz_report_allure_uses_operation_labels_for_failures(cli, ctx, app_runner, tmp_path):
    url = _make_fuzz_failure_app(ctx, app_runner)
    allure_dir = tmp_path / "allure-results"
    result = cli.main("fuzz", url, "--max-time=2", "--seed=42", f"--report-allure-path={allure_dir}")
    assert result.exit_code == 1, result.output

    results = [json.loads(f.read_text()) for f in allure_dir.glob("*-result.json")]
    failure = next(item for item in results if item["name"] == "GET /users")
    assert failure["status"] == "failed"
    assert any("Server error" in step["statusDetails"]["message"] for step in failure.get("steps", []))


def test_fuzz_junit_report_does_not_duplicate_old_failures(ctx, response_factory):
    schema = schemathesis.openapi.from_dict(
        ctx.openapi.build_schema({"/users": {"get": {"responses": {"200": {"description": "OK"}}}}})
    )
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


def test_fuzz_junit_report_uses_recorder_elapsed_per_operation(ctx):
    event, elapsed_by_label = _make_fuzz_multi_operation_event(ctx)
    execution_ctx = FuzzExecutionContext(config=SchemathesisConfig().projects.get_default())
    stream = StringIO()
    handler = JunitXMLHandler(stream)

    handler.handle_event(execution_ctx, event)
    handler.shutdown(execution_ctx)

    tree = ElementTree.fromstring(stream.getvalue())
    testcases = {testcase.attrib["name"]: float(testcase.attrib["time"]) for testcase in tree[0]}

    assert testcases == pytest.approx(elapsed_by_label)


def test_fuzz_allure_report_uses_recorder_elapsed_per_operation(ctx, tmp_path):
    event, elapsed_by_label = _make_fuzz_multi_operation_event(ctx)
    execution_ctx = FuzzExecutionContext(config=SchemathesisConfig().projects.get_default())
    handler = AllureHandler(output_dir=tmp_path / "allure-results", config=execution_ctx.config.output)

    handler.handle_event(execution_ctx, event)
    handler.shutdown(execution_ctx)

    results = [json.loads(file.read_text()) for file in (tmp_path / "allure-results").glob("*-result.json")]
    durations = {item["name"]: (item["stop"] - item["start"]) / 1000 for item in results}

    assert durations == pytest.approx(elapsed_by_label)


def test_fuzz_non_fatal_errors_fail_exit_code(cli, ctx, app_runner):
    url = _make_unsatisfiable_fuzz_app(ctx, app_runner)

    result = cli.main("fuzz", url, "--mode=positive")

    assert result.exit_code == 1, result.output


def test_fuzz_max_time_from_config(cli, ctx, app_runner, monkeypatch):
    url = _make_fuzz_app(ctx, app_runner)
    captured = {}

    def fake_execute(**kwargs):
        captured["fuzz_config"] = kwargs["fuzz_config"]

    monkeypatch.setattr(fuzz_executor, "execute", fake_execute)
    result = cli.main("fuzz", url, "--mode=positive", config={"fuzz": {"max-time": 2}})
    assert result.exit_code == 0, result.output
    assert captured["fuzz_config"].max_time == 2


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_fuzz_custom_handler_error(cli, ctx, app_runner, snapshot_cli):
    @schemathesis.cli.handler()
    class BrokenHandler(EventHandler):
        def handle_event(self, run_ctx, event) -> None:
            raise AttributeError("oops")

    app, _ = ctx.openapi.make_flask_app({"/users": {"get": {"responses": {"200": {"description": "OK"}}}}})

    @app.route("/users")
    def users():
        return jsonify([])

    port = app_runner.run_flask_app(app)
    assert cli.main("fuzz", f"http://127.0.0.1:{port}/openapi.json", "--max-time=2") == snapshot_cli


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_fuzz_custom_handler(cli, ctx, app_runner, snapshot_cli):
    class SummaryHandler(EventHandler):
        def handle_event(self, run_ctx, event):
            if isinstance(event, EngineFinished):
                run_ctx.add_summary_line("custom: fuzzing done")

    schemathesis.cli.handler()(SummaryHandler)
    app, _ = ctx.openapi.make_flask_app({"/users": {"get": {"responses": {"200": {"description": "OK"}}}}})

    @app.route("/users")
    def users():
        return jsonify([])

    port = app_runner.run_flask_app(app)
    assert cli.main("fuzz", f"http://127.0.0.1:{port}/openapi.json", "--max-time=2") == snapshot_cli


def test_fuzz_custom_handler_with_custom_option(ctx, cli, app_runner):
    group = schemathesis.cli.add_group("Fuzz custom group")
    group.add_option("--fuzz-counter", type=int, default=0)

    @schemathesis.cli.handler()
    class EventCounter(EventHandler):
        def __init__(self, *args, **params):
            self.counter = params["fuzz_counter"]

        def handle_event(self, run_ctx, event) -> None:
            if isinstance(event, EngineFinished):
                run_ctx.add_summary_line(f"Counter: {self.counter}")

    app, _ = ctx.openapi.make_flask_app({"/users": {"get": {"responses": {"200": {"description": "OK"}}}}})

    @app.route("/users")
    def users():
        return jsonify([])

    port = app_runner.run_flask_app(app)
    result = cli.main(
        "fuzz",
        f"http://127.0.0.1:{port}/openapi.json",
        "--max-time=2",
        "--fuzz-counter=42",
    )

    assert result.exit_code == 0, result.output
    assert "Counter: 42" in result.output
