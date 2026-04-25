import pytest

from schemathesis.engine.pruning import (
    DEFAULT_USE_PROBABILITY,
    MIN_PROBABILITY,
    MIN_SAMPLES,
    PruningState,
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
from schemathesis.specs.openapi.stateful.pruning import record_pruning_observation
from test.specs.openapi.stateful.conftest import PruningObserver


def _make_meta(*, is_negative: bool = False) -> CaseMetadata:
    mode = GenerationMode.NEGATIVE if is_negative else GenerationMode.POSITIVE
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


def _make_input(case, *, has_transition: bool = True, applied: bool = True) -> StepInput:
    transition = _make_transition() if has_transition else None
    applied_parameters = ["userId"] if applied else []
    return StepInput(case=case, transition=transition, applied_parameters=applied_parameters)


@pytest.fixture
def positive_case(case_factory):
    return case_factory(_meta=_make_meta(is_negative=False))


@pytest.fixture
def negative_case(case_factory):
    return case_factory(_meta=_make_meta(is_negative=True))


@pytest.fixture
def recorder():
    return ScenarioRecorder(label="test")


def test_transition_score_below_min_samples():
    score = TransitionScore()
    for _ in range(MIN_SAMPLES - 1):
        score.successes += 1
    assert score.use_probability == DEFAULT_USE_PROBABILITY


def test_transition_score_all_success():
    score = TransitionScore(successes=MIN_SAMPLES, failures=0)
    assert score.use_probability == pytest.approx(1.0)


def test_transition_score_all_failure():
    score = TransitionScore(successes=0, failures=MIN_SAMPLES)
    assert score.use_probability == MIN_PROBABILITY


def test_transition_score_all_failure_large_count():
    # MIN_PROBABILITY floor must hold regardless of how many failures accumulate
    score = TransitionScore(successes=0, failures=1000)
    assert score.use_probability == MIN_PROBABILITY


def test_transition_score_mixed():
    score = TransitionScore(successes=8, failures=2)
    assert score.use_probability == pytest.approx(0.8)


def test_transition_score_merge():
    a = TransitionScore(successes=3, failures=1)
    b = TransitionScore(successes=2, failures=4)
    a.merge(b)
    assert a.successes == 5
    assert a.failures == 5


def test_pruning_state_begin_iteration_merges_write_to_read():
    state = PruningState()
    state.record("t1", success=True)
    state.record("t1", success=True)
    state.record("t1", success=False)
    state.begin_iteration()
    score = state.read["t1"]
    assert score.successes == 2
    assert score.failures == 1


def test_pruning_state_begin_iteration_clears_write():
    state = PruningState()
    state.record("t1", success=True)
    state.begin_iteration()
    assert "t1" not in state.write


def test_pruning_state_begin_iteration_accumulates_across_iterations():
    state = PruningState()
    state.record("t1", success=True)
    state.begin_iteration()
    state.record("t1", success=False)
    state.begin_iteration()
    score = state.read["t1"]
    assert score.successes == 1
    assert score.failures == 1


def test_pruning_state_use_probability_unknown_transition():
    state = PruningState()
    assert state.use_probability("unknown") == DEFAULT_USE_PROBABILITY


def test_pruning_state_use_probability_after_data():
    state = PruningState()
    for _ in range(MIN_SAMPLES):
        state.record("t1", success=True)
    state.begin_iteration()
    assert state.use_probability("t1") == pytest.approx(1.0)


def test_pruning_read_state_frozen_during_run():
    state = PruningState()
    state.record("t1", success=True)
    state.begin_iteration()
    read_before = {k: (v.successes, v.failures) for k, v in state.read.items()}

    state.record("t1", success=False)
    state.record("t1", success=False)

    read_after = {k: (v.successes, v.failures) for k, v in state.read.items()}
    assert read_before == read_after
    assert state.write["t1"].failures == 2


def test_pruning_observer_snapshots_after_merge():
    observer = PruningObserver()

    # Before any data: first begin_iteration snapshots empty read
    observer.begin_iteration()
    assert observer.snapshots[0] == {}

    # Record some failures in write
    observer.record("t1", success=False)
    observer.record("t1", success=False)

    # Second begin_iteration merges write->read, snapshots the merged state
    observer.begin_iteration()
    assert "t1" in observer.snapshots[1]
    assert observer.snapshots[1]["t1"].failures == 2
    assert observer.snapshots[1]["t1"].successes == 0


@pytest.mark.parametrize(
    "status_code,expected_recorded",
    [
        (200, True),
        (201, True),
        (302, True),
        (400, True),
        (404, True),
        (422, True),
        (401, False),
        (403, False),
        (500, False),
        (503, False),
    ],
)
def test_record_pruning_observation_status_codes(
    status_code, expected_recorded, positive_case, response_factory, recorder
):
    state = PruningState()
    record_pruning_observation(
        state, response_factory.requests(status_code=status_code), _make_input(positive_case), recorder
    )
    assert bool(state.write) == expected_recorded


def test_record_pruning_observation_no_transition(positive_case, response_factory, recorder):
    state = PruningState()
    record_pruning_observation(
        state, response_factory.requests(status_code=200), _make_input(positive_case, has_transition=False), recorder
    )
    assert not state.write


def test_record_pruning_observation_not_applied(positive_case, response_factory, recorder):
    state = PruningState()
    inp = StepInput(case=positive_case, transition=_make_transition(with_parameters=True), applied_parameters=[])
    record_pruning_observation(state, response_factory.requests(status_code=200), inp, recorder)
    assert not state.write


def test_record_pruning_observation_negative_generation(negative_case, response_factory, recorder):
    state = PruningState()
    record_pruning_observation(state, response_factory.requests(status_code=400), _make_input(negative_case), recorder)
    assert not state.write


@pytest.mark.parametrize(
    "status_code,expected_successes,expected_failures",
    [
        (200, 1, 0),
        (404, 0, 1),
    ],
)
def test_record_pruning_observation_outcome(
    status_code, expected_successes, expected_failures, positive_case, response_factory, recorder
):
    state = PruningState()
    record_pruning_observation(
        state, response_factory.requests(status_code=status_code), _make_input(positive_case), recorder
    )
    assert state.write["t1"].successes == expected_successes
    assert state.write["t1"].failures == expected_failures


def test_pruning_accumulates_failures_for_bad_link(pruning_engine_factory):
    # ensure_resource_availability=True -> POST /users does NOT save user -> GET /users/{userId} -> 404
    observer = pruning_engine_factory(app_kwargs={"ensure_resource_availability": True})

    # begin_iteration() is called at the TOP of each engine loop iteration (before the run),
    # so after the loop exits, write still holds observations from the last completed run.
    assert observer.write, "No observations recorded — record_pruning_observation was never called"
    assert any(score.failures > 0 for score in observer.write.values()), (
        "No failures recorded — 404 responses from the bad link were not captured"
    )


def test_pruning_probability_drops_after_observing_bad_link(pruning_engine_factory):
    observer = pruning_engine_factory(app_kwargs={"ensure_resource_availability": True})

    # Manually promote write -> read as the next loop iteration would do.
    observer.begin_iteration()

    # The last snapshot (captured after the manual merge) shows what the next iteration would read.
    last = observer.snapshots[-1]
    assert last, "read state is empty after promotion — failures were not accumulated in write"

    # For every transition that has enough data, probability must have dropped.
    evaluated = [(tid, score) for tid, score in last.items() if score.failures + score.successes >= MIN_SAMPLES]
    assert evaluated, (
        f"No transition accumulated >= {MIN_SAMPLES} samples; "
        f"increase max_examples or the test proves nothing about use_probability. "
        f"Transitions found: { {tid: (s.successes, s.failures) for tid, s in last.items()} }"
    )
    # Verify we actually observed failures from the bad link, not just successes.
    # A purely successful transition would have use_probability >= DEFAULT_USE_PROBABILITY,
    # making the loop below vacuously pass without testing the pruning feature.
    assert any(score.failures > 0 for _, score in evaluated), (
        f"All evaluated transitions have zero failures — the bad link did not produce 404s. "
        f"Transitions: { {tid: (s.successes, s.failures) for tid, s in last.items()} }"
    )
    for transition_id, score in evaluated:
        assert score.use_probability < DEFAULT_USE_PROBABILITY, (
            f"Transition {transition_id!r} has {score.failures} failures / "
            f"{score.successes} successes but use_probability={score.use_probability} "
            f"is not below DEFAULT={DEFAULT_USE_PROBABILITY}"
        )
