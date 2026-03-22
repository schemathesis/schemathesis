from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from schemathesis.cli.commands.run.handlers.base import EventHandler
from schemathesis.engine import Status, events
from schemathesis.engine.run import PhaseName

if TYPE_CHECKING:
    from schemathesis.cli.context import BaseExecutionContext
    from schemathesis.config import OutputConfig
    from schemathesis.engine.recorder import ScenarioRecorder
    from schemathesis.reporting.allure import AllureWriter


class AllureHandler(EventHandler):
    __slots__ = ("writer",)

    def __init__(self, output_dir: Path, config: OutputConfig) -> None:
        from schemathesis.reporting.allure import AllureWriter

        self.writer: AllureWriter = AllureWriter(output_dir=output_dir, config=config)

    def handle_event(self, ctx: BaseExecutionContext, event: events.EngineEvent) -> None:
        if isinstance(event, events.ScenarioFinished):
            label = event.recorder.label
            failures = list(ctx.statistic.failures.get(label, {}).values()) if event.status == Status.FAILURE else []
            is_stateful = event.phase == PhaseName.STATEFUL_TESTING
            operation = ctx.find_operation_by_label(label) if not is_stateful and ctx.find_operation_by_label else None
            self.writer.record_scenario(
                label=label,
                elapsed_sec=event.elapsed_time,
                status=event.status,
                failures=failures,
                skip_reason=event.skip_reason,
                tags=operation.tags if operation is not None else None,
            )
        elif isinstance(event, events.FuzzScenarioFinished):
            if event.status in (Status.SUCCESS, Status.FAILURE):
                for label in _fuzz_labels(event.recorder):
                    self.writer.record_scenario(
                        label=label,
                        elapsed_sec=_scenario_elapsed(event.recorder, label),
                        status=Status.FAILURE if _scenario_has_failures(event.recorder, label) else Status.SUCCESS,
                        failures=_scenario_failures(ctx, event.recorder, label)
                        if event.status == Status.FAILURE
                        else [],
                        skip_reason=None,
                        tags=_operation_tags(ctx, label),
                    )
            else:
                self.writer.record_scenario(
                    label=event.recorder.label,
                    elapsed_sec=event.elapsed_time,
                    status=event.status,
                    failures=[],
                    skip_reason=None,
                    tags=None,
                )
        elif isinstance(event, events.NonFatalError):
            self.writer.record_error(label=event.label, message=event.info.format())

    def shutdown(self, ctx: BaseExecutionContext) -> None:
        self.writer.close()


def _fuzz_labels(recorder: ScenarioRecorder) -> list[str]:
    return list(dict.fromkeys(node.value.operation.label for node in recorder.cases.values()))


def _scenario_failures(ctx: BaseExecutionContext, recorder: ScenarioRecorder, label: str) -> list:
    case_ids = {case_id for case_id, node in recorder.cases.items() if node.value.operation.label == label}
    return [group for case_id, group in ctx.statistic.failures.get(label, {}).items() if case_id in case_ids]


def _scenario_has_failures(recorder: ScenarioRecorder, label: str) -> bool:
    return any(
        check.failure_info is not None
        for case_id, node in recorder.cases.items()
        if node.value.operation.label == label
        for check in recorder.checks.get(case_id, [])
    )


def _scenario_elapsed(recorder: ScenarioRecorder, label: str) -> float:
    return sum(
        interaction.response.elapsed
        for case_id, node in recorder.cases.items()
        if node.value.operation.label == label
        for interaction in [recorder.interactions.get(case_id)]
        if interaction is not None and interaction.response is not None
    )


def _operation_tags(ctx: BaseExecutionContext, label: str) -> list[str] | None:
    if ctx.find_operation_by_label is None:
        return None
    operation = ctx.find_operation_by_label(label)
    return operation.tags if operation is not None else None
