import click

from ...runner import events
from ..context import ExecutionContext
from ..handlers import EventHandler
from . import default


def on_before_execution(ctx: ExecutionContext, event: events.BeforeExecution) -> None:
    pass


def on_after_execution(ctx: ExecutionContext, event: events.AfterExecution) -> None:
    ctx.operations_processed += 1
    ctx.results.append(event.result)
    default.display_execution_result(ctx, event.status)


class ShortOutputStyleHandler(EventHandler):
    def handle_event(self, context: ExecutionContext, event: events.EngineEvent) -> None:
        """Short output style shows single symbols in the progress bar.

        Otherwise, identical to the default output style.
        """
        from schemathesis.runner.phases import PhaseName
        from schemathesis.runner.phases.probes import ProbingPayload
        from schemathesis.runner.phases.stateful import StatefulTestingPayload

        if isinstance(event, events.Initialized):
            default.on_initialized(context, event)
        elif isinstance(event, events.PhaseStarted):
            if event.phase.name == PhaseName.PROBING:
                default.on_probing_started()
            elif event.phase.name == PhaseName.STATEFUL_TESTING and event.phase.is_enabled:
                click.echo()
                default.on_stateful_testing_started(context)
        elif isinstance(event, events.PhaseFinished):
            if event.phase.name == PhaseName.PROBING:
                assert isinstance(event.payload, ProbingPayload) or event.payload is None
                default.on_probing_finished(context, event.status, event.payload)
            elif event.phase.name == PhaseName.STATEFUL_TESTING and event.phase.is_enabled:
                assert isinstance(event.payload, StatefulTestingPayload) or event.payload is None
                default.on_stateful_testing_finished(context, event.payload)
        elif isinstance(event, events.BeforeExecution):
            on_before_execution(context, event)
        elif isinstance(event, events.AfterExecution):
            on_after_execution(context, event)
        elif isinstance(event, events.EngineFinished):
            if context.operations_count == context.operations_processed:
                click.echo()
            default.on_engine_finished(context, event)
        elif isinstance(event, events.Interrupted):
            default.on_interrupted(context, event)
        elif isinstance(event, events.InternalError):
            default.on_internal_error(context, event)
        elif isinstance(event, events.TestEvent) and event.phase == PhaseName.STATEFUL_TESTING:
            default.on_stateful_test_event(context, event)
