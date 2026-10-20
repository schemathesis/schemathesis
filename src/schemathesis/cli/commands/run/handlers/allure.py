from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from schemathesis.cli.commands.run.handlers.base import EventHandler
from schemathesis.cli.events import LoadingFinished
from schemathesis.core.failures import RUN_CHECKS_LABEL
from schemathesis.engine import Status, events
from schemathesis.engine.run import PhaseName
from schemathesis.reporting.recorders import (
    recorder_failures,
    scenario_elapsed,
    scenario_failures,
    scenario_has_failures,
    unique_labels,
)

if TYPE_CHECKING:
    from schemathesis.cli.context import BaseExecutionContext
    from schemathesis.config import OutputConfig
    from schemathesis.reporting.allure import AllureWriter


class AllureHandler(EventHandler):
    __slots__ = ("writer",)

    def __init__(self, output_dir: Path, config: OutputConfig) -> None:
        from schemathesis.reporting.allure import AllureWriter

        self.writer: AllureWriter = AllureWriter(output_dir=output_dir, config=config)

    def handle_event(self, ctx: BaseExecutionContext, event: events.EngineEvent) -> None:
        if isinstance(event, events.ScenarioFinished) and event.is_test_scenario:
            label = event.recorder.label
            failures = recorder_failures(ctx.statistic, event.recorder) if event.status == Status.FAILURE else []
            is_stateful = event.phase == PhaseName.STATEFUL_TESTING
            self.writer.record_scenario(
                label=label,
                elapsed_sec=event.elapsed_time,
                status=event.status,
                failures=failures,
                skip_reason=event.skip_reason,
                tags=_operation_tags(ctx, label) if not is_stateful else None,
            )
        elif isinstance(event, events.FuzzScenarioFinished):
            if event.status in (Status.SUCCESS, Status.FAILURE):
                for label in unique_labels(event.recorder):
                    self.writer.record_scenario(
                        label=label,
                        elapsed_sec=scenario_elapsed(event.recorder, label),
                        status=Status.FAILURE if scenario_has_failures(event.recorder, label) else Status.SUCCESS,
                        failures=scenario_failures(ctx.statistic, event.recorder, label)
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
        elif isinstance(event, LoadingFinished):
            self.writer.api_title = event.title
        elif isinstance(event, events.NonFatalError):
            self.writer.record_error(
                label=event.label, message=event.info.format(base_url_is_set=ctx.config.base_url is not None)
            )
        elif isinstance(event, events.EngineFinished):
            if event.failures:
                # Recorded by the execution context before handlers run, deduplicated and sorted.
                groups = list(ctx.statistic.failures[RUN_CHECKS_LABEL].values())
                self.writer.record_scenario(
                    label=RUN_CHECKS_LABEL,
                    elapsed_sec=0.0,
                    status=Status.FAILURE,
                    failures=groups,
                    skip_reason=None,
                    tags=None,
                )

    def shutdown(self, ctx: BaseExecutionContext) -> None:
        for label in ctx.statistic.tested_operations:
            result = self.writer._results.get(label)
            if result is not None and result.status == "passed":
                self.writer._skip_reasons.pop(label, None)
        self.writer.close()


def _operation_tags(ctx: BaseExecutionContext, label: str) -> list[str] | None:
    operation = ctx.find_operation(label)
    return operation.tags if operation is not None else None
