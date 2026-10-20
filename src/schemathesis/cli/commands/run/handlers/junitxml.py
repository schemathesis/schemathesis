from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from schemathesis.cli.commands.run.handlers.base import EventHandler, TextOutput
from schemathesis.core.failures import RUN_CHECKS_LABEL
from schemathesis.engine import Status, events
from schemathesis.reporting.junitxml import JunitXmlWriter
from schemathesis.reporting.recorders import recorder_failures, scenario_elapsed, scenario_failures, unique_labels

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
        if isinstance(event, events.ScenarioFinished) and event.is_test_scenario:
            label = event.recorder.label
            failures = recorder_failures(ctx.statistic, event.recorder) if event.status == Status.FAILURE else []
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
                    failures=groups,
                    skip_reason=None,
                    config=ctx.config.output,
                )

    def shutdown(self, ctx: BaseExecutionContext) -> None:
        for label, test_case in self.writer._test_cases.items():
            if label in ctx.statistic.tested_operations:
                test_case.skipped = []
        self.writer.close()
