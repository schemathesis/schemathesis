from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from schemathesis.cli.commands.run.handlers.base import EventHandler, TextOutput
from schemathesis.engine import events
from schemathesis.reporting.junitxml import JunitXmlWriter
from schemathesis.reporting.recorders import scenario_records

if TYPE_CHECKING:
    from schemathesis.cli.context import BaseExecutionContext


@dataclass(slots=True)
class JunitXMLHandler(EventHandler):
    output: TextOutput
    writer: JunitXmlWriter
    # Errors already written: one per label and error type, however many phases raise it, as in the CLI output
    errors: set[events.NonFatalError]

    def __init__(self, output: TextOutput) -> None:
        self.output = output
        self.writer = JunitXmlWriter(output)
        self.errors = set()

    def handle_event(self, ctx: BaseExecutionContext, event: events.EngineEvent) -> None:
        for record in scenario_records(ctx.statistic, event):
            self.writer.record_scenario(
                label=record.label,
                elapsed_sec=record.elapsed_sec,
                failures=record.failures,
                skip_reason=record.skip_reason,
                config=ctx.config.output,
            )
        if isinstance(event, events.NonFatalError) and event not in self.errors:
            self.errors.add(event)
            self.writer.record_error(label=event.label, message=event.info.format(base_url_is_set=ctx.base_url_is_set))

    def shutdown(self, ctx: BaseExecutionContext) -> None:
        self.writer.unskip(ctx.statistic.tested_operations)
        self.writer.close()
