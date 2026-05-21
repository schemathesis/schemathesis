from __future__ import annotations

import json
from pathlib import Path
from queue import Queue

import pytest
import requests
from flask import jsonify, request

from schemathesis.cli.commands.run.handlers.crashes import (
    _find_failing_case_ids,
    _Process,
    _project_title,
    _run,
)
from schemathesis.config import SanitizationConfig, SchemathesisConfig
from schemathesis.core.failures import Failure
from schemathesis.core.transport import Response
from schemathesis.engine.recorder import ScenarioRecorder
from schemathesis.reporting.crashes import MANIFEST_FILENAME


def _failure() -> Failure:
    return Failure(operation="GET /x", title="Server error", message="boom")


def test_project_title_tolerates_non_dict_info():
    # A malformed `info` (null or a non-object) must not abort the run.
    config = SchemathesisConfig().projects.get_default()
    assert _project_title({"info": None}, config) is None
    assert _project_title({"info": "oops"}, config) is None


def _failing_recorder(case_factory, *, label: str = "GET /users") -> ScenarioRecorder:
    recorder = ScenarioRecorder(label=label)
    case = case_factory()
    recorder.record_case(parent_id=None, case=case, transition=None, is_transition_applied=False)
    prepared = requests.Request(method="GET", url="http://127.0.0.1/users").prepare()
    response = Response(
        status_code=500,
        headers={"content-type": ["application/json"]},
        content=b'{"error": "boom"}',
        request=prepared,
        elapsed=0.1,
        verify=False,
    )
    recorder.record_response(case_id=case.id, response=response)
    recorder.record_check_failure(name="not_a_server_error", case_id=case.id, code_sample="curl x", failure=_failure())
    return recorder


def _passing_recorder(case_factory, *, label: str = "GET /users") -> ScenarioRecorder:
    recorder = ScenarioRecorder(label=label)
    case = case_factory()
    recorder.record_case(parent_id=None, case=case, transition=None, is_transition_applied=False)
    recorder.record_check_success(name="not_a_server_error", case_id=case.id)
    return recorder


def _drive(queue: Queue, directory: Path) -> None:
    _run(
        directory=directory,
        schema_location="http://x/schema",
        base_url="http://x",
        sanitization=SanitizationConfig(),
        queue=queue,
    )


def test_find_failing_case_ids_returns_every_failing_case():
    recorder = ScenarioRecorder(label="GET /users")
    recorder.record_check_success(name="not_a_server_error", case_id="ok")
    recorder.record_check_failure(name="not_a_server_error", case_id="bad-1", code_sample="", failure=_failure())
    recorder.record_check_failure(name="not_a_server_error", case_id="bad-2", code_sample="", failure=_failure())

    assert sorted(_find_failing_case_ids(recorder)) == ["bad-1", "bad-2"]


def test_passing_scenario_does_not_delete_failing_crash(case_factory, tmp_path):
    # A passing example of an operation must not wipe the crash recorded by a failing example of the same operation.
    crashes_dir = tmp_path / "crashes"
    queue: Queue = Queue()
    queue.put(_Process(recorder=_failing_recorder(case_factory, label="GET /users"), success=False))
    queue.put(_Process(recorder=_passing_recorder(case_factory, label="GET /users"), success=True))
    queue.put(None)

    _drive(queue, crashes_dir)

    files = [f for f in crashes_dir.iterdir() if f.name != MANIFEST_FILENAME]
    assert len(files) == 1


def test_fuzz_crash_records_real_operation_not_phase_label(case_factory, tmp_path):
    crashes_dir = tmp_path / "crashes"
    queue: Queue = Queue()
    queue.put(_Process(recorder=_failing_recorder(case_factory, label="Fuzz tests"), success=False))
    queue.put(None)

    _drive(queue, crashes_dir)

    crash = json.loads(next(f for f in crashes_dir.iterdir() if f.name != MANIFEST_FILENAME).read_text())
    assert crash["operation"] == "GET /users"


