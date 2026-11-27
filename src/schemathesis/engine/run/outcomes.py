"""Requests a run sent per operation, and what the API did with them."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from schemathesis.core.transport import CallOutcome
from schemathesis.engine import events
from schemathesis.engine.summary import ValidRate
from schemathesis.generation.meta import REQUEST_SHAPE_PROBES, coverage_scenario
from schemathesis.generation.modes import GenerationMode

if TYPE_CHECKING:
    from schemathesis.core.transport import StatusClass
    from schemathesis.engine.recorder import CaseNode, RecordedScenario
    from schemathesis.engine.run import PhaseName
    from schemathesis.generation.case import Case


@dataclass(slots=True)
class OperationRequests:
    """Requests each phase sent, by operation label."""

    counts: dict[str, dict[PhaseName, int]] = field(default_factory=dict)

    def fold(self, event: events.EngineEvent) -> None:
        """Add the requests of a finished scenario."""
        if not isinstance(event, events.ScenarioFinished):
            return
        for case_id in event.recorder.interactions:
            per_phase = self.counts.setdefault(event.recorder.cases[case_id].value.operation.label, {})
            per_phase[event.phase] = per_phase.get(event.phase, 0) + 1

    def total(self, phases: tuple[PhaseName, ...]) -> int:
        """Requests the given phases sent across all operations."""
        return sum(per_phase.get(phase, 0) for per_phase in self.counts.values() for phase in phases)


def is_positive(case: CaseNode) -> bool:
    return is_positive_case(case.value)


def is_positive_case(case: Case) -> bool:
    return case.meta is not None and case.meta.generation.mode == GenerationMode.POSITIVE


def _is_request_shape_probe(case: CaseNode) -> bool:
    return coverage_scenario(case.value) in REQUEST_SHAPE_PROBES


def _accumulate_call_outcome(outcomes: ValidRate, call_outcome: CallOutcome) -> None:
    if call_outcome is CallOutcome.ACCEPTED:
        outcomes.accepted += 1
    elif call_outcome is CallOutcome.UNREACHABLE:
        outcomes.unreachable += 1
    elif call_outcome is CallOutcome.CONFLICT:
        outcomes.conflicts += 1
    elif call_outcome is CallOutcome.REJECTED:
        outcomes.rejected += 1


def positive_call_outcomes(recorder: RecordedScenario) -> ValidRate:
    """Classify every positive case by what the API did with it; auth rejections and server errors are not counted."""
    outcomes = ValidRate()
    for case in recorder.cases.values():
        if not is_positive(case):
            continue
        interaction = recorder.interactions.get(case.value.id)
        if interaction is None or interaction.response is None:
            continue
        _accumulate_call_outcome(
            outcomes, case.value.operation.schema.classify_call_outcome(case.value, interaction.response)
        )
    for cached in recorder.cached_outcomes:
        if cached.is_positive:
            _accumulate_call_outcome(outcomes, cached.call_outcome)
    return outcomes


def aggregate_status_codes(
    recorder: RecordedScenario, classify_status: Callable[[int], StatusClass], *, positive_only: bool = False
) -> dict[int, int]:
    """Count the status codes the operation answered with, skipping requests with an undeclared method.

    Those reach another operation or none. With `positive_only`, responses to cases meant to be rejected are left out.
    """
    counts: dict[int, int] = {}

    for case_id, interaction in recorder.interactions.items():
        case = recorder.cases.get(case_id)
        if case is not None and _is_request_shape_probe(case):
            continue
        if positive_only and (case is None or not is_positive(case)):
            continue
        if interaction.response is not None:
            status = interaction.response.status_code
            # A client error that says nothing about the data, e.g. a cancelled gRPC call, is no rejection of it.
            if (
                positive_only
                and case is not None
                and classify_status(status).is_client_error
                and case.value.operation.schema.classify_call_outcome(case.value, interaction.response)
                is CallOutcome.UNINFORMATIVE
            ):
                continue
            counts[status] = counts.get(status, 0) + 1

    for cached in recorder.cached_outcomes:
        if positive_only and not cached.is_positive:
            continue
        status = cached.status_code
        if (
            positive_only
            and classify_status(status).is_client_error
            and cached.call_outcome is CallOutcome.UNINFORMATIVE
        ):
            continue
        counts[status] = counts.get(status, 0) + 1

    return counts
