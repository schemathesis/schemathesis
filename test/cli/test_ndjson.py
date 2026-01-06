import json
import platform

import pytest
from _pytest.main import ExitCode
from flask import Flask, jsonify, request


@pytest.fixture
def ndjson_path(tmp_path):
    return tmp_path / "output.ndjson"


def load_ndjson(path):
    events = []
    with path.open(encoding="utf-8") as fd:
        for line in fd:
            events.append(json.loads(line))
    return events


def get_event_type(event):
    """Get the event type from externally tagged format."""
    return next(iter(event.keys()))


def get_event_data(event):
    """Get the event data from externally tagged format."""
    return next(iter(event.values()))


@pytest.mark.operations("success")
def test_store_ndjson(cli, schema_url, ndjson_path, hypothesis_max_examples):
    hypothesis_max_examples = hypothesis_max_examples or 2
    cli.run_and_assert(
        schema_url,
        f"--report-ndjson-path={ndjson_path}",
        f"--max-examples={hypothesis_max_examples}",
        "--seed=1",
        "--checks=not_a_server_error",
        "--mode=positive",
    )
    events = load_ndjson(ndjson_path)

    # First event should be Initialize
    assert get_event_type(events[0]) == "Initialize"
    init_data = get_event_data(events[0])
    assert "schemathesis_version" in init_data
    assert init_data["seed"] == 1

    # Should have EngineStarted
    engine_started = [e for e in events if get_event_type(e) == "EngineStarted"]
    assert len(engine_started) == 1

    # Should have PhaseStarted events
    phase_started = [e for e in events if get_event_type(e) == "PhaseStarted"]
    assert len(phase_started) >= 1

    # Should have ScenarioFinished events with recorder data
    scenario_finished = [e for e in events if get_event_type(e) == "ScenarioFinished"]
    assert len(scenario_finished) >= 1
    # At least one scenario should have cases/checks/interactions (empty ones are omitted)
    has_data = False
    for event in scenario_finished:
        data = get_event_data(event)
        assert "recorder" in data
        recorder = data["recorder"]
        if recorder.get("cases") or recorder.get("checks") or recorder.get("interactions"):
            has_data = True
    assert has_data, "Expected at least one scenario with test data"

    # Last event should be EngineFinished
    assert get_event_type(events[-1]) == "EngineFinished"
    assert "running_time" in get_event_data(events[-1])


@pytest.mark.operations("slow")
@pytest.mark.openapi_version("3.0")
def test_store_timeout(cli, schema_url, ndjson_path):
    cli.run_and_assert(
        schema_url,
        f"--report-ndjson-path={ndjson_path}",
        "--max-examples=1",
        "--request-timeout=0.001",
        "--seed=1",
        "--mode=positive",
        exit_code=ExitCode.TESTS_FAILED,
    )
    events = load_ndjson(ndjson_path)
    assert get_event_type(events[0]) == "Initialize"
    assert get_event_data(events[0])["seed"] == 1


@pytest.mark.operations("flaky")
def test_interaction_with_failure(cli, openapi3_schema_url, hypothesis_max_examples, ndjson_path):
    cli.run_and_assert(
        openapi3_schema_url,
        f"--report-ndjson-path={ndjson_path}",
        f"--max-examples={hypothesis_max_examples or 5}",
        "--seed=1",
        exit_code=ExitCode.TESTS_FAILED,
    )
    events = load_ndjson(ndjson_path)
    scenario_finished = [e for e in events if get_event_type(e) == "ScenarioFinished"]
    assert len(scenario_finished) >= 1


@pytest.mark.skipif(platform.system() == "Windows", reason="Simpler to setup on Linux")
@pytest.mark.operations("success")
def test_run_subprocess(testdir, ndjson_path, hypothesis_max_examples, schema_url):
    testdir.run(
        "schemathesis",
        "run",
        f"--report-ndjson-path={ndjson_path}",
        f"--max-examples={hypothesis_max_examples or 2}",
        schema_url,
    )
    events = load_ndjson(ndjson_path)
    assert get_event_type(events[0]) == "Initialize"
    init_data = get_event_data(events[0])
    assert "st run" in init_data["command"]
    assert str(ndjson_path) in init_data["command"]


@pytest.mark.parametrize("in_config", [True, False])
@pytest.mark.openapi_version("3.0")
def test_report_dir(cli, schema_url, tmp_path, in_config):
    report_dir = tmp_path / "reports"
    args = ["--max-examples=1"]
    kwargs = {}
    if in_config:
        kwargs["config"] = {"reports": {"ndjson": {"enabled": True}, "directory": str(report_dir)}}
    else:
        args = ["--report=ndjson", f"--report-dir={report_dir}", *args]
    cli.run(schema_url, *args, **kwargs)
    assert report_dir.exists()
    assert list(report_dir.glob("*.ndjson"))


