from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from schemathesis.core.failures import RUN_CHECKS_LABEL
from schemathesis.engine import Status, events
from schemathesis.engine.run import PhaseName

if TYPE_CHECKING:
    from schemathesis.engine.recorder import RecordedScenario
    from schemathesis.engine.statistic import GroupedFailures, Statistic


def unique_labels(recorder: RecordedScenario) -> list[str]:
    """Unique per-operation labels of cases recorded, in first-seen order."""
    return list(dict.fromkeys(node.value.operation.label for node in recorder.cases.values()))


def scenario_failures(statistic: Statistic, recorder: RecordedScenario, label: str) -> list[GroupedFailures]:
    """Failures recorded for a specific operation label within a multi-operation scenario."""
    case_ids = {case_id for case_id, node in recorder.cases.items() if node.value.operation.label == label}
    return [group for case_id, group in statistic.failures.get(label, {}).items() if case_id in case_ids]


def recorder_failures(statistic: Statistic, recorder: RecordedScenario) -> list[GroupedFailures]:
    case_ids = set(recorder.cases)
    return [group for groups in statistic.failures.values() for case_id, group in groups.items() if case_id in case_ids]


def scenario_has_failures(recorder: RecordedScenario, label: str) -> bool:
    """Whether any case for a specific operation label within a multi-operation scenario failed a check."""
    failing = set(recorder.failing_case_ids())
    return any(node.value.operation.label == label for case_id, node in recorder.cases.items() if case_id in failing)


def scenario_elapsed(recorder: RecordedScenario, label: str) -> float:
    """Total response time for a specific operation label within a multi-operation scenario."""
    return sum(
        interaction.response.elapsed
        for case_id, node in recorder.cases.items()
        if node.value.operation.label == label
        for interaction in [recorder.interactions.get(case_id)]
        if interaction is not None and interaction.response is not None
    )


def grouped_failures_from_recorder(recorder: RecordedScenario) -> list[GroupedFailures]:
    """Group check failures by case ID, attaching the case's response and curl sample."""
    from schemathesis.engine.statistic import GroupedFailures

    grouped = []
    for case_id, checks in recorder.checks.items():
        failed = [check.failure_info for check in checks if check.failure_info is not None]
        if not failed:
            continue
        interaction = recorder.interactions.get(case_id)
        grouped.append(
            GroupedFailures(
                case_id=case_id,
                code_sample=failed[0].code_sample,
                failures=[info.failure for info in failed],
                response=interaction.response if interaction is not None else None,
            )
        )
    return grouped


@dataclass(slots=True)
class ScenarioRecord:
    """One finished scenario as a report entry for a single label."""

    label: str
    elapsed_sec: float
    status: Status
    failures: list[GroupedFailures]
    skip_reason: str | None
    # Whether the label is an API operation whose tags describe it.
    tagged: bool


def _test_scenario_record(statistic: Statistic, event: events.ScenarioFinished) -> ScenarioRecord:
    return ScenarioRecord(
        label=event.recorder.label,
        elapsed_sec=event.elapsed_time,
        status=event.status,
        failures=recorder_failures(statistic, event.recorder) if event.status == Status.FAILURE else [],
        skip_reason=event.skip_reason,
        tagged=event.phase != PhaseName.STATEFUL_TESTING,
    )


def _label_record(statistic: Statistic, recorder: RecordedScenario, label: str, *, failed: bool) -> ScenarioRecord:
    return ScenarioRecord(
        label=label,
        elapsed_sec=scenario_elapsed(recorder, label),
        status=Status.FAILURE if scenario_has_failures(recorder, label) else Status.SUCCESS,
        failures=scenario_failures(statistic, recorder, label) if failed else [],
        skip_reason=None,
        tagged=True,
    )


def _unfinished_fuzz_record(event: events.FuzzScenarioFinished) -> ScenarioRecord:
    return ScenarioRecord(
        label=event.recorder.label,
        elapsed_sec=event.elapsed_time,
        status=event.status,
        failures=[],
        skip_reason=None,
        tagged=False,
    )


def _fuzz_scenario_records(statistic: Statistic, event: events.FuzzScenarioFinished) -> list[ScenarioRecord]:
    if event.status not in (Status.SUCCESS, Status.FAILURE):
        return [_unfinished_fuzz_record(event)]
    failed = event.status == Status.FAILURE
    return [_label_record(statistic, event.recorder, label, failed=failed) for label in unique_labels(event.recorder)]


def _run_checks_record(statistic: Statistic) -> ScenarioRecord:
    # Recorded by the execution context before handlers run, deduplicated and sorted.
    return ScenarioRecord(
        label=RUN_CHECKS_LABEL,
        elapsed_sec=0.0,
        status=Status.FAILURE,
        failures=list(statistic.failures[RUN_CHECKS_LABEL].values()),
        skip_reason=None,
        tagged=False,
    )


def scenario_records(statistic: Statistic, event: events.EngineEvent) -> list[ScenarioRecord]:
    """Report entries for an event that finishes a scenario or the run; empty for any other event."""
    if isinstance(event, events.ScenarioFinished) and event.is_test_scenario:
        return [_test_scenario_record(statistic, event)]
    if isinstance(event, events.FuzzScenarioFinished):
        return _fuzz_scenario_records(statistic, event)
    if isinstance(event, events.EngineFinished) and event.failures:
        return [_run_checks_record(statistic)]
    return []
