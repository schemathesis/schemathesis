from dataclasses import dataclass
from typing import TypeAlias

import schemathesis
from schemathesis import cli
from schemathesis.cli.commands.run.context import ExecutionContext
from schemathesis.cli.commands.run.handlers.base import EventHandler
from schemathesis.core.result import Ok
from schemathesis.engine import events

ResponseStatisticData: TypeAlias = dict[str, dict[int, int]]


@schemathesis.hook
def after_load_schema(ctx, schema):
    for op in schema.get_all_operations():
        if isinstance(op, Ok):
            DATA.setdefault(op.ok().label, {})


DATA = {}


@cli.handler()
@dataclass
class ResponseStatistic(EventHandler):
    """A helper to display response status codes per API operation."""

    data: ResponseStatisticData

    __slots__ = ("data",)

    def __init__(self, *args, **kwargs) -> None:
        self.data = DATA

    def handle_event(self, ctx: ExecutionContext, event: events.EngineEvent) -> None:
        if isinstance(event, events.ScenarioFinished):
            for key, interaction in event.recorder.interactions.items():
                case = event.recorder.cases[key]
                entry = self.data.setdefault(case.value.operation.label, {})
                if interaction.response is not None:
                    entry.setdefault(interaction.response.status_code, 0)
                    entry[interaction.response.status_code] += 1
        elif isinstance(event, events.EngineFinished):
            self._generate_summary(ctx)

    def _generate_summary(self, ctx: ExecutionContext):
        ctx.add_summary_line("")
        ctx.add_summary_line("Responses per operation:\n")

        for label in sorted(self.data):
            entry = self.data[label]
            total = sum(entry.values())
            ctx.add_summary_line(f"  {label}: {total} responses")

            max_status_len = max((len(str(s)) for s in entry.keys()), default=3)
            max_count_len = max((len(str(c)) for c in entry.values()), default=1)

            for status in sorted(entry):
                count = entry[status]
                ctx.add_summary_line(f"    {str(status).rjust(max_status_len)}  │  {str(count).rjust(max_count_len)}")
