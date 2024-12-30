import click

from schemathesis.runner import events
from schemathesis.runner.phases import PhaseName

from ..context import ExecutionContext
from ..handlers import EventHandler
from . import default


def on_before_execution(ctx: ExecutionContext, event: events.BeforeExecution) -> None:
    pass


def on_after_execution(ctx: ExecutionContext, event: events.AfterExecution) -> None:
    ctx.operations_processed += 1
    if event.result.checks:
        ctx.checks.append((event.result.label, event.result.checks))
    default.display_execution_result(ctx, event.status)


class ShortOutputStyleHandler(EventHandler):
    def handle_event(self, ctx: ExecutionContext, event: events.EngineEvent) -> None:
        """Short output style shows single symbols in the progress bar.

        Otherwise, identical to the default output style.
        """
        if isinstance(event, events.Initialized):
            default.on_initialized(ctx, event)
        elif isinstance(event, events.PhaseStarted):
            if event.phase.name == PhaseName.PROBING:
                default.on_probing_started()
            elif event.phase.name == PhaseName.STATEFUL_TESTING and event.phase.is_enabled:
                click.echo()
                default.on_stateful_testing_started(ctx)
        elif isinstance(event, events.PhaseFinished):
            if event.phase.name == PhaseName.PROBING:
                default.on_probing_finished(ctx, event.status)
            elif event.phase.name == PhaseName.STATEFUL_TESTING and event.phase.is_enabled:
                default.on_stateful_testing_finished(ctx, event.payload)
        elif isinstance(event, events.NonFatalError):
            ctx.errors.append(event)
        elif isinstance(event, events.Warning):
            ctx.warnings.append(event.message)
        elif isinstance(event, events.BeforeExecution):
            on_before_execution(ctx, event)
        elif isinstance(event, events.AfterExecution):
            on_after_execution(ctx, event)
        elif isinstance(event, events.EngineFinished):
            if ctx.operations_count == ctx.operations_processed:
                click.echo()
            default.on_engine_finished(ctx, event)
        elif isinstance(event, events.Interrupted):
            default.on_interrupted(ctx, event)
        elif isinstance(event, events.FatalError):
            default.on_internal_error(ctx, event)
        elif isinstance(event, events.TestEvent):
            if event.phase == PhaseName.STATEFUL_TESTING:
                default.on_stateful_test_event(ctx, event)
