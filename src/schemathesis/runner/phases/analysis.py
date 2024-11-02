from __future__ import annotations

from ...internal.result import Err, Ok, Result
from ...service import extensions
from ...service.models import AnalysisResult, AnalysisSuccess
from .. import events
from ..context import EngineContext
from ..events import EventGenerator


def execute(ctx: EngineContext) -> EventGenerator:
    from ..phases import PhaseKind

    yield events.BeforeAnalysis()
    analysis: Result[AnalysisResult, Exception] | None = None
    if ctx.config.service_client is not None:
        try:
            assert ctx.config.service_client is not None, "Service client is missing"
            probes = ctx.phase_data.get(PhaseKind.PROBING, list) or []
            result = ctx.config.service_client.analyze_schema(probes, ctx.config.schema.raw_schema)
            if isinstance(result, AnalysisSuccess):
                extensions.apply(result.extensions, ctx.config.schema)
            analysis = Ok(result)
        except Exception as exc:
            analysis = Err(exc)
    yield events.AfterAnalysis(analysis=analysis)
