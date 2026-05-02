from __future__ import annotations

import pytest

from schemathesis.core.warnings import SchemathesisWarning
from schemathesis.engine.supervisor import (
    METHOD_NOT_ALLOWED_THRESHOLD,
    SchedulingDirective,
    Supervisor,
)


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
        (
            ((200, False), *((405, False) for _ in range(METHOD_NOT_ALLOWED_THRESHOLD * 5))),
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
        "all-405-at-threshold-flips-to-skip",
        "below-threshold-stays-run",
        "any-non-405-blocks-skip-forever",
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
