import click

from ...runner import events
from ..context import ExecutionContext
from ..handlers import EventHandler
from . import default


def handle_before_execution(context: ExecutionContext, event: events.BeforeExecution) -> None:
    if event.recursion_level > 0:
        context.operations_count += 1  # type: ignore


def handle_after_execution(context: ExecutionContext, event: events.AfterExecution) -> None:
    context.operations_processed += 1
    context.results.append(event.result)
    context.hypothesis_output.extend(event.hypothesis_output)
    default.display_execution_result(context, event)


class ShortOutputStyleHandler(EventHandler):
    def handle_event(self, context: ExecutionContext, event: events.ExecutionEvent) -> None:
        """Short output style shows single symbols in the progress bar.

        Otherwise, identical to the default output style.
        """
        if isinstance(event, events.Initialized):
            default.handle_initialized(context, event)
        if isinstance(event, events.BeforeExecution):
            handle_before_execution(context, event)
        if isinstance(event, events.AfterExecution):
            handle_after_execution(context, event)
        if isinstance(event, events.Finished):
            if context.operations_count == context.operations_processed:
                click.echo()
            default.handle_finished(context, event)
        if isinstance(event, events.Interrupted):
            default.handle_interrupted(context, event)
        if isinstance(event, events.InternalError):
            default.handle_internal_error(context, event)