def test_passing_operation_removes_only_stale_crash(case_factory, tmp_path):
    # Cross-run healing still works: an operation that passed and never failed this run has its stale crash dropped.
    crashes_dir = tmp_path / "crashes"
    writer_queue: Queue = Queue()
    writer_queue.put(_Process(recorder=_failing_recorder(case_factory, label="GET /users"), success=False))
    writer_queue.put(None)
    _drive(writer_queue, crashes_dir)
    assert [f for f in crashes_dir.iterdir() if f.name != MANIFEST_FILENAME]

    heal_queue: Queue = Queue()
    heal_queue.put(_Process(recorder=_passing_recorder(case_factory, label="GET /users"), success=True))
    heal_queue.put(None)
    _drive(heal_queue, crashes_dir)

    assert not [f for f in crashes_dir.iterdir() if f.name != MANIFEST_FILENAME]


def _crash_files(directory: Path) -> list[Path]:
    if not directory.exists():
        return []
    return [path for path in directory.iterdir() if path.suffix == ".json" and path.name != MANIFEST_FILENAME]


def _crashes_dir(tmp_path: Path) -> Path:
    return tmp_path / ".schemathesis" / "default" / "cache" / "crashes"


def test_crash_file_written_on_failure(cli, ctx, tmp_path):
    app, _ = ctx.openapi.make_flask_app(
        {
            "/boom": {
                "get": {
                    "responses": {
                        "200": {"description": "OK"},
                        "500": {"description": "Error"},
                    }
                }
            }
        }
    )

    @app.route("/boom")
    def boom():
        return jsonify({"error": "crash"}), 500

    cli.run_openapi_app(app, "--max-examples=1")

    crash_files = _crash_files(_crashes_dir(tmp_path))
    assert len(crash_files) == 1, crash_files

    crash = json.loads(crash_files[0].read_text())
    step = crash["sequence"][0]
    assert (
        crash["operation"],
        step["method"],
        step["response"]["status_code"],
        [c["name"] for c in step["checks"]],
    ) == ("GET /boom", "GET", 500, ["not_a_server_error"])


def test_crash_file_written_for_failures_in_errored_scenario(cli, ctx, tmp_path):
    # Checks that failed before a scenario errored still get crash files, so the replay hints resolve.
    app, _ = ctx.openapi.make_flask_app(
        {"/api/missing_path_parameter/{id}": {"get": {"responses": {"200": {"description": "OK"}}}}}
    )

    cli.run_openapi_app(app, "--max-examples=5")

    crash_files = _crash_files(_crashes_dir(tmp_path))
    checks = {
        check["name"] for f in crash_files for step in json.loads(f.read_text())["sequence"] for check in step["checks"]
    }
    assert checks == {"status_code_conformance", "unsupported_method"}


def test_crash_file_uses_named_project_directory(cli, ctx, tmp_path):
    # Crashes co-locate with the project's cache: a named project writes under <title>/cache/crashes, not default/.
    app, _ = ctx.openapi.make_flask_app({"/boom": {"get": {"responses": {"500": {"description": "Error"}}}}})

    @app.route("/boom")
    def boom():
        return jsonify({"error": "crash"}), 500

    cli.run_openapi_app(app, "--max-examples=1", config={"project": [{"title": "Test"}]})

    titled = tmp_path / ".schemathesis" / "test" / "cache" / "crashes"
    default = tmp_path / ".schemathesis" / "default" / "cache" / "crashes"
    assert _crash_files(titled), list(titled.parent.glob("*")) if titled.parent.exists() else "no titled dir"
    assert not _crash_files(default)


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_no_crash_file_when_cache_disabled(cli, ctx, tmp_path, snapshot_cli):
    # With recording off, no crash file is written and the failure must not hint `st replay`.
    app, _ = ctx.openapi.make_flask_app({"/boom": {"get": {"responses": {"500": {"description": "Error"}}}}})

    @app.route("/boom")
    def boom():
        return jsonify({"error": "crash"}), 500

    assert cli.run_openapi_app(app, "--max-examples=1", config={"cache": {"enabled": False}}) == snapshot_cli
    assert not _crash_files(_crashes_dir(tmp_path))


def test_no_crash_file_on_success(cli, ctx, tmp_path):
    app, _ = ctx.openapi.make_flask_app({"/ok": {"get": {"responses": {"200": {"description": "OK"}}}}})

    @app.route("/ok")
    def ok():
        return jsonify({})

    cli.run_openapi_app(app, "--max-examples=1")

    assert not _crash_files(_crashes_dir(tmp_path))


