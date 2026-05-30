from __future__ import annotations

import pytest

from schemathesis.engine.health import (
    DEFAULT_USE_PROBABILITY,
    MIN_USE_PROBABILITY,
    PHASE_FATAL_DISTINCT_OPERATIONS,
    PHASE_FATAL_WINDOW_SECONDS,
    TIGHTENED_TIMEOUT_SECONDS,
    HealthState,
    OperationHealth,
)


@pytest.mark.parametrize(
    ("completed", "transport_failures", "expected"),
    [
        (1, 1, DEFAULT_USE_PROBABILITY),
        (2, 1, pytest.approx(2 / 3)),
        (8, 2, pytest.approx(0.8)),
        (0, 10, MIN_USE_PROBABILITY),
    ],
    ids=["below-min-samples", "at-min-samples", "above-min-samples", "floor"],
)
def test_use_probability(completed, transport_failures, expected):
    assert OperationHealth(completed=completed, transport_failures=transport_failures).use_probability == expected


def test_timeout_override_none_for_unknown_operation():
    state = HealthState()
    assert state.timeout_override("never-seen") is None


@pytest.mark.parametrize(
    ("failure_count", "expected"),
    [(1, None), (2, TIGHTENED_TIMEOUT_SECONDS)],
    ids=["below-threshold", "at-threshold"],
)
def test_timeout_override_threshold(failure_count, expected):
    state = HealthState()
    for index in range(failure_count):
        state.record_transport_failure(operation_label="op", now=10.0 + index)
    assert state.timeout_override("op") == expected


def test_timeout_override_clears_after_completion():
    state = HealthState()
    state.record_transport_failure(operation_label="op", now=10.0)
    state.record_transport_failure(operation_label="op", now=11.0)
    assert state.timeout_override("op") == TIGHTENED_TIMEOUT_SECONDS
    state.record_completion(operation_label="op")
    assert state.timeout_override("op") is None


def test_abort_reason_none_when_no_failures():
    state = HealthState()
    assert state.abort_reason(now=10.0) is None


def test_abort_reason_below_distinct_operations_threshold():
    state = HealthState()
    for index in range(PHASE_FATAL_DISTINCT_OPERATIONS - 1):
        state.record_transport_failure(operation_label=f"op{index}", now=10.0)
    assert state.abort_reason(now=10.0) is None


def test_abort_reason_fires_when_threshold_crossed_within_window():
    state = HealthState()
    labels = [f"op{index}" for index in range(PHASE_FATAL_DISTINCT_OPERATIONS)]
    for label in labels:
        state.record_transport_failure(operation_label=label, now=10.0)
    reason = state.abort_reason(now=10.0)
    assert reason is not None
    for label in labels:
        assert label in reason
    assert f"{PHASE_FATAL_DISTINCT_OPERATIONS} operations" in reason


def test_abort_reason_resets_when_window_expires():
    state = HealthState()
    for index in range(PHASE_FATAL_DISTINCT_OPERATIONS):
        state.record_transport_failure(operation_label=f"op{index}", now=10.0)
    assert state.abort_reason(now=10.0 + PHASE_FATAL_WINDOW_SECONDS + 1) is None


def test_abort_reason_resets_after_completion():
    state = HealthState()
    labels = [f"op{index}" for index in range(PHASE_FATAL_DISTINCT_OPERATIONS)]
    for label in labels:
        state.record_transport_failure(operation_label=label, now=10.0)
    assert state.abort_reason(now=10.0) is not None
    state.record_completion(operation_label=labels[0])
    assert state.abort_reason(now=10.0) is None


def test_abort_reason_window_boundary_is_strict():
    state = HealthState()
    for index in range(PHASE_FATAL_DISTINCT_OPERATIONS):
        state.record_transport_failure(operation_label=f"op{index}", now=10.0)
    # Just inside the window: still fires.
    assert state.abort_reason(now=10.0 + PHASE_FATAL_WINDOW_SECONDS - 0.001) is not None
    # Exactly at the boundary: does not fire (strict less-than).
    assert state.abort_reason(now=10.0 + PHASE_FATAL_WINDOW_SECONDS) is None


def test_abort_reason_message_format():
    state = HealthState()
    state.record_transport_failure(operation_label="POST /a", now=10.0)
    state.record_transport_failure(operation_label="POST /b", now=12.0)
    state.record_transport_failure(operation_label="POST /c", now=15.0)
    reason = state.abort_reason(now=15.5)
    assert reason == (
        "API appears unhealthy: 3 operations had transport failures within the last 30s\n"
        "  - POST /a (last failure 5.5s ago)\n"
        "  - POST /b (last failure 3.5s ago)\n"
        "  - POST /c (last failure 0.5s ago)"
    )


def test_frozen_use_probability_defaults_until_snapshot():
    state = HealthState()
    for index in range(10):
        state.record_transport_failure(operation_label="op", now=float(index))
    # Live counters reflect the failures, but generation reads the empty snapshot until a boundary.
    assert state.operations["op"].use_probability == MIN_USE_PROBABILITY
    assert state.frozen_use_probability("op") == DEFAULT_USE_PROBABILITY


def test_frozen_use_probability_stable_until_next_begin_iteration():
    state = HealthState()
    for _ in range(8):
        state.record_completion(operation_label="op")
    for index in range(2):
        state.record_transport_failure(operation_label="op", now=float(index))
    state.begin_iteration()
    assert state.frozen_use_probability("op") == pytest.approx(0.8)
    # Live updates within a run must not shift the frozen constraint (keeps generation deterministic).
    for index in range(50):
        state.record_transport_failure(operation_label="op", now=100.0 + index)
    assert state.frozen_use_probability("op") == pytest.approx(0.8)
    # Refreshed only at the next boundary (now reflects the 50 extra failures: 8 / 60).
    state.begin_iteration()
    assert state.frozen_use_probability("op") == pytest.approx(8 / 60)
