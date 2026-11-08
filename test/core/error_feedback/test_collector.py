from __future__ import annotations

import pytest

from schemathesis.core.error_feedback import ErrorFeedbackStore
from schemathesis.core.error_feedback.collector import parse_observations, record_response
from schemathesis.core.parameters import ParameterLocation
from schemathesis.core.transport import Response
from schemathesis.generation.meta import (
    CaseMetadata,
    FuzzingPhaseData,
    GenerationInfo,
    PhaseInfo,
    TestPhase,
)
from schemathesis.generation.modes import GenerationMode
from test.core.error_feedback.helpers import meta_for
from test.core.error_feedback.parsers.helpers import SPRING_MESSAGES


def _positive_meta() -> CaseMetadata:
    return CaseMetadata(
        generation=GenerationInfo(time=0.0, mode=GenerationMode.POSITIVE),
        components={},
        phase=PhaseInfo(
            name=TestPhase.FUZZING,
            data=FuzzingPhaseData(description=None, parameter=None, parameter_location=None, location=None),
        ),
    )


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


def test_parse_observations_returns_empty_for_malformed_content_type(response_factory, case_factory):
    case = case_factory(_meta=_positive_meta())
    response = Response.from_any(response_factory.requests(status_code=400, content=b"{}", content_type="a"))
    assert parse_observations(case.operation, case, response) == ()


def _drive_collector(
    *,
    case_factory,
    response_factory,
    status_code: int = 400,
    body: bytes = SPRING_MESSAGES,
    mode: GenerationMode = GenerationMode.POSITIVE,
    times: int = 1,
):
    case = case_factory(_meta=meta_for(mode))
    response = Response.from_any(
        response_factory.requests(
            content=body,
            content_type="application/json",
            status_code=status_code,
        )
    )
    store = ErrorFeedbackStore()
    for _ in range(times):
        record_response(store=store, operation=case.operation, case=case, response=response)
    return store, case


def test_collector_records_observations_from_400(case_factory, response_factory):
    store, case = _drive_collector(
        case_factory=case_factory,
        response_factory=response_factory,
        status_code=400,
    )
    out = store.observations(operation_label=case.operation.label, location=ParameterLocation.BODY)
    assert sorted(o.parameter_path for o in out) == [("city",), ("zipcode",)]


@pytest.mark.parametrize("status_code", [200, 201, 302, 401, 403, 500, 503])
def test_collector_skips_non_4xx_and_auth_failures(status_code, case_factory, response_factory):
    store, case = _drive_collector(
        case_factory=case_factory,
        response_factory=response_factory,
        status_code=status_code,
    )
    out = store.observations(
        operation_label=case.operation.label,
        location=ParameterLocation.BODY,
    )
    assert out == ()


def test_collector_skips_negative_mode_cases(case_factory, response_factory):
    store, case = _drive_collector(
        case_factory=case_factory,
        response_factory=response_factory,
        status_code=400,
        mode=GenerationMode.NEGATIVE,
    )
    out = store.observations(
        operation_label=case.operation.label,
        location=ParameterLocation.BODY,
    )
    assert out == ()
