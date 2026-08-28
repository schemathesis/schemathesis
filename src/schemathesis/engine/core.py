from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from schemathesis import auths
from schemathesis.config import ConfigError, FuzzConfig
from schemathesis.core import SpecificationFeature
from schemathesis.core.errors import HookExecutionError
from schemathesis.engine import Status, StopReason, events, fuzz, run
from schemathesis.engine._check_context import run_after_run_checks
from schemathesis.engine.context import EngineContext
from schemathesis.engine.events import EventGenerator, StatefulPhasePayload
from schemathesis.engine.observations import Observations
from schemathesis.engine.run import ELASTIC_PHASES, Phase, PhaseName, PhaseSkipReason

if TYPE_CHECKING:
    from schemathesis.core.spec import ApiSchema


@dataclass(slots=True)
class Engine:
    schema: ApiSchema

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

        ctx = EngineContext(
            schema=self.schema,
            stop_event=threading.Event(),
            observations=observations,
            max_time=self.schema.config.max_time,
        )
        return EventStream(plan.execute(ctx), ctx.control.stop_event)

    def fuzz(self, config: FuzzConfig | None = None) -> EventStream:
        """Execute in fuzz mode."""
        if self.schema.config.auth.is_defined:
            auths.unregister()

        resolved_config = config or self.schema.config.fuzz
        ctx = EngineContext(schema=self.schema, stop_event=threading.Event(), max_time=resolved_config.max_time)
        return EventStream(fuzz.execute(ctx, resolved_config), ctx.control.stop_event)

    def _create_execution_plan(self) -> ExecutionPlan:
        """Create execution plan based on configuration."""
        phases = [
            self.get_phase_config(PhaseName.PROBING, is_supported=True, requires_transitions=False),
            self.get_phase_config(
                PhaseName.SCHEMA_ANALYSIS,
                is_supported=self.schema.specification.supports_feature(SpecificationFeature.SCHEMA_ANALYSIS),
                requires_transitions=False,
            ),
            self.get_phase_config(
                PhaseName.EXAMPLES,
                is_supported=self.schema.specification.supports_feature(SpecificationFeature.EXAMPLES),
                requires_transitions=False,
            ),
            self.get_phase_config(
                PhaseName.COVERAGE,
                is_supported=self.schema.specification.supports_feature(SpecificationFeature.COVERAGE),
                requires_transitions=False,
            ),
            self.get_phase_config(PhaseName.FUZZING, is_supported=True, requires_transitions=False),
            self.get_phase_config(
                PhaseName.STATEFUL_TESTING,
                is_supported=self.schema.specification.supports_feature(SpecificationFeature.STATEFUL_TESTING),
                requires_transitions=True,
            ),
        ]
        return ExecutionPlan(phases)

    def get_phase_config(
        self,
        phase_name: PhaseName,
        *,
        is_supported: bool = True,
        requires_transitions: bool = False,
    ) -> Phase:
        """Helper to determine phase configuration with proper skip reasons."""
        # Check if feature is supported by the schema
        if not is_supported:
            return Phase(
                name=phase_name,
                is_enabled=False,
                skip_reason=PhaseSkipReason.NOT_SUPPORTED,
            )

        phase = phase_name.value
        if (
            phase in ("examples", "coverage", "fuzzing", "stateful")
            and not self.schema.config.phases.get_by_name(name=phase).enabled
        ):
            return Phase(
                name=phase_name,
                is_enabled=False,
                skip_reason=PhaseSkipReason.DISABLED,
            )

        if requires_transitions and self.schema.statistic.transitions.total == 0:
            return Phase(
                name=phase_name,
                is_enabled=False,
                skip_reason=PhaseSkipReason.NOT_APPLICABLE,
            )

        return Phase(
            name=phase_name,
            is_enabled=True,
            skip_reason=None,
        )


# An API whose rejections keep naming something new never stops teaching; without a cap it would
# keep coverage regenerating until the budget is gone and leave the other phases nothing.
MAX_COVERAGE_CATCHUP_PASSES = 3