def test_crash_file_sanitizes_url_and_response_headers(cli, ctx, tmp_path):
    app, _ = ctx.openapi.make_flask_app(
        {
            "/leak": {
                "get": {
                    "parameters": [{"name": "api_key", "in": "query", "required": True, "schema": {"type": "string"}}],
                    "responses": {"500": {"description": "Error"}},
                }
            }
        }
    )

    @app.route("/leak")
    def leak():
        return jsonify({"error": "boom"}), 500, {"Authorization": "super-secret-token"}

    cli.run_openapi_app(app, "--max-examples=1", config={"parameters": {"api_key": "SECRETVALUE"}})

    crash = json.loads(_crash_files(_crashes_dir(tmp_path))[0].read_text())
    step = crash["sequence"][0]
    assert "super-secret-token" not in json.dumps(crash)
    assert "SECRETVALUE" not in step["url"], step["url"]
    assert "%5BFiltered%5D" in step["url"] or "[Filtered]" in step["url"], step["url"]
    assert step["response"]["headers"].get("authorization") == "[Filtered]"


def test_crash_file_does_not_persist_raw_request_body(cli, ctx, tmp_path):
    # Cache files strip body fields before persistence; the raw wire body (which can carry secrets) is not stored.
    app, _ = ctx.openapi.make_flask_app(
        {
            "/items": {
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {"name": {"type": "string"}},
                                    "required": ["name"],
                                }
                            }
                        },
                    },
                    "responses": {"500": {"description": "Error"}},
                }
            }
        }
    )

    @app.route("/items", methods=["POST"])
    def items():
        if request.is_json and isinstance(request.get_json(silent=True), dict):
            return jsonify({"error": "boom"}), 500
        return jsonify({}), 200

    cli.run_openapi_app(app, "--max-examples=5", "--phases=fuzzing")

    crash = json.loads(_crash_files(_crashes_dir(tmp_path))[0].read_text())
    assert "body" not in crash["sequence"][0]


def test_crash_file_respects_disabled_sanitization(cli, ctx, tmp_path):
    # Crash files follow the output.sanitization rules; disabling sanitization persists raw values.
    app, _ = ctx.openapi.make_flask_app(
        {
            "/leak": {
                "get": {
                    "parameters": [{"name": "api_key", "in": "query", "required": True, "schema": {"type": "string"}}],
                    "responses": {"500": {"description": "Error"}},
                }
            }
        }
    )

    @app.route("/leak")
    def leak():
        return jsonify({"error": "boom"}), 500

    cli.run_openapi_app(
        app,
        "--max-examples=1",
        config={"parameters": {"api_key": "SECRETVALUE"}, "output": {"sanitization": {"enabled": False}}},
    )

    crash = json.loads(_crash_files(_crashes_dir(tmp_path))[0].read_text())
    assert "SECRETVALUE" in crash["sequence"][0]["url"]


def test_crash_file_stores_structured_case(cli, ctx, tmp_path):
    app, _ = ctx.openapi.make_flask_app(
        {
            "/items": {
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {"name": {"type": "string"}},
                                    "required": ["name"],
                                }
                            }
                        },
                    },
                    "responses": {"500": {"description": "Error"}},
                }
            }
        }
    )

    @app.route("/items", methods=["POST"])
    def items():
        if request.is_json and isinstance(request.get_json(silent=True), dict):
            return jsonify({"error": "boom"}), 500
        return jsonify({}), 200

    cli.run_openapi_app(app, "--max-examples=5", "--phases=fuzzing")

    crash = json.loads(_crash_files(_crashes_dir(tmp_path))[0].read_text())
    step = crash["sequence"][0]
    # The generated body value varies, so assert its shape; the rest of the structured case is fixed.
    assert step["case_body"]["encoding"] == "json"
    assert isinstance(step["case_body"]["value"], dict)
    structured = {
        key: step[key]
        for key in ("method", "path", "path_parameters", "query", "case_headers", "cookies", "media_type")
    }
    assert structured == {
        "method": "POST",
        "path": "/items",
        "path_parameters": {},
        "query": {},
        "case_headers": {},
        "cookies": {},
        "media_type": "application/json",
    }
