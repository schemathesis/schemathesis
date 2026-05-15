from __future__ import annotations

import json
import socket
import threading
from pathlib import Path

import pytest
import requests
from flask import jsonify

import schemathesis
from schemathesis.config import HttpBearerAuthConfig
from schemathesis.core.cache import Entry, Kind, Manifest, Request, load, write
from schemathesis.core.error_feedback import ObservationKind
from schemathesis.core.error_feedback.collector import record_response
from schemathesis.core.parameters import ParameterLocation
from schemathesis.core.version import SCHEMATHESIS_VERSION
from schemathesis.engine.context import EngineContext
from schemathesis.engine.recorder import ScenarioRecorder
from schemathesis.engine.run import cache
from schemathesis.engine.supervisor import SchedulingDirective
from schemathesis.specs.openapi.auth_inference import record_auth_inference


def _manifest() -> Manifest:
    return Manifest(
        format_version=1,
        schemathesis_version=SCHEMATHESIS_VERSION,
        schema_location="openapi.yaml",
        base_url="http://example.com",
        created_at="2026-05-05T10:00:00Z",
    )


def _seed(directory: Path, entries: list[Entry]) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    write(directory, _manifest(), entries)


def _engine_for(schema) -> EngineContext:
    return EngineContext(schema=schema, stop_event=threading.Event())


def _point_cache_at(schema, directory: Path) -> None:
    schema.config._get_parent().cache.directory = directory


def _disable_cache(schema, tmp_path):
    schema.config._get_parent().cache.enabled = False
    _point_cache_at(schema, tmp_path)


def _point_at_missing_directory(schema, tmp_path):
    _point_cache_at(schema, tmp_path / "missing")


@pytest.mark.parametrize(
    "setup",
    [_disable_cache, _point_at_missing_directory],
    ids=["disabled", "no-cache-file"],
)
def test_run_returns_none(ctx, tmp_path, setup):
    api = ctx.openapi.apps.success()
    schema = schemathesis.openapi.from_url(api.schema_url)
    setup(schema, tmp_path)
    assert _engine_for(schema).cache.run() is None


def test_operation_missing_from_schema_is_dropped_without_request(ctx, tmp_path):
    api = ctx.openapi.apps.success()
    schema = schemathesis.openapi.from_url(api.schema_url)
    _point_cache_at(schema, tmp_path)
    _seed(
        tmp_path,
        [Entry(id=1, kind=Kind.METHOD_NOT_ALLOWED, operation="POST /vanished", request=Request(method="POST"))],
    )

    report = _engine_for(schema).cache.run()

    assert report == cache.CacheReport(replayed=0, dropped=1, skipped=0)
    assert [r for r in api.requests if not r.path.endswith("/openapi.json")] == []
    surviving = load(tmp_path)
    assert surviving is not None
    _, entries = surviving
    assert entries == []


def test_error_feedback_confirmed_hydrates_store(ctx, tmp_path):
    api = ctx.openapi.apps.rails_planted_bug(envelope="modern")
    schema = schemathesis.openapi.from_url(api.schema_url)
    _point_cache_at(schema, tmp_path)
    entry = Entry(
        id=1,
        kind=Kind.ERROR_FEEDBACK,
        operation="POST /users",
        request=Request(
            method="POST",
            headers={"content-type": "application/json"},
            body={"username": "", "title": "", "description": "", "tags": []},
        ),
    )
    _seed(tmp_path, [entry])
    engine = _engine_for(schema)

    report = engine.cache.run()

    assert report.replayed == 1
    assert report.dropped == 0
    assert engine.error_feedback.observations(operation_label="POST /users", location=ParameterLocation.BODY), (
        "Rails 422 should produce at least one parsed observation"
    )


def test_error_feedback_contradicted_drops_entry(ctx, tmp_path, app_runner):
    app, _ = ctx.openapi.make_flask_app(
        {
            "/x": {
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {"schema": {"type": "object"}},
                        },
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            }
        }
    )

    @app.route("/x", methods=["POST"])
    def x():
        return jsonify({"ok": True})

    schema = schemathesis.openapi.from_url(app_runner.openapi_url(app))
    _point_cache_at(schema, tmp_path)
    _seed(
        tmp_path,
        [
            Entry(
                id=1,
                kind=Kind.ERROR_FEEDBACK,
                operation="POST /x",
                request=Request(method="POST", headers={"content-type": "application/json"}, body={"a": 1}),
            )
        ],
    )

    report = _engine_for(schema).cache.run()

    assert report.dropped == 1
    surviving = load(tmp_path)
    assert surviving is not None
    _, entries = surviving
    assert entries == []


