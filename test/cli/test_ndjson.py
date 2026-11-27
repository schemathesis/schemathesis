import base64
import builtins
import functools
import itertools
import json
import platform
import sys
import threading
from pathlib import Path

import pytest
import yaml
from _pytest.main import ExitCode
from flask import Response, jsonify, request

from schemathesis.cli.commands.run.handlers.base import WriterWorker
from test.utils import load_ndjson


@pytest.fixture
def ndjson_path(tmp_path):
    return tmp_path / "output.ndjson"


def get_event_type(event):
    return next(iter(event.keys()))


def get_event_data(event):
    return next(iter(event.values()))


LARGE_RESPONSE_BODY = json.dumps({"padding": "x" * 2000}).encode()
SECRET = "secret-token"


def test_ndjson_records_truncated_response_body(cli, ctx, ndjson_path):
    app, _ = ctx.openapi.make_flask_app({"/api/large": {"get": {"responses": {"200": {"description": "OK"}}}}})

    @app.route("/api/large")
    def large():
        return Response(LARGE_RESPONSE_BODY, content_type="application/json")

    cli.run_openapi_app(
        app,
        f"--report-ndjson-path={ndjson_path}",
        "--max-examples=1",
        "--phases=fuzzing",
        "--mode=positive",
        config={"output": {"truncation": {"max-recorded-payload-size": 64}}},
    )
    assert [
        {"content": interaction["response"]["content"], "content_size": interaction["response"].get("content_size")}
        for event in load_ndjson(ndjson_path)
        if get_event_type(event) == "ScenarioFinished"
        for interaction in get_event_data(event)["recorder"].get("interactions", {}).values()
    ] == [
        {
            "content": {"$base64": base64.b64encode(LARGE_RESPONSE_BODY[:64]).decode()},
            "content_size": len(LARGE_RESPONSE_BODY),
        }
    ]


def test_ndjson_includes_case_meta(cli, ctx, ndjson_path):
    app, _ = ctx.openapi.make_flask_app(
        {
            "/items/{itemId}": {
                "get": {
                    "parameters": [
                        {"name": "itemId", "in": "path", "required": True, "schema": {"type": "integer"}},
                    ],
                    "responses": {"200": {"description": "OK"}},
                }
            }
        }
    )

    @app.route("/items/<int:item_id>")
    def get_item(item_id):
        return jsonify({"id": item_id})

    cli.run_openapi_app(
        app,
        f"--report-ndjson-path={ndjson_path}",
        "--max-examples=2",
        "--seed=1",
        "--mode=positive",
    )
    events = load_ndjson(ndjson_path)
    scenario_finished = [e for e in events if get_event_type(e) == "ScenarioFinished"]

    meta_dicts = [
        case_node["value"]["meta"]
        for event in scenario_finished
        for case_node in get_event_data(event)["recorder"].get("cases", {}).values()
        if "meta" in case_node.get("value", {})
    ]
    assert meta_dicts, "Expected at least one case to expose `meta`"

    for meta in meta_dicts:
        assert meta["generation"]["mode"]
        assert meta["phase"]["name"]
        assert "_dirty" not in meta
        assert "_last_validated_hashes" not in meta

    with_components = [m for m in meta_dicts if "components" in m]
    assert with_components, "Expected at least one case meta to include `components`"
    for component in with_components[0]["components"].values():
        assert component["mode"]


def test_store_ndjson(ctx, cli, ndjson_path, hypothesis_max_examples):
    api = ctx.openapi.apps.success()
    hypothesis_max_examples = hypothesis_max_examples or 2
    cli.run_and_assert(
        api.schema_url,
        f"--report-ndjson-path={ndjson_path}",
        f"--max-examples={hypothesis_max_examples}",
        "--seed=1",
        "--checks=not_a_server_error",
        "--mode=positive",
    )
    events = load_ndjson(ndjson_path)

    assert get_event_type(events[0]) == "Initialize"
    init_data = get_event_data(events[0])
    assert "schemathesis_version" in init_data
    assert init_data["seed"] == 1

    engine_started = [e for e in events if get_event_type(e) == "EngineStarted"]
    assert len(engine_started) == 1

    phase_started = [e for e in events if get_event_type(e) == "PhaseStarted"]
    assert len(phase_started) >= 1
    assert any(get_event_type(e) == "PhaseFinished" for e in events)

    scenario_finished = [e for e in events if get_event_type(e) == "ScenarioFinished"]
    assert len(scenario_finished) >= 1
    # The recorder omits empty `cases`, `checks` and `interactions`.
    has_data = False
    for event in scenario_finished:
        data = get_event_data(event)
        assert "recorder" in data
        recorder = data["recorder"]
        if recorder.get("cases") or recorder.get("checks") or recorder.get("interactions"):
            has_data = True
    assert has_data, "Expected at least one scenario with test data"

    assert get_event_type(events[-1]) == "EngineFinished"
    assert "running_time" in get_event_data(events[-1])


