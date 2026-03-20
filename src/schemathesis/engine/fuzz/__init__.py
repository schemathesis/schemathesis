from __future__ import annotations

from typing import TYPE_CHECKING

from schemathesis.engine import events

if TYPE_CHECKING:
    from schemathesis.config._fuzz import FuzzConfig
    from schemathesis.engine.context import EngineContext
    from schemathesis.engine.events import EventGenerator


def execute(ctx: EngineContext, config: FuzzConfig) -> EventGenerator:
    from schemathesis.engine.fuzz._executor import run_forever

    yield events.EngineStarted()
    yield from run_forever(ctx, config)
    yield events.EngineFinished(running_time=ctx.running_time, stop_reason=ctx.stop_reason)
