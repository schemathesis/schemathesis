from __future__ import annotations

import queue
import threading
from typing import TYPE_CHECKING

from schemathesis.engine import Status, events
from schemathesis.engine.run import Phase, PhaseName, PhaseSkipReason
from schemathesis.generation.feedback import FeedbackSources
from schemathesis.generation.stateful import STATEFUL_TESTS_LABEL

if TYPE_CHECKING:
    from schemathesis.engine.context import EngineContext

EVENT_QUEUE_TIMEOUT = 0.01


def _stateful_feedback(engine: EngineContext) -> FeedbackSources:
    return FeedbackSources(
        extra_data_source=engine.extra_data_source,
        error_feedback=engine.error_feedback,
        constants_value_source=engine.constants_value_source(),
    )


def execute(engine: EngineContext, phase: Phase) -> events.EventGenerator:
    from schemathesis.engine.run.stateful._executor import execute_state_machine_loop

    try:
        state_machine = engine.schema._build_state_machine(
            feedback=_stateful_feedback(engine), link_calibration=engine.link_calibration
        )
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
    checks_ran = False

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
                if isinstance(event, events.ScenarioFinished):
                    checks_ran = checks_ran or any(event.recorder.checks.values())
                yield event
            except queue.Empty:
                # The producer may put its final events and exit between this thread's
                # get(timeout=...) raising Empty and the liveness check below.
                # Stop only when the producer exited AND there is nothing left to drain.
                if not thread.is_alive() and event_queue.empty():
                    break
    except KeyboardInterrupt:
        # Immediately notify the engine thread to stop, even though that the event will be set below in `finally`
        engine.stop()
        status = Status.INTERRUPTED
        yield events.Interrupted(phase=PhaseName.STATEFUL_TESTING)
    except GeneratorExit:
        engine.stop()
        raise
    finally:
        if thread is not threading.current_thread():
            thread.join()

    outage = engine.server.take_report(phase.name)
    if outage is not None:
        is_executed = True
        status = Status.ERROR
        yield outage

    if not is_executed:
        phase.skip_reason = PhaseSkipReason.NOTHING_TO_TEST
        status = Status.SKIP
    elif status is None:
        status = Status.SKIP
    elif status == Status.SUCCESS and not checks_ran:
        phase.skip_reason = PhaseSkipReason.NO_CHECKS_RAN
        status = Status.SKIP
    yield events.PhaseFinished(phase=phase, status=status, payload=None)
