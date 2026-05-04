import pytest

from schemathesis.core.mutations import OperatorKind
from schemathesis.core.parameters import ParameterLocation
from schemathesis.generation import GenerationMode
from schemathesis.generation.meta import (
    CaseMetadata,
    ComponentInfo,
    CoveragePhaseData,
    CoverageScenario,
    FuzzingPhaseData,
    GenerationInfo,
    PhaseInfo,
    TestPhase,
)
from schemathesis.specs.openapi.negative.mutations import Mutation, MutationChannel
from schemathesis.transport.prepare import get_exclude_headers


def _build_meta(phase_data, *, location=ParameterLocation.HEADER, mode=GenerationMode.NEGATIVE):
    phase = TestPhase.COVERAGE if isinstance(phase_data, CoveragePhaseData) else TestPhase.FUZZING
    return CaseMetadata(
        generation=GenerationInfo(time=0.0, mode=mode),
        components={location: ComponentInfo(mode=mode)},
        phase=PhaseInfo(name=phase, data=phase_data),
    )


def test_negate_required_with_multiple_headers_excludes_omitted(case_factory):
    # `len(required) > 1` sets parameter=None; the original list is needed to know which header was dropped.
    mutation = Mutation(
        path=(),
        schema_pointer="",
        channel=MutationChannel.SCHEMA,
        operator=OperatorKind.NEGATE_CONSTRAINTS,
        keywords=("required",),
        parameter=None,
        original_value=["Authorization", "X-Custom"],
        new_value=None,
    )
    meta = CaseMetadata(
        generation=GenerationInfo(time=0.0, mode=GenerationMode.NEGATIVE),
        components={ParameterLocation.HEADER: ComponentInfo(mode=GenerationMode.NEGATIVE)},
        phase=PhaseInfo(
            name=TestPhase.FUZZING,
            data=FuzzingPhaseData(
                description=None,
                parameter=None,
                parameter_location=ParameterLocation.HEADER,
                location="",
                mutations=(mutation,),
            ),
        ),
    )
    case = case_factory(headers={"X-Custom": "x"}, _meta=meta)
    assert get_exclude_headers(case) == ["Authorization"]


def test_nested_header_mutation_does_not_drop_unrelated_headers(case_factory):
    # A nested property named "Authorization" must not shadow the real top-level header.
    mutation = Mutation(
        path=("X-Token",),
        schema_pointer="/properties/X-Token/properties/Authorization",
        channel=MutationChannel.SCHEMA,
        operator=OperatorKind.REMOVE_REQUIRED_PROPERTY,
        keywords=("required",),
        parameter="Authorization",
        original_value=None,
        new_value=None,
    )
    meta = CaseMetadata(
        generation=GenerationInfo(time=0.0, mode=GenerationMode.NEGATIVE),
        components={ParameterLocation.HEADER: ComponentInfo(mode=GenerationMode.NEGATIVE)},
        phase=PhaseInfo(
            name=TestPhase.FUZZING,
            data=FuzzingPhaseData(
                description=None,
                parameter="Authorization",
                parameter_location=ParameterLocation.HEADER,
                location="/properties/X-Token/properties/Authorization",
                mutations=(mutation,),
            ),
        ),
    )
    case = case_factory(headers={"X-Token": '{"value": "x"}', "Authorization": "Bearer real"}, _meta=meta)
    assert get_exclude_headers(case) == []


def test_no_meta_returns_empty(case_factory):
    assert get_exclude_headers(case_factory(_meta=None)) == []


def test_positive_mode_returns_empty(case_factory):
    # Positive cases must keep all default/session headers.
    meta = _build_meta(
        FuzzingPhaseData(
            description=None,
            parameter="Authorization",
            parameter_location=ParameterLocation.HEADER,
            location="",
            mutations=(),
        ),
        mode=GenerationMode.POSITIVE,
    )
    assert get_exclude_headers(case_factory(_meta=meta)) == []


def test_non_header_location_returns_empty(case_factory):
    meta = _build_meta(
        FuzzingPhaseData(
            description=None,
            parameter="page",
            parameter_location=ParameterLocation.QUERY,
            location="/properties/page",
            mutations=(),
        ),
        location=ParameterLocation.QUERY,
    )
    assert get_exclude_headers(case_factory(_meta=meta)) == []


@pytest.mark.parametrize(
    ("parameter", "expected"),
    [("X-Token", ["X-Token"]), (None, [])],
    ids=["named", "unnamed"],
)
def test_coverage_missing_parameter(case_factory, parameter, expected):
    meta = _build_meta(
        CoveragePhaseData(
            scenario=CoverageScenario.MISSING_PARAMETER,
            description="missing",
            location="",
            parameter=parameter,
            parameter_location=ParameterLocation.HEADER,
        ),
    )
    assert get_exclude_headers(case_factory(_meta=meta)) == expected


def test_negate_constraints_without_required_keyword_excludes_nothing(case_factory):
    # Pattern negation sends a violating value — the header must not be stripped.
    mutation = Mutation(
        path=(),
        schema_pointer="",
        channel=MutationChannel.SCHEMA,
        operator=OperatorKind.NEGATE_CONSTRAINTS,
        keywords=("pattern",),
        parameter="X-Token",
        original_value=None,
        new_value=None,
    )
    meta = _build_meta(
        FuzzingPhaseData(
            description=None,
            parameter="X-Token",
            parameter_location=ParameterLocation.HEADER,
            location="",
            mutations=(mutation,),
        ),
    )
    assert get_exclude_headers(case_factory(headers={"X-Token": "bad"}, _meta=meta)) == []


def test_remove_required_property_excludes_named_header(case_factory):
    mutation = Mutation(
        path=(),
        schema_pointer="/properties/X-Token",
        channel=MutationChannel.SCHEMA,
        operator=OperatorKind.REMOVE_REQUIRED_PROPERTY,
        keywords=("required",),
        parameter="X-Token",
        original_value=None,
        new_value=None,
    )
    meta = _build_meta(
        FuzzingPhaseData(
            description=None,
            parameter="X-Token",
            parameter_location=ParameterLocation.HEADER,
            location="/properties/X-Token",
            mutations=(mutation,),
        ),
    )
    assert get_exclude_headers(case_factory(headers={}, _meta=meta)) == ["X-Token"]
