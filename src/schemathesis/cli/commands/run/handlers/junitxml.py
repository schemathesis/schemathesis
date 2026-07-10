from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from schemathesis.cli.commands.run.handlers.base import EventHandler, TextOutput
from schemathesis.core.failures import RUN_CHECKS_LABEL
from schemathesis.engine import Status, events
from schemathesis.engine.statistic import GroupedFailures
from schemathesis.reporting.junitxml import JunitXmlWriter
from schemathesis.reporting.recorders import scenario_elapsed, scenario_failures, unique_labels

if TYPE_CHECKING:
    from schemathesis.cli.context import BaseExecutionContext


@dataclass(slots=True)
class JunitXMLHandler(EventHandler):
    output: TextOutput
    writer: JunitXmlWriter

    def __init__(self, output: TextOutput) -> None:
        self.output = output
        self.writer = JunitXmlWriter(output)

    def handle_event(self, ctx: BaseExecutionContext, event: events.EngineEvent) -> None:
        if isinstance(event, events.ScenarioFinished):
            label = event.recorder.label
            if event.status == Status.FAILURE:
                # Look up failures by case_id across all labels — some coverage scenarios
                # (e.g. UNSPECIFIED_HTTP_METHOD) store failures under a remapped label
                # (the actual method+path tested) rather than the recorder label.
                case_ids = set(event.recorder.cases.keys())
                failures = [
                    group
                    for groups in ctx.statistic.failures.values()
                    for case_id, group in groups.items()
                    if case_id in case_ids
                ]
            else:
                failures = []
            self.writer.record_scenario(
                label=label,
                elapsed_sec=event.elapsed_time,
                failures=failures,
                skip_reason=event.skip_reason,
                config=ctx.config.output,
            )
        elif isinstance(event, events.FuzzScenarioFinished):
            if event.status in (Status.SUCCESS, Status.FAILURE):
                for label in unique_labels(event.recorder):
                    failures = (
                        scenario_failures(ctx.statistic, event.recorder, label)
                        if event.status == Status.FAILURE
                        else []
                    )
                    self.writer.record_scenario(
                        label=label,
                        elapsed_sec=scenario_elapsed(event.recorder, label),
                        failures=failures,
                        skip_reason=None,
                        config=ctx.config.output,
                    )
            else:
                self.writer.record_scenario(
                    label=event.recorder.label,
                    elapsed_sec=event.elapsed_time,
                    failures=[],
                    skip_reason=None,
                    config=ctx.config.output,
                )
        elif isinstance(event, events.NonFatalError):
            self.writer.record_error(label=event.label, message=event.info.format())
        elif isinstance(event, events.EngineFinished):
            if event.failures:
                group = GroupedFailures(case_id=None, code_sample=None, failures=event.failures, response=None)
                self.writer.record_scenario(
                    label=RUN_CHECKS_LABEL,
                    elapsed_sec=0.0,
                    failures=[group],
                    skip_reason=None,
                    config=ctx.config.output,
                )

    def shutdown(self, ctx: BaseExecutionContext) -> None:
        for label, test_case in self.writer._test_cases.items():
            if label in ctx.statistic.tested_operations:
                test_case.skipped = []
        self.writer.close()
