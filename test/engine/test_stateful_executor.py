from __future__ import annotations

import threading
import uuid
from queue import Queue
from typing import Any

import hypothesis

import schemathesis
from schemathesis.config import HealthCheck, SchemathesisConfig
from schemathesis.engine import Status, events
from schemathesis.engine.context import EngineContext
from schemathesis.engine.run import Phase, PhaseName, stateful
from schemathesis.engine.run.stateful._executor import _get_hypothesis_settings_kwargs_override
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
