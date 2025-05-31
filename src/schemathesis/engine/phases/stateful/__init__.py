from __future__ import annotations

import queue
import threading
from typing import TYPE_CHECKING

from schemathesis.engine import Status, events
from schemathesis.engine.phases import Phase, PhaseName, PhaseSkipReason
from schemathesis.generation.stateful import STATEFUL_TESTS_LABEL

if TYPE_CHECKING:
    from schemathesis.engine.context import EngineContext

EVENT_QUEUE_TIMEOUT = 0.01


def execute(engine: EngineContext, phase: Phase) -> events.EventGenerator:
    from schemathesis.engine.phases.stateful._executor import execute_state_machine_loop

    try:
        state_machine = engine.schema.as_state_machine()
    except Exception as exc:
        yield events.NonFatalError(error=exc, phase=phase.name, label=STATEFUL_TESTS_LABEL, related_to_operation=False)
        yield events.PhaseFinished(phase=phase, status=Status.ERROR, payload=None)
        return

    event_queue: queue.Queue = queue.Queue()

    thread = threading.Thread(
        target=execute_state_machine_loop,
        kwargs={"state_machine": state_machine, "event_queue": event_queue, "engine": engine},
        name="schemathesis_stateful_tests",
    )
    status: Status | None = None
    is_executed = False

    thread.start()
    try:
        while True:
            try:
                event = event_queue.get(timeout=EVENT_QUEUE_TIMEOUT)
                is_executed = True
                # Set the run status based on the suite status
                # ERROR & INTERRUPTED statuses are terminal, therefore they should not be overridden
                if (
                    isinstance(event, events.SuiteFinished)
                    and event.status != Status.SKIP
                    and (status is None or status < event.status)
                ):
                    status = event.status
                yield event
            except queue.Empty:
                if not thread.is_alive():
                    break
    except KeyboardInterrupt:
        # Immediately notify the engine thread to stop, even though that the event will be set below in `finally`
        engine.stop()
        status = Status.INTERRUPTED
        yield events.Interrupted(phase=PhaseName.STATEFUL_TESTING)
    finally:
        thread.join()

    if not is_executed:
        phase.skip_reason = PhaseSkipReason.NOTHING_TO_TEST
        status = Status.SKIP
    elif status is None:
        status = Status.SKIP
    yield events.PhaseFinished(phase=phase, status=status, payload=None)
