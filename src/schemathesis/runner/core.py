from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Sequence

from .. import experimental
from ..auths import unregister as unregister_auth
from . import events
from .config import EngineConfig
from .context import EngineContext
from .events import EventGenerator
from .phases import Phase, PhaseKind


@dataclass
class Engine:
    config: EngineConfig

    def execute(self) -> EventStream:
        """Execute all test phases."""
        # Unregister auth if explicitly provided
        if self.config.network.auth is not None:
            unregister_auth()

        ctx = EngineContext(stop_event=threading.Event(), config=self.config)
        plan = self._create_execution_plan()
        return EventStream(plan.execute(ctx), ctx.control.stop_event)

    def _create_execution_plan(self) -> ExecutionPlan:
        """Create execution plan based on configuration."""
        phases = [
            Phase(PhaseKind.PROBING, True),
            Phase(PhaseKind.ANALYSIS, True),
            Phase(PhaseKind.UNIT_TESTING, not experimental.STATEFUL_ONLY.is_enabled),
            Phase(
                PhaseKind.STATEFUL_TESTING,
                self.config.schema.links_count > 0,
            ),
        ]
        return ExecutionPlan(phases)


@dataclass
class ExecutionPlan:
    """Manages test execution phases."""

    phases: Sequence[Phase]

    def execute(self, ctx: EngineContext) -> EventGenerator:
        """Execute all phases in sequence."""
        try:
            if ctx.is_stopped:
                yield from self._finish(ctx)
                return
            # Initialize
            yield events.Initialized.from_schema(schema=ctx.config.schema, seed=ctx.config.execution.seed)
            if ctx.is_stopped:
                yield from self._finish(ctx)  # type: ignore[unreachable]
                return

            # Run main phases
            for phase in self.phases:
                if phase.should_execute(ctx):
                    yield from phase.execute(ctx)
                if ctx.is_stopped:
                    break  # type: ignore[unreachable]

        except KeyboardInterrupt:
            ctx.control.stop()
            yield events.Interrupted()

        # Always finish
        yield from self._finish(ctx)

    def _finish(self, ctx: EngineContext) -> EventGenerator:
        """Finish the test run."""
        if ctx.has_all_not_found:
            ctx.add_warning(ALL_NOT_FOUND_WARNING_MESSAGE)
        yield events.Finished.from_results(results=ctx.data, running_time=ctx.running_time)


ALL_NOT_FOUND_WARNING_MESSAGE = "All API responses have a 404 status code. Did you specify the proper API location?"


@dataclass
class EventStream:
    """Schemathesis event stream.

    Provides an API to control the execution flow.
    """

    generator: EventGenerator
    stop_event: threading.Event

    def __next__(self) -> events.ExecutionEvent:
        return next(self.generator)

    def __iter__(self) -> EventGenerator:
        return self.generator

    def stop(self) -> None:
        """Stop the event stream.

        Its next value will be the last one (Finished).
        """
        self.stop_event.set()

    def finish(self) -> events.ExecutionEvent:
        """Stop the event stream & return the last event."""
        self.stop()
        return next(self)
