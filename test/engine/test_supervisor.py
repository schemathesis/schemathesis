from __future__ import annotations

import pytest

from schemathesis.core.warnings import SchemathesisWarning
from schemathesis.engine.supervisor import (
    METHOD_NOT_ALLOWED_WINDOW,
    SchedulingDirective,
    Supervisor,
)
from schemathesis.generation.stateful.control import TransitionController
from schemathesis.specs.openapi.stateful import ApiTransitions


@pytest.mark.parametrize(
    ("recorded", "expected_directive", "expected_warning"),
    [
        ((), SchedulingDirective.RUN, None),
        (
            tuple((405, False) for _ in range(METHOD_NOT_ALLOWED_WINDOW)),
            SchedulingDirective.SKIP,
            SchemathesisWarning.METHOD_NOT_ALLOWED,
        ),
        (
            tuple((405, False) for _ in range(METHOD_NOT_ALLOWED_WINDOW - 1)),
            SchedulingDirective.RUN,
            None,
        ),
        # PTS-style: 80% of last 10 responses are undocumented 405s, the rest are
        # body-validation 400s — the strict-streak rule used to miss this.
        (
            (
                (405, False),
                (405, False),
                (400, False),
                (405, False),
                (405, False),
                (405, False),
                (400, False),
                (405, False),
                (405, False),
                (405, False),
            ),
            SchedulingDirective.SKIP,
            SchemathesisWarning.METHOD_NOT_ALLOWED,
        ),
        # 70% of last 10 — below threshold, stays run.
        (
            (
                (405, False),
                (405, False),
                (400, False),
                (400, False),
                (400, False),
                (405, False),
                (405, False),
                (405, False),
                (405, False),
                (405, False),
            ),
            SchedulingDirective.RUN,
            None,
        ),
        (tuple((200, False) for _ in range(METHOD_NOT_ALLOWED_WINDOW)), SchedulingDirective.RUN, None),
        (
            tuple((405, True) for _ in range(METHOD_NOT_ALLOWED_WINDOW * 2)),
            SchedulingDirective.RUN,
            None,
        ),
    ],
    ids=[
        "unknown-op-defaults-to-run",
        "all-405-fills-window-flips-to-skip",
        "window-not-yet-full-stays-run",
        "mixed-405-at-or-above-rate-flips-to-skip",
        "mixed-405-below-rate-stays-run",
        "all-non-405-stays-run",
        "documented-405-is-not-evidence",
    ],
)
def test_verdict(recorded, expected_directive, expected_warning):
    supervisor = Supervisor()
    label = "POST /target"
    for status_code, is_documented_status in recorded:
        supervisor.record_response(
            operation_label=label,
            status_code=status_code,
            is_documented_status=is_documented_status,
        )
    verdict = supervisor.verdict(label)
    assert verdict.directive is expected_directive
    assert verdict.warning is expected_warning


def test_window_evicts_oldest_so_recovery_is_possible():
    # Operation recovers: a partial 405 streak, then enough non-405s to push the rate
    # below threshold before the verdict ever flips. After window backfill, RUN holds.
    supervisor = Supervisor()
    label = "POST /target"
    # 7 405s in a row — below the 8/10 threshold, so verdict still RUN.
    for _ in range(METHOD_NOT_ALLOWED_WINDOW - 3):
        supervisor.record_response(operation_label=label, status_code=405)
    # 7 non-405s now in window. Window-trailing 405 count should drop to 0; rate 0%.
    for _ in range(METHOD_NOT_ALLOWED_WINDOW):
        supervisor.record_response(operation_label=label, status_code=200)
    assert supervisor.verdict(label).directive is SchedulingDirective.RUN


def test_each_operation_tracked_independently():
    supervisor = Supervisor()
    for _ in range(METHOD_NOT_ALLOWED_WINDOW):
        supervisor.record_response(operation_label="POST /missing", status_code=405)
    supervisor.record_response(operation_label="GET /items", status_code=200)
    assert supervisor.verdict("POST /missing").directive is SchedulingDirective.SKIP
    assert supervisor.verdict("GET /items").directive is SchedulingDirective.RUN


def test_skip_reason_mentions_window_rate():
    supervisor = Supervisor()
    label = "POST /target"
    for _ in range(METHOD_NOT_ALLOWED_WINDOW):
        supervisor.record_response(operation_label=label, status_code=405)
    reason = supervisor.verdict(label).reason
    assert reason is not None
    assert "405" in reason
    assert str(METHOD_NOT_ALLOWED_WINDOW) in reason


def _supervisor_with_skipped(label: str) -> Supervisor:
    supervisor = Supervisor()
    for _ in range(METHOD_NOT_ALLOWED_WINDOW):
        supervisor.record_response(operation_label=label, status_code=405)
    assert supervisor.verdict(label).directive is SchedulingDirective.SKIP
    return supervisor


def test_controller_allow_transition_rejects_supervisor_skip_target():
    controller = TransitionController(ApiTransitions())
    controller.supervisor = _supervisor_with_skipped("POST /skipped")
    assert controller.allow_transition("POST /alive", "POST /skipped") is False
    # Targets the supervisor hasn't marked SKIP are still allowed
    assert controller.allow_transition("POST /alive", "POST /other") is True


def test_controller_allow_root_transition_rejects_supervisor_skip_source():
    controller = TransitionController(ApiTransitions())
    controller.supervisor = _supervisor_with_skipped("POST /skipped")
    assert controller.allow_root_transition("POST /skipped", bundles={}) is False
    assert controller.allow_root_transition("POST /alive", bundles={}) is True


def test_controller_without_supervisor_allows_everything():
    controller = TransitionController(ApiTransitions())
    assert controller.supervisor is None
    assert controller.allow_transition("POST /a", "POST /b") is True
    assert controller.allow_root_transition("POST /a", bundles={}) is True


def test_controller_supervisor_does_not_block_run_verdicts():
    # A supervisor that has a record but verdict still RUN must not block transitions.
    controller = TransitionController(ApiTransitions())
    supervisor = Supervisor()
    for _ in range(METHOD_NOT_ALLOWED_WINDOW - 1):
        supervisor.record_response(operation_label="POST /seen", status_code=405)
    assert supervisor.verdict("POST /seen").directive is SchedulingDirective.RUN
    controller.supervisor = supervisor
    assert controller.allow_transition("POST /alive", "POST /seen") is True
    assert controller.allow_root_transition("POST /seen", bundles={}) is True
