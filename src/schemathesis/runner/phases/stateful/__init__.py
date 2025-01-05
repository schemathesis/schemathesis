from __future__ import annotations

import queue
import threading
from dataclasses import dataclass
from typing import TYPE_CHECKING

from schemathesis.runner import Status, events
from schemathesis.runner.phases import Phase, PhaseName

if TYPE_CHECKING:
    from ...context import EngineContext

EVENT_QUEUE_TIMEOUT = 0.01


@dataclass
class StatefulTestingPayload:
    transitions: dict

    __slots__ = ("transitions",)


def execute(engine: EngineContext, phase: Phase) -> events.EventGenerator:
    from schemathesis.runner.phases.stateful._executor import execute_state_machine_loop

    try:
        state_machine = engine.config.schema.as_state_machine()
    except Exception as exc:
        yield events.NonFatalError(error=exc, phase=phase.name, label="Stateful tests", related_to_operation=False)
        return

    event_queue: queue.Queue = queue.Queue()

    thread = threading.Thread(
        target=execute_state_machine_loop,
        kwargs={"state_machine": state_machine, "event_queue": event_queue, "engine": engine},
    )
    status = Status.SUCCESS

    thread.start()
    try:
        while True:
            try:
                event = event_queue.get(timeout=EVENT_QUEUE_TIMEOUT)
                # Set the run status based on the suite status
                # ERROR & INTERRUPTED statuses are terminal, therefore they should not be overridden
                if (
                    isinstance(event, events.SuiteFinished)
                    and status not in (Status.ERROR, Status.INTERRUPTED)
                    and event.status
                    in (
                        Status.FAILURE,
                        Status.ERROR,
                        Status.INTERRUPTED,
                    )
                ):
                    status = event.status
                yield event
            except queue.Empty:
                if not thread.is_alive():
                    break
    except KeyboardInterrupt:
        # Immediately notify the runner thread to stop, even though that the event will be set below in `finally`
        engine.stop()
        status = Status.INTERRUPTED
        yield events.Interrupted(phase=PhaseName.STATEFUL_TESTING)
    finally:
        thread.join()

    yield events.PhaseFinished(
        phase=phase,
        status=status,
        payload=StatefulTestingPayload(
            transitions=state_machine._transition_stats_template.transitions,  # type: ignore[attr-defined]
        ),
    )
