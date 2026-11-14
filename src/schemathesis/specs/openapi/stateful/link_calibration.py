from __future__ import annotations

from typing import TYPE_CHECKING

from schemathesis.core.parameters import ParameterLocation
from schemathesis.core.transport import AUTH_REJECTION_STATUSES
from schemathesis.engine.link_calibration import LinkCalibrationState
from schemathesis.specs.openapi.checks import resource_was_deleted

if TYPE_CHECKING:
    from schemathesis.core.error_feedback.store import Observation
    from schemathesis.core.transport import Response
    from schemathesis.engine.recorder import ScenarioRecorder
    from schemathesis.generation.stateful.state_machine import StepInput


def _is_link_attributed(
    observations: tuple[Observation, ...],
    applied_parameters: list[tuple[ParameterLocation, str | None]],
) -> bool:
    """True if an observation names a parameter the link wrote; `(location, None)` matches any body observation."""
    if not observations or not applied_parameters:
        return False
    for location, name in applied_parameters:
        for observation in observations:
            if observation.location != location:
                continue
            if name is None:
                return True
            if observation.parameter_path and observation.parameter_path[0] == name:
                return True
    return False


def record_link_outcome(
    calibration: LinkCalibrationState,
    response: Response,
    observations: tuple[Observation, ...],
    step_input: StepInput,
    recorder: ScenarioRecorder,
) -> None:
    """Score a link from parser-attributed blame; 404/409 reflect resource state and do not count.

    A 4xx no parser explains lowers the score only of a link that never succeeded.
    """
    if (
        step_input.transition is None
        or not step_input.is_applied
        or response.status_code in AUTH_REJECTION_STATUSES
        or response.status_code >= 500
    ):
        return
    if step_input.case.meta is not None and step_input.case.meta.generation.mode.is_negative:
        return

    status = response.status_code
    if 200 <= status < 400:
        calibration.record(step_input.transition.id, success=True)
        return

    if resource_was_deleted(recorder, step_input.case):
        return
    if status in (404, 409):
        return
    if not observations:
        calibration.record_unattributed(step_input.transition.id)
        return
    if not _is_link_attributed(observations, step_input.applied_parameters):
        return

    calibration.record(step_input.transition.id, success=False)
