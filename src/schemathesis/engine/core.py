from __future__ import annotations

import threading
from dataclasses import dataclass

from schemathesis import auths
from schemathesis.core import SpecificationFeature
from schemathesis.core.errors import HookExecutionError
from schemathesis.engine import Status, events, phases
from schemathesis.engine.observations import Observations
from schemathesis.schemas import BaseSchema

from .context import EngineContext
from .events import EventGenerator, StatefulPhasePayload
from .phases import Phase, PhaseName, PhaseSkipReason


@dataclass
class Engine:
    schema: BaseSchema

    __slots__ = ("schema",)

    def execute(self) -> EventStream:
        """Execute all test phases."""
        # Unregister auth if explicitly provided
        if self.schema.config.auth.is_defined:
            auths.unregister()

        plan = self._create_execution_plan()

        observations = None
        for phase in plan.phases:
            if (
                phase.name == PhaseName.STATEFUL_TESTING
                and phase.skip_reason in (None, PhaseSkipReason.NOT_APPLICABLE)
                and self.schema.config.phases.stateful.inference.is_enabled
            ):
                observations = Observations()

        ctx = EngineContext(schema=self.schema, stop_event=threading.Event(), observations=observations)
        return EventStream(plan.execute(ctx), ctx.control.stop_event)

    def _create_execution_plan(self) -> ExecutionPlan:
        """Create execution plan based on configuration."""
        phases = [
            self.get_phase_config(PhaseName.PROBING, is_supported=True, requires_links=False),
            self.get_phase_config(
                PhaseName.SCHEMA_ANALYSIS,
                is_supported=self.schema.specification.supports_feature(SpecificationFeature.SCHEMA_ANALYSIS),
                requires_links=False,
            ),
            self.get_phase_config(
                PhaseName.EXAMPLES,
                is_supported=self.schema.specification.supports_feature(SpecificationFeature.EXAMPLES),
                requires_links=False,
            ),
            self.get_phase_config(
                PhaseName.COVERAGE,
                is_supported=self.schema.specification.supports_feature(SpecificationFeature.COVERAGE),
                requires_links=False,
            ),
            self.get_phase_config(PhaseName.FUZZING, is_supported=True, requires_links=False),
            self.get_phase_config(
                PhaseName.STATEFUL_TESTING,
                is_supported=self.schema.specification.supports_feature(SpecificationFeature.STATEFUL_TESTING),
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

        phase = phase_name.value.lower()
        if (
            phase in ("examples", "coverage", "fuzzing", "stateful")
            and not self.schema.config.phases.get_by_name(name=phase).enabled
        ):
            return Phase(
                name=phase_name,
                is_supported=True,
                is_enabled=False,
                skip_reason=PhaseSkipReason.DISABLED,
            )

        if requires_links and self.schema.statistic.links.total == 0:
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

    phases: list[Phase]

    __slots__ = ("phases",)

    def execute(self, engine: EngineContext) -> EventGenerator:
        """Execute all phases in sequence."""
        yield events.EngineStarted()
        try:
            if engine.is_interrupted:
                yield from self._finish(engine)
                return
            if engine.is_interrupted:
                yield from self._finish(engine)  # type: ignore[unreachable]
                return

            # Run main phases
            for phase in self.phases:
                try:
                    payload = self._adapt_execution(engine, phase)
                except HookExecutionError as exc:
                    yield events.NonFatalError(
                        error=exc, phase=phase.name, label=f"`{exc.hook_name}` hook", related_to_operation=False
                    )
                    yield events.PhaseFinished(phase=phase, status=Status.ERROR, payload=None)
                    continue
                yield events.PhaseStarted(phase=phase, payload=payload)
                if phase.should_execute(engine):
                    yield from phases.execute(engine, phase)
                else:
                    if engine.has_reached_the_failure_limit:
                        phase.skip_reason = PhaseSkipReason.FAILURE_LIMIT_REACHED
                    yield events.PhaseFinished(phase=phase, status=Status.SKIP, payload=None)
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

    def _adapt_execution(self, engine: EngineContext, phase: Phase) -> StatefulPhasePayload | None:
        if engine.has_reached_the_failure_limit:
            phase.skip_reason = PhaseSkipReason.FAILURE_LIMIT_REACHED
        # Phase can be enabled if certain conditions are met
        if phase.name == PhaseName.STATEFUL_TESTING:
            inferred = engine.inject_links()
            # Enable stateful testing if we successfully generated any links
            if inferred:
                phase.enable()
            return StatefulPhasePayload(inferred_links=inferred)
        return None


@dataclass
class EventStream:
    """Schemathesis event stream.

    Provides an API to control the execution flow.
    """

    generator: EventGenerator
    stop_event: threading.Event

    __slots__ = ("generator", "stop_event")

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