@dataclass(slots=True)
class ExecutionPlan:
    """Manages test execution phases."""

    phases: list[Phase]
    catchup_passes: int = 0
    # Elastic phases still worth a turn; a phase that proved idle is dropped and stops being reserved for.
    elastic: list[Phase] = field(default_factory=list)

    def execute(self, engine: EngineContext) -> EventGenerator:
        """Execute all phases in sequence."""
        yield events.EngineStarted()
        try:
            # Build checks up front: config errors surface here, not in a worker thread mid-run.
            _ = engine.checks
        except ConfigError as exc:
            yield events.NonFatalError(error=exc, phase=PhaseName.PROBING, label="checks", related_to_operation=False)
            yield events.EngineFinished(running_time=engine.running_time, stop_reason=engine.stop_reason)
            return
        try:
            if engine.is_interrupted:
                yield from self._finish(engine)
                return

            self._settle_stateful(engine)

            taught: dict[str, int] = {}
            bounded = [phase for phase in self.phases if phase.name not in ELASTIC_PHASES]
            self.elastic = [phase for phase in self.phases if phase.name in ELASTIC_PHASES]

            for phase in bounded:
                is_coverage = phase.name == PhaseName.COVERAGE
                if is_coverage:
                    # Feedback recorded before the pass is already folded into what it generates.
                    taught.update(self._feedback_counts(engine))
                yield from self._run_phase(engine, phase)
                if engine.is_interrupted:
                    break  # type: ignore[unreachable]
                if is_coverage:
                    yield from self._coverage_catchup(engine, taught)

            # Examples and coverage enumerate the schema once; repeating them re-sends the same cases.
            # Fuzzing and stateful keep drawing new ones, so only they are worth another turn.
            cycle = 0
            while not engine.is_interrupted:
                engine.cycle_index = cycle
                executed = 0
                idle = []
                for phase in self.elastic:
                    if engine.is_interrupted:
                        break  # type: ignore[unreachable]
                    for event in self._run_phase(engine, phase):
                        if isinstance(event, events.ScenarioFinished):
                            executed += 1
                        elif isinstance(event, events.PhaseFinished) and event.status == Status.SKIP:
                            idle.append(phase)
                        yield event
                    if not engine.is_interrupted:
                        yield from self._coverage_catchup(engine, taught)
                # A phase that skipped has nothing to run; holding budget back for it starves the rest.
                self.elastic = [phase for phase in self.elastic if phase not in idle]
                # A cycle that ran nothing will run nothing next time either.
                if engine.has_to_stop or engine.control.max_time is None or not executed:
                    break
                cycle += 1

        except KeyboardInterrupt:
            engine.stop()
            yield events.Interrupted(phase=None)

        # Always finish
        yield from self._finish(engine)

    def _coverage_catchup(self, engine: EngineContext, taught: dict[str, int]) -> EventGenerator:
        """Regenerate coverage for operations that have started explaining their rejections."""
        if engine.control.max_time is None or engine.has_to_stop:
            return
        coverage = next((item for item in self.phases if item.name == PhaseName.COVERAGE), None)
        if coverage is None or not coverage.is_enabled:
            return
        # Satisfying one constraint can reveal the next, so keep going while the pass keeps teaching.
        while self.catchup_passes < MAX_COVERAGE_CATCHUP_PASSES and not engine.has_to_stop:
            counts = self._feedback_counts(engine)
            learned = {label for label, count in counts.items() if count > taught.get(label, 0)}
            if not learned:
                return
            taught.update(counts)
            self.catchup_passes += 1
            yield from self._run_phase(engine, coverage, only=frozenset(learned))

    def _feedback_counts(self, engine: EngineContext) -> dict[str, int]:
        store = engine.error_feedback
        return store.observation_counts() if store is not None else {}

    def _run_phase(self, engine: EngineContext, phase: Phase, *, only: frozenset[str] | None = None) -> EventGenerator:
        try:
            payload = self._adapt_execution(engine, phase)
        except HookExecutionError as exc:
            yield events.NonFatalError(
                error=exc, phase=phase.name, label=f"`{exc.hook_name}` hook", related_to_operation=False
            )
            yield events.PhaseFinished(phase=phase, status=Status.ERROR, payload=None)
            return
        yield events.PhaseStarted(phase=phase, payload=payload)
        engine.reserve_time(self._time_to_reserve_after(phase, engine))
        if phase.should_execute(engine):
            yield from run.execute(engine, phase, only=only)
        else:
            if engine.has_reached_the_failure_limit:
                phase.skip_reason = PhaseSkipReason.FAILURE_LIMIT_REACHED
            yield events.PhaseFinished(phase=phase, status=Status.SKIP, payload=None)

    def _settle_stateful(self, engine: EngineContext) -> None:
        """Decide whether stateful testing will run, before any phase spends budget.

        Links may exist only through inference, which otherwise runs when the phase starts - too late
        for the earlier phases to reserve time for it. Static injection needs no runtime data.
        """
        if engine.control.max_time is None:
            return
        stateful = next((item for item in self.phases if item.name == PhaseName.STATEFUL_TESTING), None)
        if (
            stateful is not None
            and not stateful.is_enabled
            and stateful.skip_reason == PhaseSkipReason.NOT_APPLICABLE
            and engine.apply_stateful_inference().total > 0
        ):
            stateful.enable()

    def _is_reservable(self, phase: Phase, engine: EngineContext) -> bool:
        """Whether a share is still worth holding back for this phase."""
        # Links may only surface in responses, so keep stateful's share until a pass proves it idle.
        return phase.is_enabled or (
            phase.name == PhaseName.STATEFUL_TESTING
            and phase.skip_reason == PhaseSkipReason.NOT_APPLICABLE
            and engine.observations is not None
        )

    def _time_to_reserve_after(self, phase: Phase, engine: EngineContext) -> float:
        """Budget this pass must leave for the phases queued behind it in the same cycle."""
        remaining = engine.control.remaining_time
        if remaining is None:
            return 0.0
        elastic = [item for item in self.elastic if self._is_reservable(item, engine)]
        if phase in elastic:
            queued = len(elastic) - elastic.index(phase) - 1
        else:
            queued = len(elastic) if phase.name not in ELASTIC_PHASES else 0
        return remaining * queued / (queued + 1)

    def _finish(self, ctx: EngineContext) -> EventGenerator:
        """Finish the test run."""
        ctx.cache.flush()
        store = ctx.error_feedback
        summary = events.RunSummary(
            cache=events.CacheRunMetrics(
                observations_total=store.distinct_observations() if store is not None else 0,
            ),
            reauth_count=ctx.reauth.reauth_count,
            reauth_broke=ctx.reauth.broke,
        )
        # Skip after_run on a partial run (interrupt/abort); the fuzz path runs them on stop.
        # Spending the whole time budget is a planned finish, so it keeps them.
        completed = ctx.stop_reason in (StopReason.COMPLETED, StopReason.MAX_TIME)
        failures = run_after_run_checks(ctx) if completed else []
        yield events.EngineFinished(
            running_time=ctx.running_time,
            stop_reason=ctx.stop_reason,
            payload=summary,
            failures=failures,
        )

    def _adapt_execution(self, engine: EngineContext, phase: Phase) -> StatefulPhasePayload | None:
        if engine.has_reached_the_failure_limit:
            phase.skip_reason = PhaseSkipReason.FAILURE_LIMIT_REACHED
        # Phase can be enabled if certain conditions are met
        if phase.name == PhaseName.STATEFUL_TESTING:
            inference = engine.apply_stateful_inference()
            # Enable stateful testing if we successfully inferred any transitions
            if inference.inferred:
                phase.enable()
            return StatefulPhasePayload(
                inferred_transitions=inference.inferred,
                transitions_total=inference.total,
                transitions_selected=inference.selected,
            )
        return None


@dataclass(slots=True)
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