def test_method_not_allowed_confirmed_hydrates_skip(ctx, tmp_path):
    api = ctx.openapi.apps.unimplemented_method()
    schema = schemathesis.openapi.from_url(api.schema_url)
    _point_cache_at(schema, tmp_path)
    _seed(
        tmp_path,
        [
            Entry(
                id=1,
                kind=Kind.METHOD_NOT_ALLOWED,
                operation="POST /missing",
                request=Request(
                    method="POST",
                    headers={"content-type": "application/json"},
                    body={"name": "x"},
                ),
            )
        ],
    )
    engine = _engine_for(schema)

    report = engine.cache.run()

    assert report.replayed == 1
    verdict = engine.supervisor.verdict("POST /missing")
    assert verdict.directive is SchedulingDirective.SKIP
    assert "restored from cache" in (verdict.reason or "")


def test_method_not_allowed_contradicted_drops_entry(ctx, tmp_path, app_runner):
    app, _ = ctx.openapi.make_flask_app({"/x": {"post": {"responses": {"200": {"description": "OK"}}}}})

    @app.route("/x", methods=["POST"])
    def x():
        return jsonify({"ok": True})

    schema = schemathesis.openapi.from_url(app_runner.openapi_url(app))
    _point_cache_at(schema, tmp_path)
    _seed(
        tmp_path,
        [Entry(id=1, kind=Kind.METHOD_NOT_ALLOWED, operation="POST /x", request=Request(method="POST"))],
    )
    engine = _engine_for(schema)

    report = engine.cache.run()

    assert report.dropped == 1
    assert engine.supervisor.verdict("POST /x").directive is SchedulingDirective.RUN


def test_auth_required_confirmed_with_valid_credentials(ctx, tmp_path):
    api = ctx.openapi.apps.under_declared_security()
    schema = schemathesis.openapi.from_url(api.schema_url)
    schema.config.auth.openapi.schemes["BearerAuth"] = HttpBearerAuthConfig(bearer="real-token")
    _point_cache_at(schema, tmp_path)
    _seed(
        tmp_path,
        [Entry(id=1, kind=Kind.AUTH_REQUIRED, operation="GET /protected", request=Request(method="GET"))],
    )
    engine = _engine_for(schema)

    report = engine.cache.run()

    assert report.replayed == 1
    assert any(
        obs.kind is ObservationKind.REQUIRES_AUTHENTICATION
        for obs in engine.error_feedback.observations(operation_label="GET /protected", location=ParameterLocation.PATH)
    )
    assert "GET /protected" in schema._inferred_security


def test_auth_required_contradicted_when_unauth_response_succeeds(ctx, tmp_path, app_runner):
    app, _ = ctx.openapi.make_flask_app({"/protected": {"get": {"responses": {"200": {"description": "OK"}}}}})

    @app.route("/protected")
    def protected():
        return jsonify({"ok": True})

    schema = schemathesis.openapi.from_url(app_runner.openapi_url(app))
    _point_cache_at(schema, tmp_path)
    _seed(
        tmp_path,
        [Entry(id=1, kind=Kind.AUTH_REQUIRED, operation="GET /protected", request=Request(method="GET"))],
    )

    report = _engine_for(schema).cache.run()

    assert report.dropped == 1
    _, surviving = load(tmp_path)
    assert surviving == []


def test_replay_skips_observation_kinds_when_error_feedback_disabled(ctx, tmp_path):
    api = ctx.openapi.apps.under_declared_security()
    schema = schemathesis.openapi.from_url(api.schema_url)
    schema.config.phases.fuzzing.error_feedback.is_enabled = False
    _point_cache_at(schema, tmp_path)
    _seed(
        tmp_path,
        [
            Entry(id=1, kind=Kind.AUTH_REQUIRED, operation="GET /protected", request=Request(method="GET")),
            Entry(
                id=2,
                kind=Kind.ERROR_FEEDBACK,
                operation="GET /protected",
                request=Request(method="GET"),
                observation_keys=["fake"],
            ),
        ],
    )

    report = _engine_for(schema).cache.run()

    assert report.skipped == 2
    assert report.dropped == 0
    _, surviving = load(tmp_path)
    assert {entry.id for entry in surviving} == {1, 2}


