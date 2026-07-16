from __future__ import annotations

from typing import TYPE_CHECKING

from schemathesis.core.errors import HookExecutionError
from schemathesis.engine import Status, events
from schemathesis.python._constants.warnings import iter_constants_warnings

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
    # Runs for any loaded app and any `@schemathesis.python.constants` source; records failures for the
    # latter so a registered source that silently produced nothing is surfaced as a warning.
    pool = ctx.extract_constants()
    warnings = [*warnings, *iter_constants_warnings(pool)]

    if warnings:
        yield events.SchemaAnalysisWarnings(phase=phase, warnings=warnings)

    yield events.PhaseFinished(phase=phase, status=Status.SUCCESS, payload=None)
