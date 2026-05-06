from __future__ import annotations

import threading
import uuid
from queue import Queue
from typing import Any

import schemathesis
from schemathesis.engine import Status, events
from schemathesis.engine.context import EngineContext
from schemathesis.engine.recorder import ScenarioRecorder
from schemathesis.engine.run import Phase, PhaseName, unit
from test.engine._late_put import attach_late_put


class _RacyPool:
    def __init__(self, *, phase: PhaseName, suite_id: uuid.UUID, **_: Any) -> None:
        self.events_queue: Queue = Queue()
        exited = threading.Thread(target=lambda: None, daemon=True)
        exited.start()
        exited.join()
        self.workers = [exited]

        started = events.ScenarioStarted(phase=phase, suite_id=suite_id, label="GET /api/success")
        finished = events.ScenarioFinished(
            id=started.id,
            phase=phase,
            suite_id=suite_id,
            label="GET /api/success",
            status=Status.SUCCESS,
            recorder=ScenarioRecorder(label="GET /api/success"),
            elapsed_time=0.0,
            skip_reason=None,
            is_final=False,
        )
        self.events_queue.put(started)
        attach_late_put(self.events_queue, finished)

    def __enter__(self) -> _RacyPool:
        return self

    def __exit__(self, *_: object) -> None:
        pass


def test_unit_executor_drains_pending_events_after_worker_exit(ctx, monkeypatch):
    api = ctx.openapi.apps.success()
    schema = schemathesis.openapi.from_url(api.schema_url)
    engine = EngineContext(schema=schema, stop_event=threading.Event())
    phase = Phase(name=PhaseName.FUZZING, is_supported=True, is_enabled=True)

    monkeypatch.setattr(unit, "WorkerPool", _RacyPool)

    emitted = list(unit.execute(engine, phase))

    assert any(isinstance(event, events.ScenarioFinished) for event in emitted)
    [phase_finished] = [event for event in emitted if isinstance(event, events.PhaseFinished)]
    assert phase_finished.status == Status.SUCCESS
