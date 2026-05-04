from __future__ import annotations

import pytest

from schemathesis.core.warnings import SchemathesisWarning
from schemathesis.engine.supervisor import (
    METHOD_NOT_ALLOWED_THRESHOLD,
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
            tuple((405, False) for _ in range(METHOD_NOT_ALLOWED_THRESHOLD)),
            SchedulingDirective.SKIP,
            SchemathesisWarning.METHOD_NOT_ALLOWED,
        ),
        (
            tuple((405, False) for _ in range(METHOD_NOT_ALLOWED_THRESHOLD - 1)),
            SchedulingDirective.RUN,
            None,
        ),
        # A single non-405 response permanently cancels the streak; mixed-traffic
        # operations whose handlers return 400 on some inputs stay in the queue.
        (
            ((200, False), *((405, False) for _ in range(METHOD_NOT_ALLOWED_THRESHOLD * 5))),
            SchedulingDirective.RUN,
            None,
        ),
        (
            ((400, False), *((405, False) for _ in range(METHOD_NOT_ALLOWED_THRESHOLD * 5))),
            SchedulingDirective.RUN,
            None,
        ),
        (tuple((200, False) for _ in range(10)), SchedulingDirective.RUN, None),
        (
            tuple((405, True) for _ in range(METHOD_NOT_ALLOWED_THRESHOLD * 5)),
            SchedulingDirective.RUN,
            None,
        ),
    ],
    ids=[
        "unknown-op-defaults-to-run",
        "consecutive-405s-flip-to-skip",
        "below-threshold-stays-run",
        "any-200-permanently-cancels-streak",
        "any-400-permanently-cancels-streak",
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


def test_each_operation_tracked_independently():
    supervisor = Supervisor()
    for _ in range(METHOD_NOT_ALLOWED_THRESHOLD):
        supervisor.record_response(operation_label="POST /missing", status_code=405)
    supervisor.record_response(operation_label="GET /items", status_code=200)
    assert supervisor.verdict("POST /missing").directive is SchedulingDirective.SKIP
    assert supervisor.verdict("GET /items").directive is SchedulingDirective.RUN


def test_skip_reason_format():
    supervisor = Supervisor()
    label = "POST /target"
    for _ in range(METHOD_NOT_ALLOWED_THRESHOLD):
        supervisor.record_response(operation_label=label, status_code=405)
    assert supervisor.verdict(label).reason == (
        f"Skipped after {METHOD_NOT_ALLOWED_THRESHOLD} consecutive `405 Method Not Allowed` responses"
    )


def _supervisor_with_skipped(label: str) -> Supervisor:
    supervisor = Supervisor()
    for _ in range(METHOD_NOT_ALLOWED_THRESHOLD):
        supervisor.record_response(operation_label=label, status_code=405)
    assert supervisor.verdict(label).directive is SchedulingDirective.SKIP
    return supervisor


def test_controller_allow_transition_rejects_supervisor_skip_target():
    controller = TransitionController(ApiTransitions())
    controller.supervisor = _supervisor_with_skipped("POST /skipped")
    assert controller.allow_transition("POST /alive", "POST /skipped") is False
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
    for _ in range(METHOD_NOT_ALLOWED_THRESHOLD - 1):
        supervisor.record_response(operation_label="POST /seen", status_code=405)
    assert supervisor.verdict("POST /seen").directive is SchedulingDirective.RUN
    controller.supervisor = supervisor
    assert controller.allow_transition("POST /alive", "POST /seen") is True
    assert controller.allow_root_transition("POST /seen", bundles={}) is True
