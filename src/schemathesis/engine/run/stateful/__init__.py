from __future__ import annotations

import queue
import threading
from typing import TYPE_CHECKING

from schemathesis.engine import Status, events
from schemathesis.engine.run import Phase, PhaseName, PhaseProgress, relay_events, report_outage
from schemathesis.generation.feedback import FeedbackSources
from schemathesis.generation.stateful import STATEFUL_TESTS_LABEL

if TYPE_CHECKING:
    from schemathesis.engine.context import EngineContext
    from schemathesis.generation.stateful.state_machine import APIStateMachine

EVENT_QUEUE_TIMEOUT = 0.01


def _stateful_feedback(engine: EngineContext) -> FeedbackSources:
    return FeedbackSources(
        extra_data_source=engine.extra_data_source,
        error_feedback=engine.error_feedback,
        constants_value_source=engine.constants_value_source(),
    )


def _run_state_machines(
    *, state_machine: type[APIStateMachine], event_queue: queue.Queue, engine: EngineContext, crashes: list[Exception]
) -> None:
    from schemathesis.engine.run.stateful._executor import execute_state_machine_loop

    try:
        execute_state_machine_loop(state_machine=state_machine, event_queue=event_queue, engine=engine)
    except Exception as exc:
        crashes.append(exc)


def execute(engine: EngineContext, phase: Phase) -> events.EventGenerator:
    try:
        state_machine = engine.schema.build_state_machine(
            feedback=_stateful_feedback(engine), link_calibration=engine.link_calibration
        )
    except Exception as exc:
        yield from events.fail_phase(phase, exc, label=STATEFUL_TESTS_LABEL)
        return

    event_queue: queue.Queue = queue.Queue()
    crashes: list[Exception] = []

    thread = threading.Thread(
        target=_run_state_machines,
        kwargs={"state_machine": state_machine, "event_queue": event_queue, "engine": engine, "crashes": crashes},
        name="schemathesis_stateful_tests",
    )
    progress = PhaseProgress()

    thread.start()
    try:
        for event in relay_events(event_queue, [thread], timeout=EVENT_QUEUE_TIMEOUT):
            progress.is_executed = True
            _fold_event(event, progress)
            yield event
    except KeyboardInterrupt:
        # Immediately notify the engine thread to stop, even though that the event will be set below in `finally`
        engine.stop()
        progress.status = Status.INTERRUPTED
        yield events.Interrupted(phase=PhaseName.STATEFUL_TESTING)
    except BaseException:
        engine.stop()
        raise
    finally:
        if thread is not threading.current_thread():
            thread.join()

    # A crash outside a suite leaves no suite status behind, so it must not read as a skipped phase.
    for error in crashes:
        progress.record_error()
        yield events.NonFatalError(
            error=error, phase=phase.name, label=STATEFUL_TESTS_LABEL, related_to_operation=False
        )

    yield from report_outage(engine, phase.name, progress)
    yield events.PhaseFinished(phase=phase, status=progress.final_status(phase), payload=None)


def _fold_event(event: events.EngineEvent, progress: PhaseProgress) -> None:
    # ERROR & INTERRUPTED statuses are terminal, therefore they should not be overridden
    if (
        isinstance(event, events.SuiteFinished)
        and event.status != Status.SKIP
        and (progress.status is None or progress.status < event.status)
    ):
        progress.status = event.status
    if isinstance(event, events.ScenarioFinished):
        progress.checks_ran = progress.checks_ran or any(event.recorder.checks.values())
