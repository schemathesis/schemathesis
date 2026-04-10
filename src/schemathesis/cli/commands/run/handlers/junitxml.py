from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from schemathesis.cli.commands.run.handlers.base import EventHandler, TextOutput
from schemathesis.engine import Status, events
from schemathesis.reporting.junitxml import JunitXmlWriter

if TYPE_CHECKING:
    from schemathesis.cli.context import BaseExecutionContext
    from schemathesis.engine.recorder import ScenarioRecorder


@dataclass
class JunitXMLHandler(EventHandler):
    output: TextOutput
    writer: JunitXmlWriter

    __slots__ = ("output", "writer")

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
                for label in _fuzz_labels(event.recorder):
                    failures = _scenario_failures(ctx, event.recorder, label) if event.status == Status.FAILURE else []
                    self.writer.record_scenario(
                        label=label,
                        elapsed_sec=_scenario_elapsed(event.recorder, label),
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

    def shutdown(self, ctx: BaseExecutionContext) -> None:
        self.writer.close()


def _fuzz_labels(recorder: ScenarioRecorder) -> list[str]:
    return list(dict.fromkeys(node.value.operation.label for node in recorder.cases.values()))


def _scenario_failures(ctx: BaseExecutionContext, recorder: ScenarioRecorder, label: str) -> list:
    case_ids = {case_id for case_id, node in recorder.cases.items() if node.value.operation.label == label}
    return [group for case_id, group in ctx.statistic.failures.get(label, {}).items() if case_id in case_ids]


def _scenario_elapsed(recorder: ScenarioRecorder, label: str) -> float:
    return sum(
        interaction.response.elapsed
        for case_id, node in recorder.cases.items()
        if node.value.operation.label == label
        for interaction in [recorder.interactions.get(case_id)]
        if interaction is not None and interaction.response is not None
    )
