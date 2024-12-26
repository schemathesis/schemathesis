import click

from ...runner import events
from ...stateful import events as stateful_events
from ..context import ExecutionContext
from ..handlers import EventHandler
from . import default


def handle_before_execution(context: ExecutionContext, event: events.BeforeExecution) -> None:
    pass


def handle_after_execution(context: ExecutionContext, event: events.AfterExecution) -> None:
    context.operations_processed += 1
    context.results.append(event.result)
    default.display_execution_result(context, event.status.value)


def handle_stateful_event(context: ExecutionContext, event: events.StatefulEvent) -> None:
    if isinstance(event.data, stateful_events.RunStarted):
        click.echo()
    default.handle_stateful_event(context, event)


class ShortOutputStyleHandler(EventHandler):
    def handle_event(self, context: ExecutionContext, event: events.EngineEvent) -> None:
        """Short output style shows single symbols in the progress bar.

        Otherwise, identical to the default output style.
        """
        from schemathesis.runner.phases import PhaseKind
        from schemathesis.runner.phases.analysis import AnalysisPayload
        from schemathesis.runner.phases.probes import ProbingPayload

        if isinstance(event, events.Initialized):
            default.handle_initialized(context, event)
        elif isinstance(event, events.PhaseStarted):
            if event.phase == PhaseKind.PROBING:
                default.handle_before_probing()
            elif event.phase == PhaseKind.ANALYSIS:
                default.handle_before_analysis()
        elif isinstance(event, events.PhaseFinished):
            if event.phase == PhaseKind.PROBING:
                assert isinstance(event.payload, ProbingPayload) or event.payload is None
                default.handle_after_probing(context, event.status, event.payload)
            if event.phase == PhaseKind.ANALYSIS:
                assert isinstance(event.payload, AnalysisPayload) or event.payload is None
                default.handle_after_analysis(context, event.status, event.payload)
        elif isinstance(event, events.BeforeExecution):
            handle_before_execution(context, event)
        elif isinstance(event, events.AfterExecution):
            handle_after_execution(context, event)
        elif isinstance(event, events.Finished):
            if context.operations_count == context.operations_processed:
                click.echo()
            default.handle_finished(context, event)
        elif isinstance(event, events.Interrupted):
            default.handle_interrupted(context, event)
        elif isinstance(event, events.InternalError):
            default.handle_internal_error(context, event)
        elif isinstance(event, events.StatefulEvent):
            handle_stateful_event(context, event)
        elif isinstance(event, events.AfterStatefulExecution):
            default.handle_after_stateful_execution(context, event)
