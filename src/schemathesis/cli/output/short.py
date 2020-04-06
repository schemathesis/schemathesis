import click

from ...runner import events
from ..context import ExecutionContext
from . import default


def handle_after_execution(context: ExecutionContext, event: events.AfterExecution) -> None:
    context.endpoints_processed += 1
    context.results.append(event.result)
    context.hypothesis_output.extend(event.hypothesis_output)
    default.display_execution_result(context, event)
    if context.endpoints_processed == context.endpoints_count:
        click.echo()


def handle_event(context: ExecutionContext, event: events.ExecutionEvent) -> None:
    """Short output style shows single symbols in the progress bar.

    Otherwise, identical to the default output style.
    """
    if isinstance(event, events.Initialized):
        default.handle_initialized(context, event)
    if isinstance(event, events.AfterExecution):
        handle_after_execution(context, event)
    if isinstance(event, events.Finished):
        default.handle_finished(context, event)
    if isinstance(event, events.Interrupted):
        default.handle_interrupted(context, event)
    if isinstance(event, events.InternalError):
        default.handle_internal_error(context, event)
