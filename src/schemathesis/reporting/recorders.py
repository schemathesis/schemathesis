from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from schemathesis.engine.recorder import ScenarioRecorder
    from schemathesis.engine.statistic import GroupedFailures, Statistic


def unique_labels(recorder: ScenarioRecorder) -> list[str]:
    """Unique per-operation labels of cases recorded, in first-seen order."""
    return list(dict.fromkeys(node.value.operation.label for node in recorder.cases.values()))


def scenario_failures(statistic: Statistic, recorder: ScenarioRecorder, label: str) -> list[GroupedFailures]:
    """Failures recorded for a specific operation label within a multi-operation scenario."""
    case_ids = {case_id for case_id, node in recorder.cases.items() if node.value.operation.label == label}
    return [group for case_id, group in statistic.failures.get(label, {}).items() if case_id in case_ids]


def scenario_has_failures(recorder: ScenarioRecorder, label: str) -> bool:
    """Whether any case for a specific operation label within a multi-operation scenario failed a check."""
    return any(
        check.failure_info is not None
        for case_id, node in recorder.cases.items()
        if node.value.operation.label == label
        for check in recorder.checks.get(case_id, [])
    )


def scenario_elapsed(recorder: ScenarioRecorder, label: str) -> float:
    """Total response time for a specific operation label within a multi-operation scenario."""
    return sum(
        interaction.response.elapsed
        for case_id, node in recorder.cases.items()
        if node.value.operation.label == label
        for interaction in [recorder.interactions.get(case_id)]
        if interaction is not None and interaction.response is not None
    )


def grouped_failures_from_recorder(recorder: ScenarioRecorder) -> list[GroupedFailures]:
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
