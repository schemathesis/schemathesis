from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Sequence

from schemathesis.core import SpecificationFeature
from schemathesis.runner import Status

from .. import experimental
from ..auths import unregister as unregister_auth
from . import events, phases
from .config import EngineConfig
from .context import EngineContext
from .events import EventGenerator
from .phases import Phase, PhaseName, PhaseSkipReason


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
            self.get_phase_config(PhaseName.PROBING, is_supported=True, requires_links=False),
            self.get_phase_config(PhaseName.UNIT_TESTING, is_supported=True, requires_links=False),
            self.get_phase_config(
                PhaseName.STATEFUL_TESTING,
                is_supported=self.config.schema.specification.supports_feature(SpecificationFeature.STATEFUL_TESTING),
                requires_links=True,
            ),
        ]
        return ExecutionPlan(phases)

    def get_phase_config(
        self,
        phase_name: PhaseName,
        *,
        is_supported: bool = True,
        requires_links: bool = False,
    ) -> Phase:
        """Helper to determine phase configuration with proper skip reasons."""
        # Check if feature is supported by the schema
        if not is_supported:
            return Phase(
                name=phase_name,
                is_supported=False,
                is_enabled=False,
                skip_reason=PhaseSkipReason.NOT_SUPPORTED,
            )

        # Check if stateful-only mode is enabled (only affects unit testing)
        if experimental.STATEFUL_ONLY.is_enabled and phase_name == PhaseName.UNIT_TESTING:
            return Phase(
                name=phase_name,
                is_supported=True,
                is_enabled=False,
                skip_reason=PhaseSkipReason.DISABLED,
            )

        if requires_links and self.config.schema.links_count == 0:
            return Phase(
                name=phase_name,
                is_supported=True,
                is_enabled=False,
                skip_reason=PhaseSkipReason.NOT_APPLICABLE,
            )

        # Phase can be executed
        return Phase(
            name=phase_name,
            is_supported=True,
            is_enabled=True,
            skip_reason=None,
        )


@dataclass
class ExecutionPlan:
    """Manages test execution phases."""

    phases: Sequence[Phase]

    def execute(self, engine: EngineContext) -> EventGenerator:
        """Execute all phases in sequence."""
        yield events.EngineStarted()
        try:
            if engine.is_interrupted:
                yield from self._finish(engine)
                return
            yield events.Initialized.from_schema(schema=engine.config.schema, seed=engine.config.execution.seed)
            if engine.is_interrupted:
                yield from self._finish(engine)  # type: ignore[unreachable]
                return

            # Run main phases
            for phase in self.phases:
                if engine.has_reached_the_failure_limit:
                    phase.skip_reason = PhaseSkipReason.FAILURE_LIMIT_REACHED
                yield events.PhaseStarted(phase=phase)
                if phase.should_execute(engine):
                    yield from phases.execute(engine, phase)
                else:
                    if engine.has_reached_the_failure_limit:
                        phase.skip_reason = PhaseSkipReason.FAILURE_LIMIT_REACHED
                    yield events.PhaseFinished(phase=phase, status=Status.SKIP)
                if engine.is_interrupted:
                    break  # type: ignore[unreachable]

        except KeyboardInterrupt:
            engine.stop()
            yield events.Interrupted(phase=None)

        # Always finish
        yield from self._finish(engine)

    def _finish(self, ctx: EngineContext) -> EventGenerator:
        """Finish the test run."""
        yield events.EngineFinished(running_time=ctx.running_time)


@dataclass
class EventStream:
    """Schemathesis event stream.

    Provides an API to control the execution flow.
    """

    generator: EventGenerator
    stop_event: threading.Event

    def __next__(self) -> events.EngineEvent:
        return next(self.generator)

    def __iter__(self) -> EventGenerator:
        return self.generator

    def stop(self) -> None:
        """Stop the event stream.

        Its next value will be the last one (Finished).
        """
        self.stop_event.set()

    def finish(self) -> events.EngineEvent:
        """Stop the event stream & return the last event."""
        self.stop()
        return next(self)
