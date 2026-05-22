from __future__ import annotations

from typing import TYPE_CHECKING

from schemathesis.core.errors import HookExecutionError
from schemathesis.engine import Status, events

if TYPE_CHECKING:
    from schemathesis.engine.context import EngineContext
    from schemathesis.engine.events import EventGenerator
    from schemathesis.engine.run import Phase


def execute(ctx: EngineContext, phase: Phase) -> EventGenerator:
    """Evaluate schema-level warnings once per test run."""
    try:
        warnings = ctx.schema.iter_schema_warnings()
    except HookExecutionError as exc:
        yield events.NonFatalError(
            error=exc, phase=phase.name, label=f"`{exc.hook_name}` hook", related_to_operation=False
        )
        yield events.PhaseFinished(phase=phase, status=Status.ERROR, payload=None)
        return
    if warnings:
        yield events.SchemaAnalysisWarnings(phase=phase, warnings=warnings)

    # Harvest literal constants from user-supplied Python source. No-op when no source
    # callable was registered via `@schemathesis.python.constants` — the lazy access
    # only triggers AST scanning if there's something to scan.
    _ = ctx.constants_value_source

    yield events.PhaseFinished(phase=phase, status=Status.SUCCESS, payload=None)
