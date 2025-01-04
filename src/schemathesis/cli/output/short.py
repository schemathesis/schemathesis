import click

from schemathesis.runner import events
from schemathesis.runner.phases import PhaseName

from ..context import ExecutionContext
from ..handlers import EventHandler
from . import default


def on_scenario_finished(ctx: ExecutionContext, event: events.ScenarioFinished) -> None:
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
                default.on_stateful_testing_started(ctx)
        elif isinstance(event, events.PhaseFinished):
            if event.phase.name == PhaseName.PROBING:
                default.on_probing_finished(ctx, event.status)
                click.echo("\n")
            elif event.phase.name == PhaseName.STATEFUL_TESTING and event.phase.is_enabled:
                default.on_stateful_testing_finished(ctx, event.payload)
                click.echo("\n")
            elif event.phase.name == PhaseName.UNIT_TESTING and event.phase.is_enabled:
                click.echo("\n")
        elif isinstance(event, events.EngineFinished):
            default.on_engine_finished(ctx, event)
        elif isinstance(event, events.Interrupted):
            default.on_interrupted(ctx, event)
        elif isinstance(event, events.FatalError):
            default.on_internal_error(ctx, event)
        elif isinstance(event, events.TestEvent):
            if event.phase == PhaseName.STATEFUL_TESTING:
                default.on_stateful_test_event(ctx, event)
            elif event.phase == PhaseName.UNIT_TESTING and isinstance(event, events.ScenarioFinished):
                on_scenario_finished(ctx, event)