@pytest.mark.openapi_version("3.0")
def test_all_event_types(cli, schema_url, ndjson_path):
    cli.run(
        schema_url,
        f"--report-ndjson-path={ndjson_path}",
        "--max-examples=1",
        "--seed=1",
    )
    events = load_ndjson(ndjson_path)
    event_types = {get_event_type(e) for e in events}

    assert "Initialize" in event_types
    assert "EngineStarted" in event_types
    assert "PhaseStarted" in event_types
    assert "ScenarioFinished" in event_types
    assert "PhaseFinished" in event_types
    assert "EngineFinished" in event_types


@pytest.mark.openapi_version("3.0")
def test_phase_data_in_events(cli, schema_url, ndjson_path):
    cli.run(
        schema_url,
        f"--report-ndjson-path={ndjson_path}",
        "--max-examples=1",
        "--seed=1",
        "--phases=coverage",
    )
    events = load_ndjson(ndjson_path)
    phase_started = [e for e in events if get_event_type(e) == "PhaseStarted"]
    assert len(phase_started) >= 1
    for event in phase_started:
        assert "phase" in get_event_data(event)


@pytest.mark.operations("success")
def test_binary_body_base64(cli, schema_url, ndjson_path):
    cli.run_and_assert(
        schema_url,
        f"--report-ndjson-path={ndjson_path}",
        "--max-examples=1",
        "--seed=1",
        "--mode=positive",
    )
    events = load_ndjson(ndjson_path)
    scenario_finished = [e for e in events if get_event_type(e) == "ScenarioFinished"]
    # Binary data should be encoded as {"$base64": "..."} if present
    for event in scenario_finished:
        data = get_event_data(event)
        interactions = data["recorder"].get("interactions", {})
        for interaction in interactions.values():
            if interaction.get("response") and interaction["response"].get("body"):
                body = interaction["response"]["body"]
                # Body should be a string or a base64 encoded dict
                assert isinstance(body, (str, dict))
                if isinstance(body, dict):
                    assert "$base64" in body


def test_enum_serialization(cli, ctx, app_runner, ndjson_path):
    schema = ctx.openapi.build_schema({"/users": {"get": {"responses": {"200": {"description": "OK"}}}}})

    app = Flask(__name__)

    @app.route("/openapi.json")
    def openapi():
        return jsonify(schema)

    @app.route("/users")
    def users():
        return jsonify([])

    port = app_runner.run_flask_app(app)

    cli.run(
        f"http://127.0.0.1:{port}/openapi.json",
        f"--report-ndjson-path={ndjson_path}",
        "--max-examples=10",
        "--seed=1",
        "--phases=coverage",
    )
    events = load_ndjson(ndjson_path)

    # PhaseName enum should be serialized as string value
    phase_started = [e for e in events if get_event_type(e) == "PhaseStarted"]
    assert len(phase_started) >= 1
    for event in phase_started:
        data = get_event_data(event)
        phase = data["phase"]
        assert isinstance(phase["name"], str)
        assert phase["name"] in ("API probing", "Schema analysis", "Examples", "Coverage", "Fuzzing", "Stateful")

    # Status enum should be serialized as string value
    phase_finished = [e for e in events if get_event_type(e) == "PhaseFinished"]
    assert len(phase_finished) >= 1
    for event in phase_finished:
        data = get_event_data(event)
        assert isinstance(data["status"], str)
        assert data["status"] in ("success", "failure", "error", "interrupted", "skip")


def test_form_data_string_body(cli, ctx, app_runner, ndjson_path):
    schema = ctx.openapi.build_schema(
        {
            "/submit": {
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/x-www-form-urlencoded": {
                                "schema": {
                                    "type": "object",
                                    "properties": {"name": {"type": "string"}},
                                    "required": ["name"],
                                }
                            }
                        },
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            }
        }
    )

    app = Flask(__name__)

    @app.route("/openapi.json")
    def openapi():
        return jsonify(schema)

    @app.route("/submit", methods=["POST"])
    def submit():
        return jsonify({"received": request.form.get("name")})

    port = app_runner.run_flask_app(app)

    cli.run(
        f"http://127.0.0.1:{port}/openapi.json",
        f"--report-ndjson-path={ndjson_path}",
        "--max-examples=10",
        "--seed=1",
        "--mode=positive",
    )
    events = load_ndjson(ndjson_path)
    scenario_finished = [e for e in events if get_event_type(e) == "ScenarioFinished"]
    assert len(scenario_finished) >= 1

    # Form data body should be base64 encoded
    found_form_request = False
    for event in scenario_finished:
        data = get_event_data(event)
        interactions = data["recorder"].get("interactions", {})
        for interaction in interactions.values():
            req = interaction.get("request", {})
            if req.get("body"):
                body = req["body"]
                assert isinstance(body, dict) and "$base64" in body
                found_form_request = True
    assert found_form_request


