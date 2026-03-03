from __future__ import annotations

import re
import sys
import threading
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from click.testing import CliRunner
from flask import Flask, jsonify
from hypothesis import HealthCheck, Phase, settings

from schemathesis.cli.commands import schemathesis as schemathesis_cli
from schemathesis.cli.commands.fuzz.continuous_fuzzing import ContinuousFuzzingProgressManager
from schemathesis.cli.commands.fuzz.merged_test import NoValidFuzzOperationsError, build_merged_test
from schemathesis.cli.commands.fuzz.unguided import run_unguided
from schemathesis.cli.output import make_console
from schemathesis.config import ReportFormat
from schemathesis.core.errors import SerializationNotPossible
from schemathesis.core.result import Ok
from schemathesis.engine import Status
from schemathesis.generation.case import Case


@pytest.mark.hypothesis_nested
@pytest.mark.openapi_version("3.0")
@pytest.mark.operations("success")
def test_build_merged_test_runs_against_server(real_app_schema):
    # build_merged_test returns a Hypothesis-decorated test.  We override its
    # settings to limit examples so the test terminates quickly, then call it
    # directly to exercise the full inner loop: draw Case → call() → checks.
    test_fn = build_merged_test(real_app_schema, config=real_app_schema.config)

    generated: list[Case] = []
    original_inner = test_fn.hypothesis.inner_test

    def capturing(case: Case) -> None:
        generated.append(case)
        original_inner(case)

    test_fn.hypothesis.inner_test = capturing
    test_fn._hypothesis_internal_use_settings = settings(
        max_examples=3,
        phases=[Phase.generate],
        deadline=None,
        suppress_health_check=list(HealthCheck),
    )

    test_fn()

    assert len(generated) >= 1
    for case in generated:
        assert case.operation.path == "/success"


@pytest.mark.openapi_version("3.0")
def test_fuzz_extra_data_source_enables_bug_discovery(cli, app_runner, ctx):
    # Verify that executor.py wires the ExtraDataSource into build_merged_test.
    # The ExtraDataSource is pre-populated from schema response examples (static seed IDs),
    # so GET /items/{item_id} is called with a known-valid ID, triggering the schema
    # violation that only manifests when the path parameter matches an existing item.
    # Without the ExtraDataSource wiring, only random IDs are generated and every GET
    # returns 404 so the bug is never triggered.
    known_id = "seed-id-abc123"
    item_schema = {
        "type": "object",
        "properties": {"id": {"type": "string"}, "name": {"type": "string"}},
        "required": ["id", "name"],
    }
    openapi_schema = ctx.openapi.build_schema(
        {
            "/items": {
                "post": {
                    "operationId": "createItem",
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {"name": {"type": "string"}},
                                    "required": ["name"],
                                }
                            }
                        },
                        "required": True,
                    },
                    "responses": {
                        "201": {
                            "description": "Created",
                            "content": {
                                "application/json": {
                                    "schema": item_schema,
                                    # Seed the extra_data_source repository via schema example
                                    "example": {"id": known_id, "name": "Seeded Item"},
                                }
                            },
                            "links": {
                                "GetItem": {
                                    "operationId": "getItem",
                                    "parameters": {"item_id": "$response.body#/id"},
                                }
                            },
                        }
                    },
                }
            },
            "/items/{item_id}": {
                "get": {
                    "operationId": "getItem",
                    "parameters": [{"name": "item_id", "in": "path", "required": True, "schema": {"type": "string"}}],
                    "responses": {
                        "200": {
                            "description": "Success",
                            "content": {"application/json": {"schema": item_schema}},
                        },
                        "404": {"description": "Not found"},
                    },
                }
            },
        }
    )

    app = Flask(__name__)

    @app.route("/openapi.json")
    def get_schema():  # type: ignore[no-untyped-def]
        return jsonify(openapi_schema)

    @app.route("/items", methods=["POST"])
    def create_item():  # type: ignore[no-untyped-def]
        return jsonify({"id": uuid.uuid4().hex, "name": "Item"}), 201

    @app.route("/items/<item_id>")
    def get_item(item_id):  # type: ignore[no-untyped-def]
        if item_id != known_id:
            return "", 404
        # Bug: omits required 'name' — only triggered when known_id is used as path param
        return jsonify({"id": known_id})

    port = app_runner.run_flask_app(app)

    # extra_data_sources.responses is True by default.  executor.py must call
    # schema.create_extra_data_source() and pass it to build_merged_test so that
    # the pre-seeded known_id is used by the GET /items/{item_id} strategy.
    result = cli.main(
        "fuzz",
        f"http://127.0.0.1:{port}/openapi.json",
        "--workers=1",
        "--max-failures=1",
        "-c response_schema_conformance",
        "--mode=positive",
    )
    # The bug on GET /items/{item_id} was triggered: a failure was reported
    assert result.exit_code == 1, result.output
    assert "FAILURES" in result.output


