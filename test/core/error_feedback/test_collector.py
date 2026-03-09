from __future__ import annotations

import pytest

from schemathesis.core.error_feedback.collector import parse_observations
from schemathesis.core.transport import Response
from schemathesis.generation.meta import (
    CaseMetadata,
    FuzzingPhaseData,
    GenerationInfo,
    PhaseInfo,
    TestPhase,
)
from schemathesis.generation.modes import GenerationMode


def _positive_meta() -> CaseMetadata:
    return CaseMetadata(
        generation=GenerationInfo(time=0.0, mode=GenerationMode.POSITIVE),
        components={},
        phase=PhaseInfo(
            name=TestPhase.FUZZING,
            data=FuzzingPhaseData(description=None, parameter=None, parameter_location=None, location=None),
        ),
    )


def _negative_meta() -> CaseMetadata:
    return CaseMetadata(
        generation=GenerationInfo(time=0.0, mode=GenerationMode.NEGATIVE),
        components={},
        phase=PhaseInfo(
            name=TestPhase.FUZZING,
            data=FuzzingPhaseData(description=None, parameter=None, parameter_location=None, location=None),
        ),
    )


@pytest.mark.parametrize("status_code", [200, 201, 302, 401, 403, 500, 503])
def test_parse_observations_returns_empty_for_non_4xx(status_code, response_factory, case_factory):
    case = case_factory(_meta=_positive_meta())
    response = response_factory.requests(status_code=status_code)
    assert parse_observations(case.operation, case, response) == ()


def test_parse_observations_returns_empty_for_negative_mode(response_factory, case_factory):
    case = case_factory(_meta=_negative_meta())
    response = response_factory.requests(status_code=400)
    assert parse_observations(case.operation, case, response) == ()


def test_parse_observations_returns_empty_for_unparsable_4xx_body(response_factory, case_factory):
    case = case_factory(_meta=_positive_meta())
    response = Response.from_any(
        response_factory.requests(
            status_code=400,
            content=b"not a parseable error body",
            content_type="text/plain",
        )
    )
    assert parse_observations(case.operation, case, response) == ()