def test_sanitization_disabled(cli, ctx, app_runner, ndjson_path):
    schema = ctx.openapi.build_schema({"/users": {"get": {"responses": {"200": {"description": "OK"}}}}})

    app = Flask(__name__)

    @app.route("/openapi.json")
    def openapi():
        return jsonify(schema)

    @app.route("/users")
    def users():
        return jsonify([])

    port = app_runner.run_flask_app(app)

    cli.run(
        f"http://127.0.0.1:{port}/openapi.json",
        f"--report-ndjson-path={ndjson_path}",
        "--max-examples=10",
        "--seed=1",
        "--output-sanitize=false",
    )
    events = load_ndjson(ndjson_path)

    assert get_event_type(events[0]) == "Initialize"
    assert get_event_type(events[-1]) == "EngineFinished"

    phase_started = [e for e in events if get_event_type(e) == "PhaseStarted"]
    assert len(phase_started) >= 1


def test_stateful_with_extraction_failure(cli, ctx, app_runner, ndjson_path):
    # Link expression references non-existent field to trigger Err serialization
    schema = ctx.openapi.build_schema(
        {
            "/users": {
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {"type": "object", "properties": {"name": {"type": "string"}}}
                            }
                        },
                    },
                    "responses": {
                        "201": {
                            "description": "Created",
                            "content": {
                                "application/json": {
                                    "schema": {"type": "object", "properties": {"id": {"type": "integer"}}}
                                }
                            },
                            "links": {
                                "GetUser": {
                                    "operationId": "getUser",
                                    "parameters": {"userId": "$response.body#/nonexistent"},
                                }
                            },
                        }
                    },
                }
            },
            "/users/{userId}": {
                "get": {
                    "operationId": "getUser",
                    "parameters": [{"name": "userId", "in": "path", "required": True, "schema": {"type": "integer"}}],
                    "responses": {"200": {"description": "OK"}},
                }
            },
        }
    )

    app = Flask(__name__)

    @app.route("/openapi.json")
    def openapi():
        return jsonify(schema)

    @app.route("/users", methods=["POST"])
    def create_user():
        return jsonify({"id": 1}), 201

    @app.route("/users/<int:user_id>")
    def get_user(user_id):
        return jsonify({"id": user_id, "name": "Test"})

    port = app_runner.run_flask_app(app)

    cli.run(
        f"http://127.0.0.1:{port}/openapi.json",
        f"--report-ndjson-path={ndjson_path}",
        "--max-examples=10",
        "--seed=1",
        "--phases=stateful",
    )
    events = load_ndjson(ndjson_path)

    assert get_event_type(events[0]) == "Initialize"
    assert get_event_type(events[-1]) == "EngineFinished"

    # Stateful phase should run
    phase_started = [e for e in events if get_event_type(e) == "PhaseStarted"]
    assert any(get_event_data(e)["phase"]["name"] == "Stateful" for e in phase_started)


def test_unresolvable_extraction_serialized(cli, ctx, app_runner, ndjson_path):
    # Link references an array index that will be empty, triggering $unresolvable
    schema = ctx.openapi.build_schema(
        {
            "/tags": {
                "get": {
                    "operationId": "listTags",
                    "responses": {
                        "200": {
                            "description": "OK",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {"data": {"type": "array", "items": {"type": "object"}}},
                                    }
                                }
                            },
                            "links": {
                                "GetTag": {
                                    "operationId": "getTag",
                                    "parameters": {"tagId": "$response.body#/data/0/id"},
                                }
                            },
                        }
                    },
                }
            },
            "/tags/{tagId}": {
                "get": {
                    "operationId": "getTag",
                    "parameters": [{"name": "tagId", "in": "path", "required": True, "schema": {"type": "integer"}}],
                    "responses": {"200": {"description": "OK"}},
                }
            },
        }
    )

    app = Flask(__name__)

    @app.route("/openapi.json")
    def openapi():
        return jsonify(schema)

    @app.route("/tags")
    def list_tags():
        # Return empty array - extraction of /data/0/id will be unresolvable
        return jsonify({"data": []})

    @app.route("/tags/<int:tag_id>")
    def get_tag(tag_id):
        return jsonify({"id": tag_id, "name": "Test"})

    port = app_runner.run_flask_app(app)

    cli.run(
        f"http://127.0.0.1:{port}/openapi.json",
        f"--report-ndjson-path={ndjson_path}",
        "--max-examples=10",
        "--seed=1",
        "--phases=stateful",
    )
    events = load_ndjson(ndjson_path)

    # Find ScenarioFinished events with transitions
    scenario_finished = [e for e in events if get_event_type(e) == "ScenarioFinished"]
    found_unresolvable = False

    for event in scenario_finished:
        data = get_event_data(event)
        cases = data["recorder"].get("cases", {})
        for case_node in cases.values():
            transition = case_node.get("transition")
            if transition and transition.get("parameters"):
                for location_params in transition["parameters"].values():
                    for param in location_params.values():
                        if param.get("value") == {"$unresolvable": True}:
                            found_unresolvable = True
                            # Verify the structure
                            assert "definition" in param
                            assert "is_required" in param

    assert found_unresolvable, "Expected to find $unresolvable marker in extraction results"
