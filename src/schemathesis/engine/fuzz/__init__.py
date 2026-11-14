from __future__ import annotations

from typing import TYPE_CHECKING

from schemathesis.engine import Status, events
from schemathesis.engine.run import PhaseProgress

if TYPE_CHECKING:
    from schemathesis.engine.context import EngineContext
    from schemathesis.engine.events import EventGenerator
    from schemathesis.engine.run import Phase


def execute(ctx: EngineContext, phase: Phase) -> EventGenerator:
    from schemathesis.engine.fuzz._executor import run_forever

    progress = PhaseProgress()
    for event in run_forever(ctx):
        _fold_event(event, progress)
        yield event
    yield events.PhaseFinished(phase=phase, status=progress.final_status(phase), payload=None)


def _fold_event(event: events.EngineEvent, progress: PhaseProgress) -> None:
    if isinstance(event, events.FuzzScenarioFinished):
        progress.is_executed = True
        progress.checks_ran = progress.checks_ran or any(event.recorder.checks.values())
        if event.status != Status.SKIP and (progress.status is None or progress.status < event.status):
            progress.status = event.status
    elif isinstance(event, events.NonFatalError):
        progress.record_error()
    elif isinstance(event, events.Interrupted):
        progress.status = Status.INTERRUPTED
