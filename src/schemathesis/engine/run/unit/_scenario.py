from __future__ import annotations

import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from schemathesis.core.timing import Instant
from schemathesis.engine import Status, events
from schemathesis.engine._check_context import collect_check_context_data
from schemathesis.engine.errors import TestingState, deduplicate_errors
from schemathesis.engine.recorder import ScenarioRecorder
from schemathesis.engine.run import PhaseName
from schemathesis.engine.run.unit._case import record_extra_data_from_recorder

if TYPE_CHECKING:
    from schemathesis.checks import CheckContext
    from schemathesis.config import GenerationConfig
    from schemathesis.engine.context import EngineContext
    from schemathesis.schemas import APIOperation


@dataclass(slots=True)
class Scenario:
    """Everything one unit-phase scenario for an operation runs with."""

    operation: APIOperation
    phase: PhaseName
    suite_id: uuid.UUID
    scenario_id: uuid.UUID
    started_at: Instant
    recorder: ScenarioRecorder
    state: TestingState
    check_ctx: CheckContext
    generation: GenerationConfig
    transport_kwargs: dict[str, Any]
    continue_on_failure: bool

    def non_fatal_error(self, error: Exception, code_sample: str | None = None) -> events.NonFatalError:
        return events.NonFatalError(
            error=error,
            phase=self.phase,
            label=self.operation.label,
            related_to_operation=True,
            code_sample=code_sample,
        )

    def finished(self, status: Status, skip_reason: str | None) -> events.ScenarioFinished:
        return events.ScenarioFinished(
            id=self.scenario_id,
            suite_id=self.suite_id,
            phase=self.phase,
            label=self.operation.label,
            recorder=self.recorder,
            status=status,
            elapsed_time=self.started_at.elapsed,
            skip_reason=skip_reason,
            is_final=False,
        )


def start_scenario(
    *, operation: APIOperation, ctx: EngineContext, phase: PhaseName, suite_id: uuid.UUID, scenario_id: uuid.UUID
) -> Scenario:
    """Resolve the per-operation configuration a scenario needs and open a fresh error-feedback window."""
    started_at = Instant()
    recorder = ScenarioRecorder(label=operation.label, config=ctx.config.output)
    check_data = collect_check_context_data(operation=operation, ctx=ctx, phase=phase.value)
    scenario = Scenario(
        operation=operation,
        phase=phase,
        suite_id=suite_id,
        scenario_id=scenario_id,
        started_at=started_at,
        recorder=recorder,
        state=TestingState(),
        check_ctx=check_data.to_check_context(
            recorder=recorder,
            response_checks=ctx.checks.for_responses(),
            phase=phase,
            auth_enforced_operations=ctx.auth_enforced_operations,
        ),
        generation=ctx.config.generation_for(operation=operation, phase=phase.value),
        transport_kwargs=check_data.transport_kwargs,
        continue_on_failure=_continue_on_failure(operation, ctx),
    )
    _checkpoint_error_feedback(ctx)
    return scenario


def _checkpoint_error_feedback(ctx: EngineContext) -> None:
    if ctx.error_feedback is not None:
        ctx.error_feedback.checkpoint()


def _continue_on_failure(operation: APIOperation, ctx: EngineContext) -> bool:
    operation_config = ctx.config.operations.get_for_operation(operation)
    return operation_config.continue_on_failure or ctx.config.continue_on_failure or False


def iter_closing_events(
    scenario: Scenario,
    ctx: EngineContext,
    *,
    pending_events: list[events.EngineEvent],
    errors: list[Exception],
) -> Iterator[events.EngineEvent]:
    """Deferred events and deduplicated errors, then feed what the scenario captured into the resource pool."""
    yield from pending_events
    for error in deduplicate_errors(errors):
        yield scenario.non_fatal_error(error)
    record_extra_data_from_recorder(ctx, scenario.operation, scenario.recorder)
