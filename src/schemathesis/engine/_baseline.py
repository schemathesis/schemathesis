from __future__ import annotations

from typing import TYPE_CHECKING

from schemathesis.engine import Status

if TYPE_CHECKING:
    from schemathesis.baseline import Baseline
    from schemathesis.core.failures import Failure
    from schemathesis.engine.recorder import ScenarioRecorder


def is_known(failure: Failure, check: str, baseline: Baseline | None) -> bool:
    return baseline is not None and baseline.match(failure, check) is not None


def has_new_failures(recorder: ScenarioRecorder, baseline: Baseline | None) -> bool:
    """Whether the scenario hit anything the baseline does not already account for."""
    for checks in recorder.checks.values():
        for check in checks:
            if check.status != Status.FAILURE:
                continue
            if check.failure_info is None or not is_known(check.failure_info.failure, check.name, baseline):
                return True
    return False