def test_store_timeout(ctx, cli, ndjson_path):
    api = ctx.openapi.apps.slow()
    cli.run_and_assert(
        api.schema_url,
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


def test_interaction_with_failure(ctx, cli, hypothesis_max_examples, ndjson_path):
    api = ctx.openapi.apps.flaky()
    cli.run_and_assert(
        api.schema_url,
        f"--report-ndjson-path={ndjson_path}",
        f"--max-examples={hypothesis_max_examples or 5}",
        "--seed=1",
        exit_code=ExitCode.TESTS_FAILED,
    )
    events = load_ndjson(ndjson_path)
    scenario_finished = [e for e in events if get_event_type(e) == "ScenarioFinished"]
    assert len(scenario_finished) >= 1


def collect_failures(events):
    return [
        entry["failure_info"]["failure"]
        for event in events
        if get_event_type(event) == "ScenarioFinished"
        for entries in get_event_data(event)["recorder"].get("checks", {}).values()
        for entry in entries
        if entry.get("failure_info")
    ]


def test_failure_details(ctx, cli, ndjson_path):
    api = ctx.openapi.apps.failure()
    cli.run_and_assert(
        api.schema_url,
        f"--report-ndjson-path={ndjson_path}",
        "--max-examples=1",
        "--seed=1",
        exit_code=ExitCode.TESTS_FAILED,
    )
    failures = collect_failures(load_ndjson(ndjson_path))
    assert failures
    assert [{key: value for key, value in failure.items() if key != "case_id"} for failure in failures] == [
        {
            "type": "ServerError",
            "operation": "GET /api/failure",
            "title": "Server error",
            "message": "",
            "severity": "critical",
            "code": "ST101",
        }
    ] * len(failures)


@pytest.mark.skipif(platform.system() == "Windows", reason="Simpler to setup on Linux")
def test_run_subprocess(ctx, testdir, ndjson_path, hypothesis_max_examples):
    api = ctx.openapi.apps.success()
    testdir.run(
        str(Path(sys.executable).with_name("schemathesis")),
        "run",
        f"--report-ndjson-path={ndjson_path}",
        f"--max-examples={hypothesis_max_examples or 2}",
        api.schema_url,
    )
    events = load_ndjson(ndjson_path)
    assert get_event_type(events[0]) == "Initialize"
    init_data = get_event_data(events[0])
    assert "st run" in init_data["command"]
    assert str(ndjson_path) in init_data["command"]
    event_types = {get_event_type(e) for e in events}
    assert "EngineFinished" in event_types, f"truncated stream: got only {sorted(event_types)}"


def test_binary_body_base64(ctx, cli, ndjson_path):
    api = ctx.openapi.apps.upload_file()
    cli.run_and_assert(
        api.schema_url,
        f"--report-ndjson-path={ndjson_path}",
        "--max-examples=1",
        "--seed=1",
        "--mode=positive",
        "--phases=fuzzing",
    )
    events = load_ndjson(ndjson_path)
    bodies = [
        interaction["request"]["body"]
        for event in events
        if get_event_type(event) == "ScenarioFinished"
        for interaction in get_event_data(event)["recorder"].get("interactions", {}).values()
        if "body" in interaction["request"]
    ]
    assert [list(body) for body in bodies] == [["$base64"]] * len(bodies) != []
    assert all(b"\x89PNG" in base64.b64decode(body["$base64"]) for body in bodies)


def test_serialization_without_sanitization(cli, ctx, ndjson_path):
    app, _ = ctx.openapi.make_flask_app(
        {
            "/users": {
                "get": {
                    "parameters": [
                        {
                            "name": "api_key",
                            "in": "query",
                            "required": True,
                            "schema": {"type": "string", "example": SECRET},
                        }
                    ],
                    "responses": {"200": {"description": "OK"}},
                }
            }
        }
    )

    @app.route("/users")
    def users():
        return jsonify([])

    cli.run_openapi_app(
        app,
        f"--report-ndjson-path={ndjson_path}",
        "--max-examples=10",
        "--seed=1",
        "--phases=coverage",
        "--mode=positive",
        "--output-sanitize=false",
    )
    events = load_ndjson(ndjson_path)
    assert [
        (node["value"]["query"], node["value"]["meta"]["raw_containers"])
        for event in events
        if get_event_type(event) == "ScenarioFinished"
        for node in get_event_data(event)["recorder"]["cases"].values()
    ] == [({"api_key": SECRET}, {"query": {"api_key": SECRET}})]
    assert get_event_type(events[0]) == "Initialize"
    assert get_event_type(events[-1]) == "EngineFinished"

    phase_started = [e for e in events if get_event_type(e) == "PhaseStarted"]
    assert len(phase_started) >= 1
    for event in phase_started:
        data = get_event_data(event)
        phase = data["phase"]
        assert isinstance(phase["name"], str)
        assert phase["name"] in (
            "setup",
            "examples",
            "coverage",
            "fuzzing",
            "stateful",
        )

    phase_finished = [e for e in events if get_event_type(e) == "PhaseFinished"]
    assert len(phase_finished) >= 1
    for event in phase_finished:
        data = get_event_data(event)
        assert isinstance(data["status"], str)
        assert data["status"] in ("success", "failure", "error", "interrupted", "skip")


def test_form_data_string_body(cli, ctx, ndjson_path):
    app, _ = ctx.openapi.make_flask_app(
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

    @app.route("/submit", methods=["POST"])
    def submit():
        return jsonify({"received": request.form.get("name")})

    cli.run_openapi_app(
        app,
        f"--report-ndjson-path={ndjson_path}",
        "--max-examples=10",
        "--seed=1",
        "--mode=positive",
    )
    events = load_ndjson(ndjson_path)
    scenario_finished = [e for e in events if get_event_type(e) == "ScenarioFinished"]
    assert len(scenario_finished) >= 1

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


@pytest.mark.parametrize(
    ("parameters", "args", "config", "expected", "raw_containers"),
    [
        (
            [{"name": "X-API-Key", "in": "header", "required": True, "schema": {"type": "string", "example": SECRET}}],
            ["-H", f"Authorization: Bearer {SECRET}"],
            {"parameters": {"X-API-Key": SECRET}},
            {"headers": {"Authorization": "[Filtered]", "X-API-Key": "[Filtered]"}},
            {"header": {"X-API-Key": "[Filtered]"}},
        ),
        (
            [{"name": "api_key", "in": "query", "required": True, "schema": {"type": "string", "example": SECRET}}],
            [],
            {"parameters": {"api_key": SECRET}},
            {"query": {"api_key": "[Filtered]"}},
            {"query": {"api_key": "[Filtered]"}},
        ),
        (
            [{"name": "session", "in": "cookie", "required": True, "schema": {"type": "string", "example": SECRET}}],
            [],
            {"parameters": {"session": SECRET}},
            {"cookies": {"session": "[Filtered]"}},
            {"cookie": {"session": "[Filtered]"}},
        ),
    ],
    ids=["header", "query", "cookie"],
)
def test_recorded_cases_sanitized(cli, ctx, ndjson_path, parameters, args, config, expected, raw_containers):
    app, _ = ctx.openapi.make_flask_app(
        {"/foo": {"get": {"parameters": parameters, "responses": {"200": {"description": "OK"}}}}}
    )

    @app.route("/foo")
    def foo():
        return jsonify({})

    cli.run_openapi_app(
        app,
        f"--report-ndjson-path={ndjson_path}",
        "--max-examples=1",
        "--phases=coverage,fuzzing",
        "--mode=positive",
        *args,
        config=config,
    )
    assert SECRET not in ndjson_path.read_text()
    cases = [
        node["value"]
        for event in load_ndjson(ndjson_path)
        if get_event_type(event) == "ScenarioFinished"
        for node in get_event_data(event)["recorder"]["cases"].values()
    ]
    assert {case["meta"]["phase"]["name"] for case in cases} == {"coverage", "fuzzing"}
    for case in cases:
        assert {key: case[key] for key in expected} == expected
    assert [case["meta"].get("raw_containers") for case in cases if case["meta"]["phase"]["name"] == "coverage"] == [
        raw_containers
    ]


@pytest.mark.skipif(platform.system() == "Windows", reason="Simpler to setup on Linux")
def test_credentials_sanitized_in_command(ctx, testdir, ndjson_path):
    api = ctx.openapi.apps.success()
    testdir.run(
        str(Path(sys.executable).with_name("schemathesis")),
        "run",
        f"--report-ndjson-path={ndjson_path}",
        "--max-examples=1",
        "-H",
        "X-API-Key: s3cret",
        "-H",
        "X-Trace-Id: keep-me",
        "--auth",
        "admin:hunter2",
        api.schema_url,
    )
    assert get_event_data(load_ndjson(ndjson_path)[0])["command"] == (
        f"st run --report-ndjson-path={ndjson_path} --max-examples=1 "
        "-H X-API-Key: [Filtered] -H X-Trace-Id: keep-me "
        f"--auth [Filtered] {api.schema_url}"
    )


def test_stateful_with_extraction_failure(cli, ctx, ndjson_path):
    # The link references a missing field to produce an extraction error.
    app, _ = ctx.openapi.make_flask_app(
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

    @app.route("/users", methods=["POST"])
    def create_user():
        return jsonify({"id": 1}), 201

    @app.route("/users/<int:user_id>")
    def get_user(user_id):
        return jsonify({"id": user_id, "name": "Test"})

    cli.run_openapi_app(
        app,
        f"--report-ndjson-path={ndjson_path}",
        "--max-examples=10",
        "--seed=1",
        "--phases=stateful",
    )
    events = load_ndjson(ndjson_path)

    assert get_event_type(events[0]) == "Initialize"
    assert get_event_type(events[-1]) == "EngineFinished"

    phase_started = [e for e in events if get_event_type(e) == "PhaseStarted"]
    assert any(get_event_data(e)["phase"]["name"] == "stateful" for e in phase_started)


def test_unresolvable_extraction_serialized(cli, ctx, ndjson_path):
    # The link points at an index of an empty array, so extraction yields `$unresolvable`.
    app, _ = ctx.openapi.make_flask_app(
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

    @app.route("/tags")
    def list_tags():
        return jsonify({"data": []})

    @app.route("/tags/<int:tag_id>")
    def get_tag(tag_id):
        return jsonify({"id": tag_id, "name": "Test"})

    cli.run_openapi_app(
        app,
        f"--report-ndjson-path={ndjson_path}",
        "--max-examples=10",
        "--seed=1",
        "--phases=stateful",
    )
    events = load_ndjson(ndjson_path)

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
                            assert "definition" in param
                            assert "is_required" in param

    assert found_unresolvable, "Expected to find $unresolvable marker in extraction results"


def imported_by_coverage():
    # Branch coverage parses each source file lazily, importing on whichever thread first runs it
    frame = sys._getframe()
    while frame is not None:
        if frame.f_globals.get("__name__", "").startswith("coverage."):
            return True
        frame = frame.f_back
    return False


def test_report_writers_do_not_import(cli, ctx, ndjson_path, tmp_path, monkeypatch):
    # An import on a writer thread races the main thread's own and dies, losing every later event
    api = ctx.openapi.apps.success()
    cassette_path = tmp_path / "output.yaml"
    har_path = tmp_path / "output.har"
    real_import = builtins.__import__

    def guarded_import(name, *args, **kwargs):
        on_writer = threading.current_thread().name in ("SchemathesisNdjsonWriter", "SchemathesisHarWriter")
        if on_writer and not imported_by_coverage():
            raise RuntimeError(f"deadlock detected by _ModuleLock({name!r})")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    cli.run(
        api.schema_url,
        f"--report-ndjson-path={ndjson_path}",
        f"--report-vcr-path={cassette_path}",
        f"--report-har-path={har_path}",
        "--max-examples=1",
        "--phases=fuzzing",
    )
    event_types = [get_event_type(event) for event in load_ndjson(ndjson_path)]
    assert event_types[0] == "Initialize"
    assert event_types[-1] == "EngineFinished"
    assert "ScenarioFinished" in event_types
    assert yaml.safe_load(cassette_path.read_text())["http_interactions"]
    assert json.loads(har_path.read_text())["log"]["entries"]


def keyed_operation(schema):
    return {
        "/api/items": {
            "get": {
                "parameters": [{"name": "key", "in": "query", "required": True, "schema": schema}],
                "responses": {"200": {"description": "OK"}},
            }
        }
    }


def serve_numbered_responses(ctx, schema, make_response):
    app, _ = ctx.openapi.make_flask_app(keyed_operation(schema))
    counter = itertools.count()

    @app.route("/api/items")
    def items():
        return make_response(next(counter))

    return app


MALFORMED_REQUESTS = {"unspecified_http_method", "malformed_content_type"}
SEEN_EVERY_TIME = {"distinct": 1, "singletons": 0, "doubletons": 0, "discovering": 0, "discoveries": [1]}


def load_behaviors(ndjson_path):
    events = load_ndjson(ndjson_path)
    scenarios = [
        recorder["cases"][case_id]["value"]["meta"]["phase"]["data"].get("scenario")
        for recorder in (get_event_data(event)["recorder"] for event in events if "ScenarioFinished" in event)
        for case_id in recorder.get("interactions", {})
    ]
    return get_event_data(events[-1])["payload"]["behaviors"], scenarios


def test_behavior_counts_follow_what_responses_say(cli, ctx, ndjson_path):
    app = serve_numbered_responses(
        ctx, {"type": "integer"}, lambda index: jsonify({"state": "ready", "next": None} if index else {"state": "new"})
    )
    cli.run_openapi_app(
        app,
        f"--report-ndjson-path={ndjson_path}",
        "--max-examples=10",
        "--phases=fuzzing",
        "--mode=positive",
        "--seed=1",
    )
    behaviors, scenarios = load_behaviors(ndjson_path)
    assert behaviors == {
        "GET /api/items": {
            "responses": len(scenarios),
            "sources": {
                "status": SEEN_EVERY_TIME,
                # `state` arrives with the first response, `next` with the second.
                "structure": {"distinct": 2, "singletons": 0, "doubletons": 0, "discovering": 0, "discoveries": [1, 2]},
                "parameters": SEEN_EVERY_TIME,
            },
        }
    }


@pytest.mark.parametrize(
    ("make_response", "distinct"),
    [
        (lambda index: Response(b"", content_type="application/json"), 0),
        (lambda index: jsonify({}), 0),
        (lambda index: jsonify(index), 1),
        (lambda index: Response(f"Record {index}", content_type="text/plain"), 1),
        (lambda index: Response(b'{"id": 1}', content_type="application/json; charset=nonexistent"), 1),
        (lambda index: jsonify({"id": f"id-{index}", "created": f"2020-01-{index % 28 + 1:02}"}), 2),
        (lambda index: jsonify({"items": [{"name": f"item-{number}"} for number in range(index % 3 + 1)]}), 3),
        (lambda index: jsonify(functools.reduce(lambda value, _: {"next": value}, range(10), index)), 8),
    ],
    ids=[
        "empty-body",
        "empty-object",
        "number",
        "text",
        "unknown-charset",
        "identifiers",
        "list-of-records",
        "deep-value",
    ],
)
def test_behavior_structure_ignores_values(cli, ctx, ndjson_path, make_response, distinct):
    app = serve_numbered_responses(ctx, {"type": "integer"}, make_response)
    cli.run_openapi_app(app, f"--report-ndjson-path={ndjson_path}", "--max-examples=10", "--phases=fuzzing", "--seed=1")
    behaviors, _ = load_behaviors(ndjson_path)
    expected = {"distinct": distinct, "singletons": 0, "doubletons": 0, "discovering": 0, "discoveries": [1] * distinct}
    assert behaviors["GET /api/items"]["sources"].get("structure") == (expected if distinct else None)


@pytest.mark.parametrize(
    ("make_message", "distinct"),
    [
        (lambda index: f"Lock 0x{index:x}f on 'table-{index}' timed out after {index} attempts", 1),
        (lambda index: f"Unknown column {'abcdefghij'[index % 10]}x", 8),
    ],
    ids=["same-wording", "more-wordings-than-kept"],
)
def test_behavior_failure_messages(cli, ctx, ndjson_path, make_message, distinct):
    # The coverage phase sends one request per enum value, enough to repeat every message.
    app = serve_numbered_responses(
        ctx, {"type": "integer", "enum": list(range(40))}, lambda index: Response(make_message(index), status=500)
    )
    cli.run_openapi_app(
        app, f"--report-ndjson-path={ndjson_path}", "--phases=coverage", "--continue-on-failure", "--seed=1"
    )
    behaviors, scenarios = load_behaviors(ndjson_path)
    assert set(scenarios) > MALFORMED_REQUESTS
    assert behaviors == {
        "GET /api/items": {
            "responses": len([scenario for scenario in scenarios if scenario not in MALFORMED_REQUESTS]),
            "sources": {
                "status": SEEN_EVERY_TIME,
                "structure": SEEN_EVERY_TIME,
                "failure": {
                    "distinct": distinct,
                    "singletons": 0,
                    "doubletons": 0,
                    "discovering": 0,
                    "discoveries": list(range(1, distinct + 1)),
                },
            },
        }
    }


@pytest.mark.parametrize("optional", [2, 6], ids=["few", "more-than-kept"])
def test_behavior_parameter_sets_of_accepted_requests(cli, ctx, ndjson_path, optional):
    names = [f"filter{number}" for number in range(optional)]
    app, _ = ctx.openapi.make_flask_app(
        {
            "/api/items": {
                "get": {
                    "parameters": [
                        {"name": name, "in": "query", "schema": {"type": "boolean"}} for name in [*names, "refused"]
                    ],
                    "responses": {"200": {"description": "OK"}, "400": {"description": "Refused"}},
                }
            }
        }
    )

    @app.route("/api/items")
    def items():
        return (jsonify({}), 400) if "refused" in request.args else jsonify({})

    # Enough requests to send more sets of parameters than are kept.
    cli.run_openapi_app(
        app,
        f"--report-ndjson-path={ndjson_path}",
        "--max-examples=100",
        "--phases=fuzzing",
        "--mode=positive",
        "--continue-on-failure",
        "--seed=1",
    )
    events = load_ndjson(ndjson_path)
    accepted = {
        frozenset(pair.partition("=")[0] for pair in interaction["request"]["uri"].partition("?")[2].split("&") if pair)
        for recorder in (get_event_data(event)["recorder"] for event in events if "ScenarioFinished" in event)
        for interaction in recorder.get("interactions", {}).values()
        if interaction["response"]["status_code"] == 200
    }
    assert (len(accepted) > 16) == (optional == 6)
    behaviors = get_event_data(events[-1])["payload"]["behaviors"]
    assert behaviors["GET /api/items"]["sources"]["parameters"]["distinct"] == min(16, len(accepted))


def test_behavior_counts_include_stateful_responses(cli, ctx, ndjson_path):
    api = ctx.openapi.apps.users_crud()
    cli.run(api.schema_url, f"--report-ndjson-path={ndjson_path}", "--max-examples=5", "--phases=stateful", "--seed=1")
    events = load_ndjson(ndjson_path)
    recorded = {}
    for event in events:
        if get_event_type(event) == "ScenarioFinished":
            recorder = get_event_data(event)["recorder"]
            for case_id in recorder.get("interactions", {}):
                case = recorder["cases"][case_id]["value"]
                label = f"{case['method']} {case['path']}"
                recorded[label] = recorded.get(label, 0) + 1
    behaviors = get_event_data(events[-1])["payload"]["behaviors"]
    assert {label: counts["responses"] for label, counts in behaviors.items()} == recorded
    assert set(recorded) == {"POST /users/", "GET /users/{user_id}", "PATCH /users/{user_id}"}


def test_writer_thread_error_is_reraised():
    def fail():
        raise RuntimeError("Something went wrong")

    worker = WriterWorker(name="SchemathesisTestWriter", target=fail, kwargs={})
    worker.start()
    with pytest.raises(RuntimeError, match="Something went wrong"):
        worker.join()