def test_auth_required_skipped_when_no_credentials_configured(ctx, tmp_path):
    api = ctx.openapi.apps.under_declared_security()
    schema = schemathesis.openapi.from_url(api.schema_url)
    _point_cache_at(schema, tmp_path)
    _seed(
        tmp_path,
        [Entry(id=1, kind=Kind.AUTH_REQUIRED, operation="GET /protected", request=Request(method="GET"))],
    )
    engine = _engine_for(schema)

    report = engine.cache.run()

    assert report.skipped == 1
    assert report.dropped == 0
    surviving = load(tmp_path)
    assert surviving is not None
    _, entries = surviving
    assert [entry.id for entry in entries] == [1]


def test_skipped_on_network_error(ctx, tmp_path, app_runner):
    app, _ = ctx.openapi.make_flask_app({"/x": {"post": {"responses": {"200": {"description": "OK"}}}}})
    schema = schemathesis.openapi.from_url(app_runner.openapi_url(app))
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        closed_port = sock.getsockname()[1]
    schema.config.base_url = f"http://127.0.0.1:{closed_port}"
    schema.config.request_timeout = 1
    _point_cache_at(schema, tmp_path)
    _seed(
        tmp_path,
        [Entry(id=1, kind=Kind.METHOD_NOT_ALLOWED, operation="POST /x", request=Request(method="POST"))],
    )

    report = _engine_for(schema).cache.run()

    assert report.skipped == 1
    surviving = load(tmp_path)
    assert surviving is not None
    _, entries = surviving
    assert [entry.id for entry in entries] == [1]


def test_replay_rotates_across_runs_when_cache_exceeds_budget(ctx, tmp_path):
    api = ctx.openapi.apps.unimplemented_method()
    schema = schemathesis.openapi.from_url(api.schema_url)
    _point_cache_at(schema, tmp_path)
    entries = [
        Entry(
            id=i,
            kind=Kind.METHOD_NOT_ALLOWED,
            operation="POST /missing",
            request=Request(
                method="POST",
                headers={"content-type": "application/json"},
                body={"name": f"x-{i}"},
            ),
        )
        for i in range(cache._BUDGET + 5)
    ]
    _seed(tmp_path, entries)

    _engine_for(schema).cache.run()
    _, after_first = load(tmp_path)
    first_run_replayed = {entry.id for entry in after_first if entry.last_replayed_run == 1}
    assert len(first_run_replayed) == cache._BUDGET

    _engine_for(schema).cache.run()
    _, after_second = load(tmp_path)
    second_run_replayed = {entry.id for entry in after_second if entry.last_replayed_run == 2}
    originally_untouched = {entry.id for entry in entries} - first_run_replayed
    assert originally_untouched <= second_run_replayed


def test_budget_caps_replays(ctx, tmp_path):
    api = ctx.openapi.apps.unimplemented_method()
    schema = schemathesis.openapi.from_url(api.schema_url)
    _point_cache_at(schema, tmp_path)
    entries = [
        Entry(
            id=i,
            kind=Kind.METHOD_NOT_ALLOWED,
            operation="POST /missing",
            request=Request(
                method="POST",
                headers={"content-type": "application/json"},
                body={"name": f"x-{i}"},
            ),
        )
        for i in range(cache._BUDGET + 5)
    ]
    _seed(tmp_path, entries)

    report = _engine_for(schema).cache.run()

    assert report.replayed == cache._BUDGET
    surviving = load(tmp_path)
    assert surviving is not None
    _, surviving_entries = surviving
    assert len(surviving_entries) == cache._BUDGET + 5


