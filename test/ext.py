from dataclasses import dataclass

import schemathesis
from schemathesis import cli
from schemathesis.cli.commands.run.context import ExecutionContext
from schemathesis.cli.commands.run.handlers.base import EventHandler
from schemathesis.core.result import Ok
from schemathesis.engine import events


@schemathesis.hook
def after_load_schema(ctx, schema):
    for op in schema.get_all_operations():
        if isinstance(op, Ok):
            RESPONSES.setdefault(op.ok().label, {})
    state_machine = schema.as_state_machine()
    for operation in state_machine._transitions.operations.values():
        for link in operation.incoming:
            LINKS.setdefault(link.full_name, 0)
        for link in operation.outgoing:
            LINKS.setdefault(link.full_name, 0)


RESPONSES = {}
LINKS = {}


@cli.handler()
@dataclass
class ResponseStatistic(EventHandler):
    """A helper to display response status codes and link usage."""

    responses: dict[str, dict[int, int]]
    links: dict[str, int]

    __slots__ = ("responses", "links")

    def __init__(self, *args, **kwargs) -> None:
        self.responses = RESPONSES
        self.links = LINKS

    def handle_event(self, ctx: ExecutionContext, event: events.EngineEvent) -> None:
        if isinstance(event, events.ScenarioFinished):
            for key, interaction in event.recorder.interactions.items():
                case = event.recorder.cases[key]
                if interaction.response is not None:
                    response_entry = self.responses.setdefault(case.value.operation.label, {})
                    response_entry.setdefault(interaction.response.status_code, 0)
                    response_entry[interaction.response.status_code] += 1
                if case.transition is not None:
                    # A link could be dynamically added via inference
                    self.links.setdefault(case.transition.id, 0)
                    self.links[case.transition.id] += 1
        elif isinstance(event, events.EngineFinished):
            self._generate_summary(ctx)

    def _generate_summary(self, ctx: ExecutionContext):
        ctx.add_summary_line("")
        ctx.add_summary_line("Responses per operation:\n")

        for label in sorted(self.responses):
            entry = self.responses[label]
            total = sum(entry.values())
            ctx.add_summary_line(f"  {label}: {total} responses")

            max_status_len = max((len(str(s)) for s in entry.keys()), default=3)
            max_count_len = max((len(str(c)) for c in entry.values()), default=1)

            for status in sorted(entry):
                count = entry[status]
                ctx.add_summary_line(f"    {str(status).rjust(max_status_len)}  │  {str(count).rjust(max_count_len)}")

        used_links = {link: count for link, count in self.links.items() if count > 0}

        if used_links:
            ctx.add_summary_line("")
            ctx.add_summary_line(f"Links usage ({len(used_links)}):\n")

            max_link_len = max((len(link) for link in used_links.keys()), default=1)
            max_count_len = max((len(str(c)) for c in used_links.values()), default=1)

            for link in sorted(used_links):
                count = used_links[link]
                ctx.add_summary_line(f"  {link.ljust(max_link_len)}  │  {str(count).rjust(max_count_len)}")

        unused_links = {link: count for link, count in self.links.items() if count == 0}
        if unused_links:
            ctx.add_summary_line("")
            ctx.add_summary_line(f"Unused links({len(unused_links)}):\n")
            for link in sorted(unused_links):
                ctx.add_summary_line(f"  {link}")
