from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from schemathesis.engine.recorder import ScenarioRecorder
    from schemathesis.engine.statistic import GroupedFailures


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