def test_flush_persists_pending_entries(ctx, tmp_path):
    api = ctx.openapi.apps.success()
    schema = schemathesis.openapi.from_url(api.schema_url)
    _point_cache_at(schema, tmp_path)
    engine = _engine_for(schema)

    engine.cache.record(
        Kind.ERROR_FEEDBACK,
        "POST /users",
        Request(method="POST", headers={"content-type": "application/json"}, body={"email": "x"}),
    )

    engine.cache.flush()

    loaded = load(tmp_path)
    assert loaded is not None
    _, entries = loaded
    assert len(entries) == 1
    assert entries[0].kind is Kind.ERROR_FEEDBACK
    assert entries[0].operation == "POST /users"
    assert entries[0].id == 1


def test_flush_dedups_against_existing_entries(ctx, tmp_path):
    api = ctx.openapi.apps.success()
    schema = schemathesis.openapi.from_url(api.schema_url)
    _point_cache_at(schema, tmp_path)
    request = Request(method="POST", headers={"content-type": "application/json"}, body={"email": "x"})
    existing = Entry(id=42, kind=Kind.ERROR_FEEDBACK, operation="POST /users", request=request)
    write(tmp_path, _manifest(), [existing])

    engine = _engine_for(schema)
    engine.cache.record(Kind.ERROR_FEEDBACK, "POST /users", request)
    engine.cache.flush()

    _, entries = load(tmp_path)
    assert len(entries) == 1
    assert entries[0].id == 42


def test_flush_appends_new_entries_to_existing_file(ctx, tmp_path):
    api = ctx.openapi.apps.success()
    schema = schemathesis.openapi.from_url(api.schema_url)
    _point_cache_at(schema, tmp_path)
    existing = Entry(
        id=1,
        kind=Kind.METHOD_NOT_ALLOWED,
        operation="POST /old",
        request=Request(method="POST", body={"a": 1}),
    )
    write(tmp_path, _manifest(), [existing])

    engine = _engine_for(schema)
    engine.cache.record(
        Kind.ERROR_FEEDBACK,
        "POST /new",
        Request(method="POST", headers={"content-type": "application/json"}, body={"email": "x"}),
    )
    engine.cache.flush()

    _, entries = load(tmp_path)
    ids = sorted(e.id for e in entries)
    assert ids == [1, 2]
    operations = {e.operation for e in entries}
    assert operations == {"POST /old", "POST /new"}


def test_flush_is_noop_when_cache_disabled(ctx, tmp_path):
    api = ctx.openapi.apps.success()
    schema = schemathesis.openapi.from_url(api.schema_url)
    schema.config._get_parent().cache.enabled = False
    _point_cache_at(schema, tmp_path)
    engine = _engine_for(schema)
    engine.cache.record(Kind.ERROR_FEEDBACK, "POST /users", Request(method="POST", body={"a": 1}))

    engine.cache.flush()

    assert load(tmp_path) is None


def test_flush_swallows_disk_errors(ctx, tmp_path):
    api = ctx.openapi.apps.success()
    schema = schemathesis.openapi.from_url(api.schema_url)
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory")
    _point_cache_at(schema, blocker)
    engine = _engine_for(schema)
    engine.cache.record(Kind.ERROR_FEEDBACK, "POST /users", Request(method="POST", body={"a": 1}))

    engine.cache.flush()


def test_error_feedback_discovery_writes_to_cache_on_flush(ctx, tmp_path):
    api = ctx.openapi.apps.rails_planted_bug(envelope="modern")
    schema = schemathesis.openapi.from_url(api.schema_url)
    _point_cache_at(schema, tmp_path)
    engine = _engine_for(schema)

    operation = schema["/users"]["POST"]
    case = operation.Case(
        headers={"content-type": "application/json"},
        body={"username": "", "title": "", "description": "", "tags": []},
    )
    response = case.call(**engine.get_transport_kwargs(operation=operation))

    record_response(
        store=engine.error_feedback,
        operation=operation,
        case=case,
        response=response,
        cache_writer=engine.cache.writer,
    )
    engine.cache.flush()

    loaded = load(tmp_path)
    assert loaded is not None
    _, entries = loaded
    assert any(entry.kind is Kind.ERROR_FEEDBACK and entry.operation == "POST /users" for entry in entries)


def _record_run(schema, operation, body):
    engine = _engine_for(schema)
    engine.cache.run()
    case = operation.Case(headers={"content-type": "application/json"}, body=body)
    response = case.call(**engine.get_transport_kwargs(operation=operation))
    record_response(
        store=engine.error_feedback,
        operation=operation,
        case=case,
        response=response,
        cache_writer=engine.cache.writer,
    )
    engine.cache.flush()


