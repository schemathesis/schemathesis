"""Teardown phase: user cleanup once testing ends, however the run stopped."""

from __future__ import annotations

from typing import TYPE_CHECKING

from schemathesis.engine import Status, events
from schemathesis.hooks import dispatch_teardown

if TYPE_CHECKING:
    from schemathesis.engine.context import EngineContext
    from schemathesis.engine.events import EventGenerator
    from schemathesis.engine.run import Phase


def execute(ctx: EngineContext, phase: Phase) -> EventGenerator:
    try:
        errors = dispatch_teardown(ctx.schema)
    except KeyboardInterrupt:
        ctx.stop()
        yield events.Interrupted(phase=phase.name)
        yield events.PhaseFinished(phase=phase, status=Status.INTERRUPTED, payload=None)
        return
    for error in errors:
        yield events.hook_error(phase.name, error)
    yield events.PhaseFinished(phase=phase, status=Status.ERROR if errors else Status.SUCCESS, payload=None)
