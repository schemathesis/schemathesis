from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from schemathesis.core.errors import format_exception
from schemathesis.core.result import Err, Ok, Result

from ...service import extensions
from ...service.models import AnalysisResult, AnalysisSuccess
from .. import events
from ..context import EngineContext

if TYPE_CHECKING:
    from schemathesis.runner.events import EventGenerator
    from schemathesis.runner.phases import Phase


@dataclass
class AnalysisPayload:
    data: Result[AnalysisResult, Exception] | None = None

    def asdict(self) -> dict[str, Any]:
        data = {}
        if isinstance(self.data, Ok):
            result = self.data.ok()
            if isinstance(result, AnalysisSuccess):
                data["analysis_id"] = result.id
            else:
                data["error"] = result.message
        elif isinstance(self.data, Err):
            data["error"] = format_exception(self.data.err())
        return data


def execute(ctx: EngineContext, phase: Phase) -> EventGenerator:
    from schemathesis.runner.models.status import Status

    assert ctx.config.service_client is not None
    data: Result[AnalysisResult, Exception] | None = None
    try:
        probes = ctx.phase_data.get(phase.name, list) or []
        result = ctx.config.service_client.analyze_schema(probes, ctx.config.schema.raw_schema)
        if isinstance(result, AnalysisSuccess):
            status = Status.SUCCESS
            extensions.apply(result.extensions, ctx.config.schema)
        else:
            status = Status.ERROR
        data = Ok(result)
    except Exception as exc:
        data = Err(exc)
        status = Status.ERROR
    yield events.PhaseFinished(phase=phase, status=status, payload=AnalysisPayload(data=data))