def test_unguided_loop_stops_on_stop_event():
    stop_event = threading.Event()
    call_count = [0]

    def fake_test():
        call_count[0] += 1
        stop_event.set()

    run_unguided(fake_test, n_workers=1, stop_event=stop_event)
    assert call_count[0] >= 1


def test_unguided_loop_continues_on_failure_when_flagged():
    stop_event = threading.Event()
    call_count = [0]

    def failing_test():
        call_count[0] += 1
        raise RuntimeError("simulated failure")

    failures = [0]

    def on_failure(exc: Exception) -> None:
        failures[0] += 1

    run_unguided(
        failing_test,
        n_workers=1,
        stop_event=stop_event,
        on_failure=on_failure,
        continue_on_failure=True,
    )
    assert call_count[0] == 1
    assert failures[0] == 1


def test_continuous_fuzzing_progress_shows_compact_stats_line():
    manager = ContinuousFuzzingProgressManager(console=make_console(), title="Fuzzing", total_operations=5)

    message = manager._get_stats_message()

    assert "\n" in message
    assert " · " in message
    assert "/s" in message
    assert "Time since last unique failure: none yet" in message
    assert "active:" not in message


def test_continuous_fuzzing_progress_tracks_operation_coverage():
    manager = ContinuousFuzzingProgressManager(console=make_console(), title="Fuzzing", total_operations=4)

    manager.update_stats(Status.SUCCESS, label="GET /users", unique_failures=0, non_fatal_errors=0)
    manager.update_stats(Status.FAILURE, label="GET /users", unique_failures=1, non_fatal_errors=0)
    manager.update_stats(Status.SUCCESS, label="POST /users", unique_failures=1, non_fatal_errors=0)

    message = manager._get_stats_message()
    assert "3 test cases" in message
    assert " · " in message
    assert "❌ 1 unique" in message
    assert "Time since last unique failure:" in message
    assert "none yet" not in message
    assert "operations hit:" not in message


@pytest.mark.openapi_version("3.0")
@pytest.mark.operations("success")
def test_build_merged_test_uses_project_hypothesis_settings(real_app_schema):
    real_app_schema.config.generation.update(database="none", no_shrink=False)
    real_app_schema.config.seed = 123

    test_fn = build_merged_test(real_app_schema, config=real_app_schema.config)
    hypothesis_settings = test_fn._hypothesis_internal_use_settings

    assert hypothesis_settings.max_examples == sys.maxsize
    assert Phase.generate in hypothesis_settings.phases
    assert Phase.target in hypothesis_settings.phases
    assert Phase.shrink in hypothesis_settings.phases
    assert Phase.reuse not in hypothesis_settings.phases
    assert test_fn._hypothesis_internal_use_seed == 123


@pytest.mark.openapi_version("3.0")
@pytest.mark.operations("success")
def test_build_merged_test_uses_worker_specific_seed(real_app_schema):
    real_app_schema.config.seed = 123

    worker0 = build_merged_test(real_app_schema, config=real_app_schema.config, worker_id=0)
    worker1 = build_merged_test(real_app_schema, config=real_app_schema.config, worker_id=1)
    worker1_repeat = build_merged_test(real_app_schema, config=real_app_schema.config, worker_id=1)

    assert worker0._hypothesis_internal_use_seed == 123
    assert worker1._hypothesis_internal_use_seed == 124
    assert worker1_repeat._hypothesis_internal_use_seed == 124


@pytest.mark.openapi_version("3.0")
@pytest.mark.operations("success")
def test_build_merged_test_respects_no_shrink(real_app_schema):
    real_app_schema.config.generation.update(database="none", no_shrink=True)

    test_fn = build_merged_test(real_app_schema, config=real_app_schema.config)
    hypothesis_settings = test_fn._hypothesis_internal_use_settings

    assert Phase.shrink not in hypothesis_settings.phases


