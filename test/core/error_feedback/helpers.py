from __future__ import annotations

import json

from schemathesis.core.error_feedback.collector import record_response
from schemathesis.core.transport import Response
from schemathesis.generation import GenerationMode
from schemathesis.generation.meta import (
    CaseMetadata,
    FuzzingPhaseData,
    GenerationInfo,
    PhaseInfo,
    TestPhase,
)


def meta_for(mode: GenerationMode) -> CaseMetadata:
    return CaseMetadata(
        generation=GenerationInfo(time=0.0, mode=mode),
        components={},
        phase=PhaseInfo(
            name=TestPhase.FUZZING,
            data=FuzzingPhaseData(
                description="",
                parameter=None,
                parameter_location=None,
                location=None,
            ),
        ),
    )


def record_400(store, *, operation, case, body, response_factory):
    response = Response.from_any(
        response_factory.requests(
            content=json.dumps(body).encode(),
            content_type="application/json",
            status_code=400,
        )
    )
    record_response(store=store, operation=operation, case=case, response=response)
