from __future__ import annotations

import threading
import warnings
from dataclasses import dataclass
from typing import Sequence

from .. import experimental
from ..auths import unregister as unregister_auth
from . import events, phases
from .config import EngineConfig
from .context import RunnerContext
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

        ctx = RunnerContext(stop_event=threading.Event(), config=self.config)
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
                self.config.execution.stateful is not None and self.config.schema.links_count > 0,
            ),
        ]
        return ExecutionPlan(phases)


@dataclass
class ExecutionPlan:
    """Manages test execution phases."""

    phases: Sequence[Phase]

    def execute(self, ctx: RunnerContext) -> EventGenerator:
        """Execute all phases in sequence."""
        try:
            if ctx.is_stopped:
                yield from self._finish(ctx)
                return
            # Initialize
            yield from self._run_initialization(ctx)
            if ctx.is_stopped:
                yield from self._finish(ctx)  # type: ignore[unreachable]
                return

            # Run main phases
            for phase in self.phases:
                if phase.should_run(ctx):
                    yield from self._run_phase(phase, ctx)
                if ctx.is_stopped:
                    break  # type: ignore[unreachable]

        except KeyboardInterrupt:
            ctx.control.stop()
            yield events.Interrupted()

        # Always finish
        yield from self._finish(ctx)

    def _run_initialization(self, ctx: RunnerContext) -> EventGenerator:
        """Initialize the test run."""
        yield events.Initialized.from_schema(schema=ctx.config.schema, seed=ctx.config.execution.seed)

    def _finish(self, ctx: RunnerContext) -> EventGenerator:
        """Finish the test run."""
        if ctx.has_all_not_found:
            ctx.add_warning(ALL_NOT_FOUND_WARNING_MESSAGE)
        yield events.Finished.from_results(results=ctx.data, running_time=ctx.running_time)

    def _run_phase(self, phase: Phase, ctx: RunnerContext) -> EventGenerator:
        """Execute a single phase."""
        from urllib3.exceptions import InsecureRequestWarning

        with warnings.catch_warnings():
            warnings.simplefilter("ignore", InsecureRequestWarning)

            if phase.kind == PhaseKind.PROBING:
                yield from phases.probes.execute(ctx)
            elif phase.kind == PhaseKind.ANALYSIS:
                yield from phases.analysis.execute(ctx)
            elif phase.kind == PhaseKind.UNIT_TESTING:
                yield from phases.unit.execute(ctx)
            elif phase.kind == PhaseKind.STATEFUL_TESTING:
                yield from phases.stateful.execute(ctx)


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
