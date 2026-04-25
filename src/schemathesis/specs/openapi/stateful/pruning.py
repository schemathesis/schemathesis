from __future__ import annotations

from typing import TYPE_CHECKING

from schemathesis.engine.pruning import PruningState

if TYPE_CHECKING:
    from schemathesis.core.transport import Response
    from schemathesis.engine.recorder import ScenarioRecorder
    from schemathesis.generation.stateful.state_machine import StepInput


def record_pruning_observation(
    pruning: PruningState,
    response: Response,
    step_input: StepInput,
    recorder: ScenarioRecorder,
) -> None:
    """Record a pruning observation if the outcome is attributable to the link.

    4xx responses (excluding 401/403) are treated as failures: they indicate the
    link-extracted values were rejected by the server. 401/403 are skipped because
    they reflect auth configuration issues, not link quality. 5xx are skipped because
    server errors mask whether the request itself was valid.

    When a recorder is provided, 4xx responses are additionally filtered: if a prior
    successful DELETE in the same scenario explains the failure, the observation is
    skipped to avoid penalising a link that was actually correct.
    """
    if (
        step_input.transition is None
        or not step_input.is_applied
        or response.status_code in {401, 403}
        or response.status_code >= 500
    ):
        return
    if step_input.case.meta is not None and step_input.case.meta.generation.mode.is_negative:
        return
    if response.status_code >= 400:
        from schemathesis.specs.openapi.checks import resource_was_deleted

        if resource_was_deleted(recorder, step_input.case):
            return
    success = 200 <= response.status_code < 400
    pruning.record(step_input.transition.id, success=success)
