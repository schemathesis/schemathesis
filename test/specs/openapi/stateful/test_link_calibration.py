import pytest

from schemathesis.core.error_feedback.store import Observation, ObservationKind
from schemathesis.core.parameters import ParameterLocation
from schemathesis.engine.link_calibration import (
    DEFAULT_USE_PROBABILITY,
    MIN_PROBABILITY,
    MIN_SAMPLES,
    LinkCalibrationState,
    TransitionScore,
)
from schemathesis.engine.recorder import ScenarioRecorder
from schemathesis.generation.meta import (
    CaseMetadata,
    FuzzingPhaseData,
    GenerationInfo,
    PhaseInfo,
    TestPhase,
)
from schemathesis.generation.modes import GenerationMode
from schemathesis.generation.stateful.state_machine import StepInput, Transition
from schemathesis.specs.openapi.stateful.link_calibration import record_link_outcome
from test.apps.catalog.openapi.modifiers.stateful import (
    EnsureResourceAvailability,
    ParserBlamesUnrelated,
    SingleLink,
    WrongLinkParserAttributed,
    WrongLinkToMissingId,
    WrongLinkTypeMismatch,
)
from test.apps.runtime import Modifier


def _meta(mode: GenerationMode) -> CaseMetadata:
    return CaseMetadata(
        generation=GenerationInfo(time=0.0, mode=mode),
        components={},
        phase=PhaseInfo(
            name=TestPhase.STATEFUL,
            data=FuzzingPhaseData(description=None, parameter=None, parameter_location=None, location=None),
        ),
    )


def _make_transition(*, transition_id: str = "t1", with_parameters: bool = False) -> Transition:
    parameters = {"path": {"userId": object()}} if with_parameters else {}
    return Transition(id=transition_id, parent_id="parent", is_inferred=False, parameters=parameters, request_body=None)


def _step_input(case, *, has_transition: bool = True, applied: bool = True) -> StepInput:
    transition = _make_transition() if has_transition else None
    applied_parameters: list[tuple[ParameterLocation, str | None]] = (
        [(ParameterLocation.PATH, "userId")] if applied else []
    )
    return StepInput(case=case, transition=transition, applied_parameters=applied_parameters)


def _observation(
    *,
    location: ParameterLocation,
    parameter_path: tuple[str, ...],
    kind: ObservationKind = ObservationKind.FORMAT,
) -> Observation:
    return Observation(
        operation_label="GET /users/{userId}",
        location=location,
        parameter_path=parameter_path,
        kind=kind,
        raw_message="",
    )


@pytest.fixture
def positive_case(case_factory):
    return case_factory(_meta=_meta(GenerationMode.POSITIVE))


@pytest.fixture
def negative_case(case_factory):
    return case_factory(_meta=_meta(GenerationMode.NEGATIVE))


@pytest.fixture
def recorder():
    return ScenarioRecorder(label="test")


@pytest.mark.parametrize(
    "successes,failures,expected",
    [
        pytest.param(MIN_SAMPLES - 1, 0, DEFAULT_USE_PROBABILITY, id="below-min-samples-keeps-default"),
        pytest.param(MIN_SAMPLES, 0, 1.0, id="all-success"),
        pytest.param(0, MIN_SAMPLES, MIN_PROBABILITY, id="all-failure-hits-floor"),
        pytest.param(0, 1000, MIN_PROBABILITY, id="floor-holds-at-large-counts"),
        pytest.param(8, 2, 0.8, id="mixed-empirical-ratio"),
    ],
)
def test_transition_score_use_probability(successes, failures, expected):
    assert TransitionScore(successes=successes, failures=failures).use_probability == pytest.approx(expected)


def test_transition_score_merge():
    score = TransitionScore(successes=3, failures=1)
    score.merge(TransitionScore(successes=2, failures=4))
    assert score == TransitionScore(successes=5, failures=5)


def test_calibration_state_begin_iteration_merges_write_to_read():
    state = LinkCalibrationState()
    state.record("t1", success=True)
    state.record("t1", success=True)
    state.record("t1", success=False)
    state.begin_iteration()
    assert state.read["t1"] == TransitionScore(successes=2, failures=1)


def test_calibration_state_begin_iteration_clears_write():
    state = LinkCalibrationState()
    state.record("t1", success=True)
    state.begin_iteration()
    assert "t1" not in state.write


def test_calibration_state_begin_iteration_accumulates_across_iterations():
    state = LinkCalibrationState()
    state.record("t1", success=True)
    state.begin_iteration()
    state.record("t1", success=False)
    state.begin_iteration()
    assert state.read["t1"] == TransitionScore(successes=1, failures=1)


def test_calibration_state_use_probability_unknown_transition():
    assert LinkCalibrationState().use_probability("unknown") == DEFAULT_USE_PROBABILITY


def test_calibration_state_use_probability_after_data():
    state = LinkCalibrationState()
    for _ in range(MIN_SAMPLES):
        state.record("t1", success=True)
    state.begin_iteration()
    assert state.use_probability("t1") == pytest.approx(1.0)


def test_calibration_read_state_frozen_during_run():
    state = LinkCalibrationState()
    state.record("t1", success=True)
    state.begin_iteration()
    snapshot = state.read["t1"]
    state.record("t1", success=False)
    state.record("t1", success=False)
    assert state.read["t1"] == snapshot
    assert state.write["t1"] == TransitionScore(successes=0, failures=2)