def _feedback_keys(entries):
    return {key for entry in entries if entry.kind is Kind.ERROR_FEEDBACK for key in entry.observation_keys}


def test_progressive_observations_accumulate_across_runs(ctx, tmp_path):
    # Rails handler emits one violation per response, so each layer body holds exactly one empty field.
    api = ctx.openapi.apps.rails_planted_bug(envelope="modern")
    schema = schemathesis.openapi.from_url(api.schema_url)
    _point_cache_at(schema, tmp_path)
    operation = schema["/users"]["POST"]

    layer_a_body = {"username": "", "title": "valid-title", "description": "valid-description", "tags": ["t"]}
    layer_b_body = {"username": "valid-name", "title": "", "description": "valid-description", "tags": ["t"]}

    _record_run(schema, operation, layer_a_body)
    _, after_run_1 = load(tmp_path)
    layer_a_keys = _feedback_keys(after_run_1)
    assert layer_a_keys, "Run 1 should have produced at least one cached observation"

    _record_run(schema, operation, layer_b_body)
    _, after_run_2 = load(tmp_path)
    layer_b_keys = _feedback_keys(after_run_2)
    feedback_entries = [entry for entry in after_run_2 if entry.kind is Kind.ERROR_FEEDBACK]
    assert layer_a_keys <= layer_b_keys
    assert layer_b_keys - layer_a_keys, "Layer B's observation should appear as a new cached entry"
    seen: set[str] = set()
    for entry in feedback_entries:
        assert seen.isdisjoint(entry.observation_keys)
        seen.update(entry.observation_keys)
    assert len(feedback_entries) <= len(seen)


def test_flush_caps_entries_per_operation(ctx, tmp_path):
    api = ctx.openapi.apps.success()
    schema = schemathesis.openapi.from_url(api.schema_url)
    _point_cache_at(schema, tmp_path)
    cap = cache._MAX_ENTRIES_PER_OPERATION
    bloated = [
        Entry(
            id=i + 1,
            kind=Kind.ERROR_FEEDBACK,
            operation="POST /users",
            request=Request(method="POST", headers={"content-type": "application/json"}, body={"x": i}),
            observation_keys=[f"key-{i}"],
        )
        for i in range(cap + 25)
    ]
    write(tmp_path, _manifest(), bloated)

    engine = _engine_for(schema)
    engine.cache.record(
        Kind.ERROR_FEEDBACK,
        "POST /users",
        Request(method="POST", headers={"content-type": "application/json"}, body={"x": "fresh"}),
        observation_keys=["fresh-key"],
    )
    engine.cache.flush()

    _, surviving = load(tmp_path)
    assert len(surviving) == cap
    assert any("fresh-key" in entry.observation_keys for entry in surviving)
    seen: set[str] = set()
    for entry in surviving:
        assert seen.isdisjoint(entry.observation_keys)
        seen.update(entry.observation_keys)
    assert len(surviving) <= len(seen)


def test_cache_metrics_serialize_in_ndjson(ctx, cli, tmp_path):
    api = ctx.openapi.apps.unimplemented_method()
    cache_dir = tmp_path / "cache"
    _seed(
        cache_dir,
        [
            Entry(
                id=1,
                kind=Kind.METHOD_NOT_ALLOWED,
                operation="POST /missing",
                request=Request(
                    method="POST",
                    headers={"content-type": "application/json"},
                    body={"name": "x"},
                ),
            )
        ],
    )
    ndjson_path = tmp_path / "events.ndjson"
    result = cli.run(
        api.schema_url,
        "--max-examples=1",
        "--phases=fuzzing",
        config={
            "cache": {"directory": str(cache_dir)},
            "reports": {"ndjson": {"enabled": True, "path": str(ndjson_path)}},
        },
    )
    assert result.exit_code == 0

    cache_report = None
    for line in ndjson_path.read_text(encoding="utf-8").splitlines():
        if not line:
            continue
        event = json.loads(line)
        ((name, payload),) = event.items()
        if name != "PhaseFinished":
            continue
        phase = payload.get("phase") or {}
        if phase.get("name") != "API probing":
            continue
        cache_report = payload.get("payload", {}).get("cache")
        break
    assert cache_report is not None, "PhaseFinished(PROBING) should carry a `cache` payload"
    assert cache_report.get("replayed") == 1
    assert cache_report.get("dropped", 0) == 0

    summary = None
    for line in ndjson_path.read_text(encoding="utf-8").splitlines():
        if not line:
            continue
        event = json.loads(line)
        ((name, payload),) = event.items()
        if name == "EngineFinished":
            summary = payload.get("payload")
            break
    assert summary is not None, "EngineFinished should carry a RunSummary payload"
    assert isinstance(summary["cache"]["observations_total"], int)


