from __future__ import annotations

from typing import TYPE_CHECKING

from schemathesis.core.parameters import ParameterLocation
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
    """Return True iff at least one observation names a parameter the link wrote.

    A `(location, None)` entry means the link replaced the whole body and matches any
    body-located observation.
    """
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
    """Update a link's score from parser-attributed blame, dropping 404/409 as resource-state evidence."""
    if (
        step_input.transition is None
        or not step_input.is_applied
        or response.status_code in {401, 403}
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
    if observations and not _is_link_attributed(observations, step_input.applied_parameters):
        return

    calibration.record(step_input.transition.id, success=False)
