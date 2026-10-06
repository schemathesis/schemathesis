from __future__ import annotations

import pytest

from schemathesis.engine.health import (
    ADAPTIVE_TIMEOUT_MULTIPLIER,
    DEFAULT_USE_PROBABILITY,
    MIN_USE_PROBABILITY,
    PHASE_FATAL_FAILURES,
    PHASE_FATAL_WINDOW_SECONDS,
    TIGHTENED_TIMEOUT_SECONDS,
    UNRESPONSIVE_MIN_FAILURES,
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


# A fast answer does not mean the inputs that hung stopped hanging.
def test_timeout_override_stays_after_completion():
    state = HealthState()
    state.record_transport_failure(operation_label="op", now=10.0)
    state.record_transport_failure(operation_label="op", now=11.0)
    state.record_completion(operation_label="op", now=12.0, elapsed=0.01)
    assert state.timeout_override("op") == TIGHTENED_TIMEOUT_SECONDS


def test_timeout_override_scales_with_slowest_completion():
    state = HealthState()
    state.record_completion(operation_label="op", now=1.0, elapsed=0.2)
    state.record_completion(operation_label="op", now=2.0, elapsed=0.6)
    state.record_transport_failure(operation_label="op", now=3.0)
    state.record_transport_failure(operation_label="op", now=4.0)
    assert state.timeout_override("op") == pytest.approx(0.6 * ADAPTIVE_TIMEOUT_MULTIPLIER)


# One transient hang must not shorten the timeout for the rest of the run.
def test_timeout_override_needs_repeated_failures():
    state = HealthState()
    state.record_completion(operation_label="op", now=1.0, elapsed=0.01)
    state.record_transport_failure(operation_label="op", now=2.0)
    assert state.timeout_override("op") is None
    state.record_completion(operation_label="op", now=3.0, elapsed=0.01)
    state.record_transport_failure(operation_label="op", now=4.0)
    assert state.timeout_override("op") == TIGHTENED_TIMEOUT_SECONDS


def test_abort_reason_none_when_no_failures():
    state = HealthState()
    assert state.abort_reason(now=10.0) is None


def test_abort_reason_none_below_failure_threshold():
    state = HealthState()
    state.record_completion(operation_label="healthy", now=0.0)
    for index in range(PHASE_FATAL_FAILURES - 1):
        state.record_transport_failure(operation_label=f"op{index}", now=40.0 + index)
    assert state.abort_reason(now=42.0) is None


@pytest.mark.parametrize("operations", [1, 2, 3], ids=["one-operation", "two-operations", "three-operations"])
def test_abort_reason_fires_after_silence(operations):
    state = HealthState()
    state.record_completion(operation_label="healthy", now=0.0)
    for index in range(PHASE_FATAL_FAILURES):
        state.record_transport_failure(operation_label=f"op{index % operations}", now=40.0 + index)
    assert state.abort_reason(now=42.0) is not None


def test_abort_reason_none_while_other_operations_answer():
    state = HealthState()
    state.record_completion(operation_label="healthy", now=35.0)
    for index in range(PHASE_FATAL_FAILURES):
        state.record_transport_failure(operation_label=f"op{index}", now=40.0 + index)
    assert state.abort_reason(now=42.0) is None


# A slow cold start must not abort before the API had a full window to answer.
def test_abort_reason_waits_a_full_window_when_nothing_answered_yet():
    state = HealthState()
    for index in range(PHASE_FATAL_FAILURES):
        state.record_transport_failure(operation_label="POST /users", now=10.0 + index)
    assert state.abort_reason(now=12.0) is None
    for index in range(PHASE_FATAL_FAILURES):
        state.record_transport_failure(operation_label="POST /users", now=40.0 + index)
    assert state.abort_reason(now=42.0) is not None


def test_abort_reason_ignores_failures_older_than_window():
    state = HealthState()
    state.record_completion(operation_label="healthy", now=0.0)
    state.record_transport_failure(operation_label="op", now=40.0)
    state.record_transport_failure(operation_label="op", now=41.0)
    state.record_transport_failure(operation_label="op", now=40.0 + PHASE_FATAL_WINDOW_SECONDS)
    assert state.abort_reason(now=40.0 + PHASE_FATAL_WINDOW_SECONDS) is None


def test_abort_reason_message_format():
    state = HealthState()
    state.record_completion(operation_label="healthy", now=0.0)
    state.record_transport_failure(operation_label="POST /a", now=40.0)
    state.record_transport_failure(operation_label="POST /b", now=42.0)
    state.record_transport_failure(operation_label="POST /a", now=45.0)
    assert state.abort_reason(now=45.5) == (
        "API stopped responding: no answers for 45.5s, 3 requests failed in the last 30s\n"
        "  - POST /a (2 failed, last 0.5s ago)\n"
        "  - POST /b (1 failed, last 3.5s ago)"
    )


def test_operation_becomes_unresponsive_once_most_requests_hang_while_others_answer():
    state = HealthState()
    for index in range(UNRESPONSIVE_MIN_FAILURES):
        state.record_transport_failure(operation_label="GET /hangs", now=10.0 + index)
        state.record_completion(operation_label="GET /healthy", now=10.5 + index)
    assert state.mark_unresponsive("GET /hangs") is True
    assert state.mark_unresponsive("GET /hangs") is False
    assert state.is_unresponsive("GET /hangs")
    assert not state.is_unresponsive("GET /healthy")


def test_operation_that_mostly_answers_stays_responsive():
    state = HealthState()
    for index in range(UNRESPONSIVE_MIN_FAILURES):
        state.record_transport_failure(operation_label="GET /items/{id}", now=10.0 + index)
        for _ in range(4):
            state.record_completion(operation_label="GET /items/{id}", now=10.5 + index)
    assert state.mark_unresponsive("GET /items/{id}") is False
    assert not state.is_unresponsive("GET /items/{id}")


# Nothing answering is a dead API, which aborts the phase instead of dropping operations one by one.
def test_operation_is_not_unresponsive_when_nothing_answers():
    state = HealthState()
    state.record_completion(operation_label="GET /healthy", now=1.0)
    for index in range(UNRESPONSIVE_MIN_FAILURES):
        state.record_transport_failure(operation_label="GET /hangs", now=10.0 + index)
    assert state.mark_unresponsive("GET /hangs") is False
