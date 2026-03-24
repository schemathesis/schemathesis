from __future__ import annotations

from dataclasses import dataclass

from schemathesis.cli.commands.run.context import ExecutionContext
from schemathesis.cli.commands.run.handlers.base import EventHandler, TextOutput
from schemathesis.engine import Status, events
from schemathesis.reporting.junitxml import JunitXmlWriter


@dataclass
class JunitXMLHandler(EventHandler):
    output: TextOutput
    writer: JunitXmlWriter

    __slots__ = ("output", "writer")

    def __init__(self, output: TextOutput) -> None:
        self.output = output
        self.writer = JunitXmlWriter(output)

    def handle_event(self, ctx: ExecutionContext, event: events.EngineEvent) -> None:
        if isinstance(event, events.ScenarioFinished):
            label = event.recorder.label
            failures = ctx.statistic.failures.get(label, {}).values() if event.status == Status.FAILURE else []
            self.writer.record_scenario(
                label=label,
                elapsed_sec=event.elapsed_time,
                failures=failures,
                skip_reason=event.skip_reason if event.status == Status.SKIP else None,
                config=ctx.config.output,
            )
        elif isinstance(event, events.NonFatalError):
            self.writer.record_error(label=event.label, message=event.info.format())

    def shutdown(self, ctx: ExecutionContext) -> None:
        self.writer.close()
