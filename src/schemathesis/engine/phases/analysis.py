from __future__ import annotations

from typing import TYPE_CHECKING

from schemathesis.core.errors import HookExecutionError
from schemathesis.engine import Status, events

if TYPE_CHECKING:
    from schemathesis.core.schema_analysis import SchemaWarning
    from schemathesis.engine.context import EngineContext
    from schemathesis.engine.events import EventGenerator
    from schemathesis.engine.phases import Phase


def execute(ctx: EngineContext, phase: Phase) -> EventGenerator:
    """Evaluate schema-level warnings once per test run."""
    try:
        warnings = _collect_warnings(ctx)
    except HookExecutionError as exc:
        yield events.NonFatalError(
            error=exc, phase=phase.name, label=f"`{exc.hook_name}` hook", related_to_operation=False
        )
        yield events.PhaseFinished(phase=phase, status=Status.ERROR, payload=None)
        return
    if warnings:
        yield events.SchemaAnalysisWarnings(phase=phase, warnings=warnings)
    yield events.PhaseFinished(phase=phase, status=Status.SUCCESS, payload=None)


def _collect_warnings(ctx: EngineContext) -> list[SchemaWarning]:
    from schemathesis.specs.openapi.schemas import OpenApiSchema

    schema = ctx.schema
    if isinstance(schema, OpenApiSchema):
        return list(schema.analysis.iter_warnings())
    return []