def test_build_merged_test_skips_operations_with_unsupported_serializers():
    class FakeTransport:
        def get_first_matching_media_type(self, media_type: str):  # type: ignore[no-untyped-def]
            return None

    class FakeSchema:
        def __init__(self):  # type: ignore[no-untyped-def]
            self.transport = FakeTransport()
            self.operation = FakeOperation(schema=self)

        def get_all_operations(self):  # type: ignore[no-untyped-def]
            return iter([Ok(self.operation)])

    class FakeOperation:
        def __init__(self, schema):  # type: ignore[no-untyped-def]
            self.label = "POST /csv"
            self.schema = schema

        def get_request_payload_content_types(self) -> list[str]:
            return ["text/csv"]

        def as_strategy(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            raise AssertionError("Unsupported operation should be excluded before strategy creation")

    schema = FakeSchema()
    invalid: list[tuple[str, Exception]] = []

    with pytest.raises(NoValidFuzzOperationsError):
        build_merged_test(
            schema,  # type: ignore[arg-type]
            config=SimpleNamespace(),
            on_invalid_operation=lambda label, error: invalid.append((label, error)),
        )

    assert invalid
    assert invalid[0][0] == "POST /csv"
    assert isinstance(invalid[0][1], SerializationNotPossible)


@pytest.mark.openapi_version("3.0")
@pytest.mark.operations("success")
def test_fuzz_custom_handler_receives_custom_cli_options(ctx, cli, schema_url):
    module = ctx.write_pymodule(
        r"""
from schemathesis import cli, engine

group = cli.add_group("My custom group")
group.add_option("--custom-counter", type=int)

@cli.handler()
class EventCounter(cli.EventHandler):
    def __init__(self, *args, **params):
        self.counter = params["custom_counter"] or 0

    def handle_event(self, ctx, event) -> None:
        if isinstance(event, engine.events.EngineFinished):
            ctx.add_summary_line(f"Counter: {self.counter}")
"""
    )
    result = cli.main(
        "fuzz",
        schema_url,
        "--workers=1",
        "--include-method=DELETE",
        hooks=module,
    )
    assert result.exit_code == 0, result.output
    assert "Counter: 0" in result.output


@pytest.mark.openapi_version("3.0")
@pytest.mark.operations("success")
def test_fuzz_custom_handler_receives_run_only_option_defaults(ctx, cli, schema_url):
    module = ctx.write_pymodule(
        r"""
from schemathesis import cli, engine

@cli.handler()
class EventCounter(cli.EventHandler):
    def __init__(self, *args, **params):
        self.phases = params["phases"]

    def handle_event(self, ctx, event) -> None:
        if isinstance(event, engine.events.EngineFinished):
            ctx.add_summary_line(f"Phases: {self.phases}")
"""
    )
    result = cli.main(
        "fuzz",
        schema_url,
        "--workers=1",
        "--include-method=DELETE",
        hooks=module,
    )
    assert result.exit_code == 0, result.output
    assert "Phases: examples,coverage,fuzzing,stateful" in result.output


@pytest.mark.openapi_version("3.0")
@pytest.mark.operations("success")
def test_fuzz_rejects_generation_database_in_deterministic_mode(cli, schema_url):
    result = cli.main(
        "fuzz",
        schema_url,
        "--generation-deterministic",
        "--generation-database=:memory:",
    )
    assert result.exit_code == 2
    assert (
        "`--generation-deterministic` implies no database, so passing `--generation-database` too is invalid."
        in result.output
    )


@pytest.mark.openapi_version("3.0")
@pytest.mark.operations("success")
def test_fuzz_writes_ndjson_report(cli, schema_url, tmp_path):
    report_path = tmp_path / "events.ndjson"

    result = cli.main("fuzz", schema_url, "--include-method=DELETE", f"--report-ndjson-path={report_path}")
    assert result.exit_code == 0, result.output
    assert report_path.exists()

    content = report_path.read_text(encoding="utf-8")
    assert '"Initialize"' in content
    assert '"LoadingStarted"' in content
    assert '"LoadingFinished"' in content
    assert '"EngineFinished"' in content


@pytest.mark.openapi_version("3.0")
@pytest.mark.operations("success")
def test_fuzz_writes_junit_report(cli, schema_url, tmp_path):
    report_path = tmp_path / "junit.xml"

    result = cli.main("fuzz", schema_url, "--include-method=DELETE", f"--report-junit-path={report_path}")
    assert result.exit_code == 0, result.output
    assert report_path.exists()
    assert "<testsuite" in report_path.read_text(encoding="utf-8")


@pytest.mark.openapi_version("3.0")
@pytest.mark.operations("success")
def test_fuzz_writes_vcr_report(cli, schema_url, tmp_path):
    report_path = tmp_path / "cassette.yaml"

    result = cli.main("fuzz", schema_url, "--include-method=DELETE", f"--report-vcr-path={report_path}")
    assert result.exit_code == 0, result.output
    assert report_path.exists()
    assert "http_interactions:" in report_path.read_text(encoding="utf-8")


@pytest.mark.openapi_version("3.0")
@pytest.mark.operations("success")
def test_fuzz_writes_har_report(cli, schema_url, tmp_path):
    report_path = tmp_path / "report.har"

    result = cli.main("fuzz", schema_url, "--include-method=DELETE", f"--report-har-path={report_path}")
    assert result.exit_code == 0, result.output
    assert report_path.exists()
    assert '"log"' in report_path.read_text(encoding="utf-8")


@pytest.mark.openapi_version("3.0")
@pytest.mark.operations("success")
@pytest.mark.parametrize(
    ("report_format", "extension"),
    [
        (ReportFormat.JUNIT, "xml"),
        (ReportFormat.VCR, "yaml"),
        (ReportFormat.HAR, "json"),
    ],
)
def test_fuzz_writes_report_to_report_dir(cli, schema_url, tmp_path, report_format, extension):
    report_dir = tmp_path / "reports"

    result = cli.main(
        "fuzz",
        schema_url,
        "--include-method=DELETE",
        f"--report={report_format.value}",
        f"--report-dir={report_dir}",
    )
    assert result.exit_code == 0, result.output
    assert report_dir.exists()
    assert list(report_dir.glob(f"{report_format.value}-*.{extension}"))


@pytest.mark.openapi_version("3.0")
@pytest.mark.operations("success")
def test_fuzz_writes_multiple_reports_to_report_dir(cli, schema_url, tmp_path):
    report_dir = tmp_path / "reports"

    result = cli.main(
        "fuzz",
        schema_url,
        "--include-method=DELETE",
        "--report=junit,vcr,har,ndjson",
        f"--report-dir={report_dir}",
    )
    assert result.exit_code == 0, result.output
    assert report_dir.exists()
    assert list(report_dir.glob("junit-*.xml"))
    assert list(report_dir.glob("vcr-*.yaml"))
    assert list(report_dir.glob("har-*.json"))
    assert list(report_dir.glob("ndjson-*.ndjson"))


def test_fuzz_invalid_operations_trigger_fail_fast_stop_by_default(cli, app_runner, ctx):
    schema = ctx.openapi.build_schema(
        {
            "/ok": {
                "get": {
                    "responses": {
                        "200": {
                            "description": "OK",
                            "content": {"application/json": {"schema": {"type": "object"}}},
                        }
                    }
                }
            },
            "/csv": {
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {"text/csv": {"schema": {"type": "string"}}},
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            },
        }
    )

    ok_calls = 0

    app = Flask(__name__)

    @app.route("/openapi.json")
    def get_schema():  # type: ignore[no-untyped-def]
        return jsonify(schema)

    @app.route("/ok")
    def ok():  # type: ignore[no-untyped-def]
        nonlocal ok_calls
        ok_calls += 1
        return jsonify({})

    @app.route("/csv", methods=["POST"])
    def csv():  # type: ignore[no-untyped-def]
        return jsonify({})

    port = app_runner.run_flask_app(app)

    result = cli.main("fuzz", f"http://127.0.0.1:{port}/openapi.json", "--workers=1")
    assert result.exit_code == 1, result.output
    assert ok_calls == 0
    assert "ERRORS" in result.output
    assert "POST /csv" in result.output
    assert "Serialization not possible" in result.output
    assert "  Stopped: failure detected in fail-fast mode" in result.output


def test_fuzz_invalid_operations_respect_continue_on_failure(cli, app_runner, ctx):
    schema = ctx.openapi.build_schema(
        {
            "/ok": {
                "get": {
                    "responses": {
                        "200": {
                            "description": "OK",
                            "content": {"application/json": {"schema": {"type": "object"}}},
                        }
                    }
                }
            },
            "/csv": {
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {"text/csv": {"schema": {"type": "string"}}},
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            },
        }
    )

    ok_calls = 0

    app = Flask(__name__)

    @app.route("/openapi.json")
    def get_schema():  # type: ignore[no-untyped-def]
        return jsonify(schema)

    @app.route("/ok")
    def ok():  # type: ignore[no-untyped-def]
        nonlocal ok_calls
        ok_calls += 1
        return jsonify({})

    @app.route("/csv", methods=["POST"])
    def csv():  # type: ignore[no-untyped-def]
        return jsonify({})

    port = app_runner.run_flask_app(app)

    result = cli.main(
        "fuzz",
        f"http://127.0.0.1:{port}/openapi.json",
        "--workers=1",
        "--continue-on-failure",
    )
    assert result.exit_code == 1, result.output
    # GET /ok has no parameters so exactly 1 case is generated;
    # --continue-on-failure lets it proceed past the CSV serialization error.
    assert ok_calls > 0
    assert "ERRORS" in result.output
    assert "POST /csv" in result.output
    assert "Serialization not possible" in result.output


def test_fuzz_help_options_are_documented_in_cli_reference():
    result = CliRunner().invoke(schemathesis_cli, ["fuzz", "--help"])
    assert result.exit_code == 0, result.output

    options = {option for option in re.findall(r"--[a-zA-Z0-9][a-zA-Z0-9-]*", result.output) if option != "--help"}
    reference = Path("docs/reference/cli.md").read_text(encoding="utf-8")
    missing = sorted(option for option in options if option not in reference)

    assert not missing, f"Missing fuzz options in docs/reference/cli.md: {', '.join(missing)}"
