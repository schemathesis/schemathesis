from __future__ import annotations

import queue
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Generator

from schemathesis.runner.phases import PhaseName

from ... import events
from ...models import Status, TestResult

if TYPE_CHECKING:
    from schemathesis.runner.phases import Phase

    from ...context import EngineContext
    from ...events import EventGenerator

EVENT_QUEUE_TIMEOUT = 0.01


@dataclass
class StatefulTestingPayload:
    result: TestResult
    transitions: dict
    elapsed_time: float

    def asdict(self) -> dict[str, Any]:
        return {
            "result": self.result.asdict(),
            "transitions": self.transitions,
            "elapsed_time": self.elapsed_time,
        }


def execute(engine: EngineContext, phase: Phase) -> EventGenerator:
    from schemathesis.runner.phases.stateful._executor import execute_state_machine_loop

    result = TestResult(label="Stateful tests")
    started_at = time.monotonic()

    state_machine = engine.config.schema.as_state_machine()

    event_queue: queue.Queue = queue.Queue()

    runner_thread = threading.Thread(
        target=execute_state_machine_loop,
        kwargs={"state_machine": state_machine, "event_queue": event_queue, "engine": engine},
    )
    status = Status.SUCCESS

    with thread_manager(runner_thread):
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
                    elif isinstance(event, events.StepFinished):
                        result.checks.extend(event.checks)
                        if event.response is not None and event.status is not None:
                            result.store_requests_response(
                                status=event.status,
                                case=event.case,
                                response=event.response,
                                checks=event.checks,
                                session=engine.session,
                            )
                    elif isinstance(event, events.Errored):
                        status = Status.ERROR
                        result.add_error(event.exception)
                    yield event
                except queue.Empty:
                    if not runner_thread.is_alive():
                        break
        except KeyboardInterrupt:
            # Immediately notify the runner thread to stop, even though that the event will be set below in `finally`
            engine.control.stop()
            status = Status.INTERRUPTED
            yield events.Interrupted(phase=PhaseName.STATEFUL_TESTING)

    engine.add_result(result)
    yield events.PhaseFinished(
        phase=phase,
        status=status,
        payload=StatefulTestingPayload(
            result=result,
            transitions=state_machine._transition_stats_template.transitions,  # type: ignore[attr-defined]
            elapsed_time=time.monotonic() - started_at,
        ),
    )


@contextmanager
def thread_manager(thread: threading.Thread) -> Generator[None, None, None]:
    thread.start()
    try:
        yield
    finally:
        thread.join()
