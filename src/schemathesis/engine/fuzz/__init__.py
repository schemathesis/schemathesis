from __future__ import annotations

from typing import TYPE_CHECKING

from schemathesis.engine import events

if TYPE_CHECKING:
    from schemathesis.config._fuzz import FuzzConfig
    from schemathesis.engine.context import EngineContext
    from schemathesis.engine.events import EventGenerator


def execute(ctx: EngineContext, config: FuzzConfig) -> EventGenerator:
    from schemathesis.engine._check_context import run_after_run_checks
    from schemathesis.engine.core import preflight_error
    from schemathesis.engine.fuzz._executor import run_forever

    yield events.EngineStarted()
    error = preflight_error(ctx)
    if error is not None:
        yield error
        yield events.EngineFinished(running_time=ctx.running_time, stop_reason=ctx.stop_reason)
        return
    yield from run_forever(ctx, config)
    yield events.EngineFinished(
        running_time=ctx.running_time,
        stop_reason=ctx.stop_reason,
        failures=run_after_run_checks(ctx),
    )
