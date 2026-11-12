from __future__ import annotations

from typing import TYPE_CHECKING

from schemathesis.core.errors import HookExecutionError
from schemathesis.engine import events
from schemathesis.python._constants.warnings import iter_constants_warnings

if TYPE_CHECKING:
    from collections.abc import Generator

    from schemathesis.engine.context import EngineContext
    from schemathesis.engine.events import EngineEvent
    from schemathesis.engine.run import Phase


def analyze(ctx: EngineContext, phase: Phase) -> Generator[EngineEvent, None, bool]:
    """Evaluate schema-level warnings once per test run; return whether a hook failed."""
    try:
        warnings = ctx.schema.iter_schema_warnings()
    except HookExecutionError as exc:
        yield events.NonFatalError(
            error=exc, phase=phase.name, label=f"`{exc.hook_name}` hook", related_to_operation=False
        )
        return True
    # Runs for any loaded app and any `@schemathesis.python.constants` source; records failures for the
    # latter so a registered source that silently produced nothing is surfaced as a warning.
    pool = ctx.extract_constants()
    warnings = [*warnings, *iter_constants_warnings(pool)]

    if warnings:
        yield events.SetupWarnings(phase=phase, warnings=warnings)
    return False