def test_error_feedback_dedups_by_observation_not_by_request(ctx, tmp_path):
    # Identical observation set with per-variant unique request bytes — dedup must collapse on observations.
    api = ctx.openapi.apps.rails_planted_bug(envelope="modern")
    schema = schemathesis.openapi.from_url(api.schema_url)
    _point_cache_at(schema, tmp_path)
    engine = _engine_for(schema)
    operation = schema["/users"]["POST"]
    transport_kwargs = engine.get_transport_kwargs(operation=operation)

    for variant in range(20):
        case = operation.Case(
            headers={"content-type": "application/json"},
            body={
                "username": "",
                "title": "",
                "description": "",
                "tags": [],
                f"extra_{variant}": f"junk-{variant}",
            },
        )
        response = case.call(**transport_kwargs)
        record_response(
            store=engine.error_feedback,
            operation=operation,
            case=case,
            response=response,
            cache_writer=engine.cache.writer,
        )

    engine.cache.flush()
    _, entries = load(tmp_path)
    feedback_entries = [e for e in entries if e.kind is Kind.ERROR_FEEDBACK]
    # Rails planted_bug exposes 3 bounded fields (username/title/description); all 20 variants
    # produce the same 3 observations, so a single entry must absorb the full covering set.
    assert len(feedback_entries) == 1
    assert len(feedback_entries[0].observation_keys) == 3


def test_supervisor_threshold_writes_to_cache_on_flush(ctx, tmp_path):
    api = ctx.openapi.apps.unimplemented_method()
    schema = schemathesis.openapi.from_url(api.schema_url)
    _point_cache_at(schema, tmp_path)
    engine = _engine_for(schema)

    operation = schema["/missing"]["POST"]
    case = operation.Case(headers={"content-type": "application/json"}, body={"name": "x"})
    for _ in range(10):
        engine.supervisor.record_response(
            operation_label=operation.label,
            status_code=405,
            is_documented_status=False,
            case=case,
            cache_writer=engine.cache.writer,
        )

    assert engine.supervisor.verdict(operation.label).directive is SchedulingDirective.SKIP
    engine.cache.flush()

    _, entries = load(tmp_path)
    assert any(entry.kind is Kind.METHOD_NOT_ALLOWED and entry.operation == "POST /missing" for entry in entries)


def test_auth_inference_discovery_writes_to_cache_on_flush(ctx, tmp_path):
    api = ctx.openapi.apps.under_declared_security()
    schema = schemathesis.openapi.from_url(api.schema_url)
    schema.config.auth.openapi.schemes["BearerAuth"] = HttpBearerAuthConfig(bearer="real-token")
    _point_cache_at(schema, tmp_path)
    engine = _engine_for(schema)

    operation = schema["/protected"]["GET"]
    case = operation.Case()
    unauth_session = requests.Session()
    transport_kwargs = engine.get_transport_kwargs(operation=operation)
    response = case.call(**{**transport_kwargs, "session": unauth_session})
    assert response.status_code == 401

    recorder = ScenarioRecorder(label="test")
    recorder.record_case(parent_id=None, case=case, transition=None, is_transition_applied=False)
    recorder.record_response(case_id=case.id, response=response)

    record_auth_inference(
        store=engine.error_feedback,
        recorder=recorder,
        case=case,
        response=response,
        transport_kwargs=engine.get_transport_kwargs(operation=operation),
        cache_writer=engine.cache.writer,
    )
    engine.cache.flush()

    _, entries = load(tmp_path)
    assert any(entry.kind is Kind.AUTH_REQUIRED and entry.operation == "GET /protected" for entry in entries)
