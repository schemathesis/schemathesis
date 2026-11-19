from __future__ import annotations

import sys
import threading
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from schemathesis import auths
from schemathesis.config import ConfigError, FuzzConfig, InferenceAlgorithm
from schemathesis.core import SpecificationFeature
from schemathesis.core.app_output import inside_application
from schemathesis.core.errors import (
    ApplicationShutdownError,
    HookExecutionError,
    InvalidSchema,
    format_app_exception,
)
from schemathesis.engine import Status, StopReason, events, run
from schemathesis.engine._check_context import run_after_run_checks
from schemathesis.engine.context import EngineContext
from schemathesis.engine.events import EventGenerator, StatefulPhasePayload
from schemathesis.engine.observations import Observations
from schemathesis.engine.run import ELASTIC_PHASES, TESTING_PHASES, Phase, PhaseName, PhaseSkipReason
from schemathesis.engine.run.outcomes import OperationRequests
from schemathesis.hooks import defines_teardown
from schemathesis.python.application import application_roots

if TYPE_CHECKING:
    from collections.abc import Generator, Mapping

    from schemathesis.core.spec import ApiSchema
    from schemathesis.generation.case import Case


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
                and self.schema.config.phases.stateful.inference.is_algorithm_enabled(
                    InferenceAlgorithm.LOCATION_HEADERS
                )
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
        plan = ExecutionPlan([Phase(PhaseName.SETUP), Phase(PhaseName.CONTINUOUS_FUZZING)])
        ctx = EngineContext(schema=self.schema, stop_event=threading.Event(), max_time=resolved_config.max_time)
        return EventStream(plan.execute(ctx), ctx.control.stop_event)

    def _create_execution_plan(self) -> ExecutionPlan:
        """Create execution plan based on configuration."""
        phases = [
            self.get_phase_config(PhaseName.SETUP, is_supported=True, requires_transitions=False),
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
        is_supported: bool,
        requires_transitions: bool,
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
        if phase_name in TESTING_PHASES and not self.schema.config.phases.get_by_name(name=phase).enabled:
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


def preflight_error(engine: EngineContext) -> events.NonFatalError | None:
    """Report run-wide configuration problems before any test starts."""
    try:
        # Build checks up front: config errors surface here, not in a worker thread mid-run.
        _ = engine.checks
        # Same for the base URL - a malformed `servers` entry is a schema error, not a phase crash.
        engine.schema.get_base_url()
    except ConfigError as exc:
        return events.NonFatalError(error=exc, phase=PhaseName.SETUP, label="checks", related_to_operation=False)
    except InvalidSchema as exc:
        return events.NonFatalError(error=exc, phase=PhaseName.SETUP, label="schema", related_to_operation=False)
    return None


# An API whose rejections keep naming something new never stops teaching; without a cap it would
# keep coverage regenerating until the budget is gone and leave the other phases nothing.
MAX_COVERAGE_CATCHUP_PASSES = 3


def _shutdown_applications(app: object) -> EventGenerator:
    """End the lifespans of in-process ASGI applications while their shutdown errors can still be reported."""
    # Only the ASGI client starts lifespans, so while it is not loaded there is nothing to end
    if "schemathesis.python.asgi" in sys.modules:
        from schemathesis.python.asgi import shutdown_lifespans

        try:
            with inside_application():
                shutdown_lifespans()
        except (Exception, BaseExceptionGroup) as exc:
            errors = exc.exceptions if isinstance(exc, BaseExceptionGroup) else (exc,)
            for error in errors:
                yield events.NonFatalError(
                    error=ApplicationShutdownError(format_app_exception(error, roots=application_roots(app))),
                    phase=PhaseName.TEARDOWN,
                    label="Application shutdown",
                    related_to_operation=False,
                )


@dataclass(slots=True)
class ExecutionPlan:
    """Manages test execution phases."""

    phases: list[Phase]
    catchup_passes: int = 0
    # Elastic phases still worth a turn; a phase that proved idle is dropped and stops being reserved for.
    elastic: list[Phase] = field(default_factory=list)
    # Title and message of errors already reported on an operation.
    operation_errors: set[tuple[str, str]] = field(default_factory=set)
    # Error-feedback observation counts per operation that coverage has already generated with.
    taught: dict[str, int] = field(default_factory=dict)
    requests: OperationRequests = field(default_factory=OperationRequests)
    # Phase whose start went out without its finish.
    unfinished: Phase | None = None

    def execute(self, engine: EngineContext) -> EventGenerator:
        """Execute all phases in sequence."""
        yield events.EngineStarted()
        error = preflight_error(engine)
        if error is not None:
            yield error
            yield events.EngineFinished(running_time=engine.running_time, stop_reason=engine.stop_reason)
            return
        try:
            if engine.is_interrupted:
                yield from self._finish(engine)
                return

            self._settle_stateful(engine)
            # Examples and coverage enumerate the schema once; repeating them re-sends the same cases.
            # Fuzzing and stateful keep drawing new ones, so only they are worth another turn.
            self.elastic = [phase for phase in self.phases if phase.name in ELASTIC_PHASES]
            yield from self._run_bounded(engine)
            yield from self._run_cycles(engine)
        except KeyboardInterrupt:
            engine.stop()
            yield events.Interrupted(phase=None)
            # Ctrl-C outside a phase's own handling, e.g. while the phase sets up, must still close the phase
            if self.unfinished is not None:
                yield events.PhaseFinished(phase=self.unfinished, status=Status.INTERRUPTED, payload=None)

        # Always finish
        yield from self._finish(engine)

    def _run_bounded(self, engine: EngineContext) -> EventGenerator:
        """Run each phase that enumerates the schema, once."""
        for phase in self.phases:
            if phase.name in ELASTIC_PHASES:
                continue
            yield from self._run_once(engine, phase)
            if engine.is_interrupted:
                break

    def _run_once(self, engine: EngineContext, phase: Phase) -> EventGenerator:
        """Run a bounded phase; coverage then regenerates what feedback has taught it since."""
        if phase.name != PhaseName.COVERAGE:
            yield from self._run_phase(engine, phase)
            return
        # Feedback recorded before the pass is already folded into what it generates.
        self.taught.update(self._feedback_counts(engine))
        yield from self._run_phase(engine, phase)
        if not engine.is_interrupted:
            yield from self._coverage_catchup(engine)
            yield from self._send_held_back(engine)

    def _run_cycles(self, engine: EngineContext) -> EventGenerator:
        """Give the elastic phases another turn until the time budget runs out or a turn sends nothing."""
        cycle = 0
        while not engine.is_interrupted:
            engine.cycle_index = cycle
            sent = yield from self._run_cycle(engine)
            # A cycle that sent nothing, e.g. because every input was sent before, will send nothing next time either.
            if engine.has_to_stop or engine.control.max_time is None or not sent:
                break
            cycle += 1

    def _run_cycle(self, engine: EngineContext) -> Generator[events.EngineEvent, None, bool]:
        """Give each elastic phase one turn; whether any of them sent a request."""
        before = self.requests.total(ELASTIC_PHASES)
        idle = []
        for phase in self.elastic:
            if engine.is_interrupted:
                break
            if (yield from self._take_turn(engine, phase)):
                idle.append(phase)
        # A phase that skipped has nothing to run; holding budget back for it starves the rest.
        self.elastic = [phase for phase in self.elastic if phase not in idle]
        return self.requests.total(ELASTIC_PHASES) > before

    def _take_turn(self, engine: EngineContext, phase: Phase) -> Generator[events.EngineEvent, None, bool]:
        """Run an elastic phase, then catch coverage up; whether the phase proved idle."""
        idle = False
        for event in self._run_phase(engine, phase):
            idle = idle or _is_idle(phase, event)
            yield event
        if not engine.is_interrupted:
            yield from self._coverage_catchup(engine)
            yield from self._send_held_back(engine)
        return idle

    def _coverage_catchup(self, engine: EngineContext) -> EventGenerator:
        """Regenerate coverage for operations that have started explaining their rejections."""
        if engine.control.max_time is None or engine.has_to_stop:
            return
        coverage = next((item for item in self.phases if item.name == PhaseName.COVERAGE), None)
        if coverage is None or not coverage.is_enabled:
            return
        # Satisfying one constraint can reveal the next, so keep going while the pass keeps teaching.
        while self.catchup_passes < MAX_COVERAGE_CATCHUP_PASSES and not engine.has_to_stop:
            counts = self._feedback_counts(engine)
            learned = {label for label, count in counts.items() if count > self.taught.get(label, 0)}
            if not learned:
                return
            self.taught.update(counts)
            self.catchup_passes += 1
            yield from self._run_phase(engine, coverage, only=frozenset(learned))

    def _send_held_back(self, engine: EngineContext) -> EventGenerator:
        """Send the coverage cases a server may not survive, once every other coverage case went out."""
        held_back = engine.held_back_cases
        if not held_back or engine.has_to_stop:
            return
        engine.held_back_cases = {}
        coverage = next(item for item in self.phases if item.name == PhaseName.COVERAGE)
        yield from self._run_phase(engine, coverage, only=frozenset(held_back), held_back=held_back)

    def _feedback_counts(self, engine: EngineContext) -> dict[str, int]:
        store = engine.error_feedback
        return store.observation_counts() if store is not None else {}

    def _run_phase(
        self,
        engine: EngineContext,
        phase: Phase,
        *,
        only: frozenset[str] | None = None,
        held_back: Mapping[str, list[Case]] | None = None,
    ) -> EventGenerator:
        try:
            payload = self._adapt_execution(engine, phase)
        except HookExecutionError as exc:
            yield from events.fail_phase(phase, exc, label=f"`{exc.hook_name}` hook")
            return
        started = events.PhaseStarted(phase=phase, payload=payload, operations=only)
        # Marked only once the start event exists, so an interrupt never finishes a phase that did not start
        self.unfinished = phase
        yield started
        engine.reserve_time(self._time_to_reserve_after(phase, engine))
        if phase.should_execute(engine):
            # Never bind this generator to a local: an error thrown at the yield must close it and stop its workers
            for event in run.execute(engine, phase, only=only, held_back=held_back):
                if isinstance(event, events.NonFatalError):
                    key = (event.info.title, event.info.message)
                    if event.related_to_operation:
                        self.operation_errors.add(key)
                    # Skip run-level errors already shown on an operation.
                    elif key in self.operation_errors:
                        continue
                self.requests.fold(event)
                if isinstance(event, events.PhaseFinished):
                    self.unfinished = None
                yield event
        else:
            if engine.has_reached_the_failure_limit:
                phase.skip_reason = PhaseSkipReason.FAILURE_LIMIT_REACHED
            elif phase.is_enabled and engine.control.is_server_unavailable:
                phase.skip_reason = PhaseSkipReason.SERVER_UNAVAILABLE
            self.unfinished = None
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
        # Skip after_run on a partial run (interrupt/abort). Spending the whole time budget is a planned finish,
        # and continuous fuzzing has no natural end, so stopping it is how every such run finishes.
        completed = ctx.stop_reason in (StopReason.COMPLETED, StopReason.MAX_TIME) or any(
            phase.name is PhaseName.CONTINUOUS_FUZZING for phase in self.phases
        )
        failures, skipped_checks = run_after_run_checks(ctx, completed=completed)
        if defines_teardown(ctx.schema):
            teardown = Phase(PhaseName.TEARDOWN)
            yield events.PhaseStarted(phase=teardown, payload=None)
            yield from run.execute(ctx, teardown)
        yield from _shutdown_applications(ctx.schema.app)
        yield events.EngineFinished(
            running_time=ctx.running_time,
            stop_reason=ctx.stop_reason,
            payload=summary,
            failures=failures,
            skipped_checks=skipped_checks,
        )

    def _adapt_execution(self, engine: EngineContext, phase: Phase) -> StatefulPhasePayload | None:
        if engine.has_reached_the_failure_limit:
            phase.skip_reason = PhaseSkipReason.FAILURE_LIMIT_REACHED
        # Phase can be enabled if certain conditions are met
        if phase.name == PhaseName.STATEFUL_TESTING:
            inference = engine.apply_stateful_inference()
            # Enable stateful testing if links were only missing statically, not disabled by the user
            if inference.inferred and phase.skip_reason in (None, PhaseSkipReason.NOT_APPLICABLE):
                phase.enable()
            return StatefulPhasePayload(
                inferred_transitions=inference.inferred,
                transitions_total=inference.total,
                transitions_selected=inference.selected,
            )
        return None


def _is_idle(phase: Phase, event: events.EngineEvent) -> bool:
    """Whether the event ends a phase that had nothing to run."""
    return (
        isinstance(event, events.PhaseFinished)
        and event.status == Status.SKIP
        # Requests went out; a later cycle may still reach operations its checks apply to.
        and phase.skip_reason is not PhaseSkipReason.NO_CHECKS_RAN
    )


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
