from __future__ import annotations

import threading
import uuid
from queue import Queue
from typing import Any

import hypothesis
from flask import jsonify

import schemathesis
from schemathesis.config import HealthCheck, SchemathesisConfig
from schemathesis.engine import Status, events, from_schema
from schemathesis.engine.context import EngineContext
from schemathesis.engine.run import Phase, PhaseName, stateful
from schemathesis.engine.run.stateful._executor import (
    _classify_suite_error,
    _get_hypothesis_settings_kwargs_override,
)
from schemathesis.generation.stateful.state_machine import DEFAULT_STATE_MACHINE_SETTINGS
from test.engine._late_put import attach_late_put


class _RacyThread:
    def __init__(self, *, kwargs: dict[str, Any], **_: Any) -> None:
        self._event_queue: Queue = kwargs["event_queue"]

    def start(self) -> None:
        suite_id = uuid.uuid4()
        started = events.ScenarioStarted(label=None, phase=PhaseName.STATEFUL_TESTING, suite_id=suite_id)
        finished = events.SuiteFinished(id=suite_id, phase=PhaseName.STATEFUL_TESTING, status=Status.FAILURE)
        self._event_queue.put(started)
        attach_late_put(self._event_queue, finished)

    def is_alive(self) -> bool:
        return False

    def join(self) -> None:
        pass


def test_stateful_executor_drains_pending_events_after_thread_exit(ctx, monkeypatch):
    api = ctx.openapi.apps.users_crud()
    schema = schemathesis.openapi.from_url(api.schema_url)
    engine = EngineContext(schema=schema, stop_event=threading.Event())
    phase = Phase(name=PhaseName.STATEFUL_TESTING, is_enabled=True)

    monkeypatch.setattr(stateful.threading, "Thread", _RacyThread)

    emitted = list(stateful.execute(engine, phase))

    assert any(isinstance(event, events.SuiteFinished) for event in emitted)
    [phase_finished] = [event for event in emitted if isinstance(event, events.PhaseFinished)]
    assert phase_finished.status == Status.FAILURE


# Naming a subset of health checks must not re-enable the ones stateful testing suppresses by default.
def test_narrow_suppress_health_check_keeps_stateful_suppression():
    config = SchemathesisConfig()
    config.update(suppress_health_check=[HealthCheck.filter_too_much], max_failures=None)
    settings = config.projects.default.get_hypothesis_settings(phase="stateful")

    assert set(
        hypothesis.settings(settings, **_get_hypothesis_settings_kwargs_override(settings)).suppress_health_check
    ) == set(DEFAULT_STATE_MACHINE_SETTINGS.suppress_health_check)


def test_user_interrupt_after_the_deadline_is_not_a_clean_finish(ctx):
    # Ctrl-C and a spent budget both stop the suite; only the budget means "keep what you have".
    api = ctx.openapi.apps.users_crud()
    schema = schemathesis.openapi.from_url(api.schema_url)
    engine = EngineContext(schema=schema, stop_event=threading.Event(), max_time=0)
    engine.stop()

    status, _, emitted = _classify_suite_error(KeyboardInterrupt(), ctx=None, engine=engine, state=None, settings=None)

    assert status == Status.INTERRUPTED
    assert emitted


# One operation whose schema cannot produce a strategy must not take the whole phase with it.
def test_unbuildable_operation_does_not_abort_the_phase(ctx, app_runner):
    def collection(item_schema):
        return {
            "post": {
                "requestBody": {
                    "required": True,
                    "content": {"application/json": {"schema": item_schema}},
                },
                "responses": {
                    "201": {
                        "description": "Created",
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {"id": {"type": "integer"}},
                                }
                            }
                        },
                    }
                },
            }
        }

    def item(name):
        return {
            "get": {
                "parameters": [{"name": name, "in": "path", "required": True, "schema": {"type": "integer"}}],
                "responses": {"200": {"description": "OK"}},
            }
        }

    app, _ = ctx.openapi.make_flask_app(
        {
            "/users": collection({"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}),
            "/users/{userId}": item("userId"),
            # Swagger 2.0 parameter spelling; a Schema Object needs a list of property names here.
            "/items": collection({"type": "array", "items": {"type": "string", "required": True}}),
            "/items/{itemId}": item("itemId"),
        }
    )

    @app.route("/users", methods=["POST"])
    def create_user():
        return jsonify({"id": 1}), 201

    @app.route("/users/<int:user_id>", methods=["GET"])
    def get_user(user_id):
        return jsonify({"id": user_id}), 200

    @app.route("/items", methods=["POST"])
    def create_item():
        return jsonify({"id": 1}), 201

    @app.route("/items/<int:item_id>", methods=["GET"])
    def get_item(item_id):
        return jsonify({"id": item_id}), 200

    schema = schemathesis.openapi.from_url(app_runner.openapi_url(app))
    schema.config.phases.update(phases=["stateful"])
    schema.config.generation.update(max_examples=15)

    visited = set()
    reported = set()
    for event in from_schema(schema).execute():
        if isinstance(event, events.ScenarioFinished):
            for case in event.recorder.cases.values():
                visited.add(case.value.operation.label)
        elif isinstance(event, events.NonFatalError):
            reported.add(event.label)

    assert any(label.endswith("/users") for label in visited), (
        f"The broken operation took the whole phase with it; visited {sorted(visited)}"
    )
    assert "POST /items" in reported, f"The dropped operation was not reported; got {sorted(reported)}"
    assert not any(label.endswith("/items") for label in visited), (
        f"The broken operation should not have been exercised; visited {sorted(visited)}"
    )