@pytest.mark.parametrize(
    "status_code,expected_recorded",
    [
        (200, True),
        (201, True),
        (302, True),
        (400, True),
        (404, False),
        (409, False),
        (422, True),
        (401, False),
        (403, False),
        (500, False),
        (503, False),
    ],
)
def test_record_link_outcome_status_codes(status_code, expected_recorded, positive_case, response_factory, recorder):
    state = LinkCalibrationState()
    record_link_outcome(
        state, response_factory.requests(status_code=status_code), (), _step_input(positive_case), recorder
    )
    assert bool(state.write) == expected_recorded


def test_record_link_outcome_no_transition(positive_case, response_factory, recorder):
    state = LinkCalibrationState()
    record_link_outcome(
        state,
        response_factory.requests(status_code=200),
        (),
        _step_input(positive_case, has_transition=False),
        recorder,
    )
    assert not state.write


def test_record_link_outcome_not_applied(positive_case, response_factory, recorder):
    state = LinkCalibrationState()
    inp = StepInput(case=positive_case, transition=_make_transition(with_parameters=True), applied_parameters=[])
    record_link_outcome(state, response_factory.requests(status_code=200), (), inp, recorder)
    assert not state.write


def test_record_link_outcome_negative_generation(negative_case, response_factory, recorder):
    state = LinkCalibrationState()
    record_link_outcome(state, response_factory.requests(status_code=400), (), _step_input(negative_case), recorder)
    assert not state.write


@pytest.mark.parametrize(
    "status_code,expected",
    [
        (200, TransitionScore(successes=1, failures=0)),
        (400, TransitionScore(successes=0, failures=1)),
    ],
)
def test_record_link_outcome_records_success_or_failure(
    status_code, expected, positive_case, response_factory, recorder
):
    state = LinkCalibrationState()
    record_link_outcome(
        state, response_factory.requests(status_code=status_code), (), _step_input(positive_case), recorder
    )
    assert state.write["t1"] == expected


_PATH_USERID = _observation(location=ParameterLocation.PATH, parameter_path=("userId",))
_HEADER_TENANT = _observation(
    location=ParameterLocation.HEADER,
    parameter_path=("X-Tenant-Id",),
    kind=ObservationKind.MUST_NOT_BE_BLANK,
)


@pytest.mark.parametrize(
    "status_code,observations,applied,expected_recorded",
    [
        pytest.param(400, (), [(ParameterLocation.PATH, "userId")], True, id="400-no-parser-status-fallback"),
        pytest.param(
            400, (_HEADER_TENANT,), [(ParameterLocation.PATH, "userId")], False, id="400-parser-mismatch-drops"
        ),
        pytest.param(
            422, (_PATH_USERID,), [(ParameterLocation.PATH, "userId")], True, id="422-parser-attributed-records"
        ),
        pytest.param(
            422, (_HEADER_TENANT,), [(ParameterLocation.PATH, "userId")], False, id="422-parser-mismatch-drops"
        ),
        pytest.param(
            400, (_PATH_USERID,), [(ParameterLocation.BODY, None)], False, id="bare-body-vs-path-observation-drops"
        ),
        pytest.param(
            400,
            (_observation(location=ParameterLocation.BODY, parameter_path=("anything",)),),
            [(ParameterLocation.BODY, None)],
            True,
            id="bare-body-matches-any-body-observation",
        ),
    ],
)
def test_record_link_outcome_attribution(
    status_code, observations, applied, expected_recorded, positive_case, response_factory, recorder
):
    state = LinkCalibrationState()
    step_input = StepInput(case=positive_case, transition=_make_transition(), applied_parameters=applied)
    record_link_outcome(state, response_factory.requests(status_code=status_code), observations, step_input, recorder)
    assert bool(state.write) is expected_recorded


@pytest.mark.parametrize(
    "scenario_modifier,expected_score_drops",
    [
        pytest.param(EnsureResourceAvailability(), False, id="correct-link-flaky-resource-stays-default"),
        pytest.param(WrongLinkToMissingId(), False, id="wrong-link-404-blind-spot"),
        pytest.param(WrongLinkTypeMismatch(), True, id="wrong-link-status-fallback-drops"),
        pytest.param(WrongLinkParserAttributed(), True, id="wrong-link-parser-attributed-drops"),
        pytest.param(ParserBlamesUnrelated(), False, id="parser-mismatch-stays-default"),
    ],
)
def test_calibration_attribution_e2e(
    calibration_engine_factory, scenario_modifier: Modifier, expected_score_drops: bool
):
    observer = calibration_engine_factory(scenario_modifier, SingleLink(), max_examples=50)
    observer.begin_iteration()
    name = type(scenario_modifier).__name__
    if expected_score_drops:
        assert observer.read, f"{name}: link expected to be penalized but no failures recorded"
        score = next(iter(observer.read.values()))
        assert score.successes + score.failures >= MIN_SAMPLES, f"{name}: insufficient samples ({score!r})"
        assert score.use_probability < DEFAULT_USE_PROBABILITY, f"{name}: probability not dropped ({score!r})"
    else:
        assert observer.target_request_count >= MIN_SAMPLES, (
            f"{name}: link target hit only {observer.target_request_count} times — engine did not exercise the link"
        )
        for transition_id, score in observer.read.items():
            assert score.failures == 0, f"{transition_id!r} ({name}): {score.failures} unattributed failures"
