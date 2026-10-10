from __future__ import annotations  # noqa: I001

import queue
import time
import unittest
from typing import TYPE_CHECKING, Any, NoReturn, TypeGuard
from warnings import catch_warnings, filterwarnings

import hypothesis
import requests
from hypothesis import reject
from hypothesis.control import current_build_context
from hypothesis.errors import Flaky, HypothesisWarning, Unsatisfiable, UnsatisfiedAssumption
from hypothesis.stateful import Rule
from requests.exceptions import ChunkedEncodingError

from schemathesis.checks import CheckContext, CheckFunction, run_checks
from schemathesis.core.control import SkipTest
from schemathesis.core.error_feedback.collector import parse_observations
from schemathesis.core.failures import Failure, FailureGroup
from schemathesis.core.timing import Instant
from schemathesis.core.transport import Response
from schemathesis.engine import Status, events
from schemathesis.engine._check_context import CheckContextCache
from schemathesis.engine.context import EngineContext
from schemathesis.engine.control import ExecutionControl
from schemathesis.engine.errors import (
    TestingState,
    UnhealthyAPIError,
    UnresponsiveOperationError,
    UnrecoverableNetworkError,
    build_code_sample,
    clear_hypothesis_notes,
    is_inconsistent_replay,
    is_unrecoverable_network_error,
)
from schemathesis.engine.run import PhaseName
from schemathesis.engine.run.unit._case import BudgetExpired, ServerWentAway
from schemathesis.engine._baseline import is_known
from schemathesis.engine._rate_limit_retry import call_and_validate_with_retry
from schemathesis.engine.run.stateful.context import StatefulContext
from schemathesis.engine.recorder import ReproductionStep, ScenarioRecorder
from schemathesis.generation import overrides
from schemathesis.generation.stateful.state_machine import release_state_machines
from schemathesis.generation.case import Case
from schemathesis.generation.hypothesis.reporting import UnsatisfiableSchema, ignore_hypothesis_output
from schemathesis.generation.stateful import STATEFUL_TESTS_LABEL
from schemathesis.generation.stateful.state_machine import (
    DEFAULT_STATE_MACHINE_SETTINGS,
    APIStateMachine,
    StepInput,
    StepOutput,
)
from schemathesis.generation.metrics import MetricCollector

if TYPE_CHECKING:
    from collections.abc import Sequence

    from schemathesis.baseline import Baseline
    from schemathesis.core.error_feedback.store import Observation
    from schemathesis.resources import ResourceRecorder


def _replay_recorders_into_pool(extra_data_source: ResourceRecorder, recorders: list[ScenarioRecorder]) -> None:
    """Feed every captured interaction from this suite into the pool.

    Mirrors `record_extra_data_from_recorder` in the unit phase: the pool stays frozen during
    Hypothesis runs (shrinking sees a stable strategy) and is refreshed at suite boundaries.
    """
    for recorder in recorders:
        for case_id, interaction in recorder.interactions.items():
            if interaction.response is not None:
                _record_into_pool(extra_data_source, recorder.cases[case_id].value, interaction.response)


def _record_into_pool(extra_data_source: ResourceRecorder, case: Case, response: Response) -> None:
    operation = case.operation
    if extra_data_source.should_record(operation=operation.label):
        extra_data_source.record_response(operation=operation, response=response, case=case)
    if extra_data_source.should_record_request(operation=operation.label):
        extra_data_source.record_request(operation=operation, case=case, status_code=response.status_code)
    if 200 <= response.status_code < 300 or response.status_code == 404:
        extra_data_source.record_successful_delete(operation=operation, case=case)
    response.clear_cache()


def _get_hypothesis_settings_kwargs_override(settings: hypothesis.settings) -> dict[str, Any]:
    """Get the settings that should be overridden to match the defaults for API state machines."""
    kwargs = {}
    hypothesis_default = hypothesis.settings.get_profile("default")
    if settings.phases == hypothesis_default.phases:
        kwargs["phases"] = DEFAULT_STATE_MACHINE_SETTINGS.phases
    if settings.deadline == hypothesis_default.deadline:
        kwargs["deadline"] = DEFAULT_STATE_MACHINE_SETTINGS.deadline
    # Suppressing a subset must not re-enable the rest, hence the union rather than a swap
    kwargs["suppress_health_check"] = list(
        dict.fromkeys([*DEFAULT_STATE_MACHINE_SETTINGS.suppress_health_check, *settings.suppress_health_check])
    )
    return kwargs


def _network_nonfatal_error(stored: UnrecoverableNetworkError) -> events.NonFatalError:
    error = UnhealthyAPIError(stored.reason) if stored.reason is not None else stored.error
    return events.NonFatalError(
        error=error,
        phase=PhaseName.STATEFUL_TESTING,
        label=STATEFUL_TESTS_LABEL,
        related_to_operation=False,
        code_sample=stored.code_sample,
    )


# The suite status, whether to re-run the suite, and the events to emit.
SuiteOutcome = tuple[Status, bool, list[events.EngineEvent]]


def _network_error_outcome(state: TestingState) -> SuiteOutcome | None:
    """End the suite with the stored transport failure, if there is one."""
    stored = state.unrecoverable_network_error
    if stored is None:
        return None
    return Status.ERROR, False, [_network_nonfatal_error(stored)]


def _classify_failure_group(
    exc: FailureGroup, *, ctx: StatefulContext, engine: EngineContext, state: TestingState
) -> SuiteOutcome:
    # When a check fails, the state machine is stopped
    # The failure is already sent to the queue by the state machine
    # Here we need to either exit or re-run the state machine with this failure marked as known
    stored = state.take_unrecoverable_network_error()
    network_events: list[events.EngineEvent] = [_network_nonfatal_error(stored)] if stored is not None else []
    if engine.has_reached_the_failure_limit:
        return Status.FAILURE, False, network_events
    for failure in exc.exceptions:
        ctx.mark_as_seen_in_run(failure)
    return Status.FAILURE, True, network_events


def _classify_flaky(*, ctx: StatefulContext, engine: EngineContext, state: TestingState) -> SuiteOutcome:
    # A replay that cannot reproduce the failure does not unreport it: the suite already showed it.
    found = Status.FAILURE if ctx.has_new_failures_in_suite else Status.SUCCESS
    if engine.has_reached_the_failure_limit:
        return found, False, []
    # Flakiness caused by a transient transport failure: surface it and stop rather than
    # restarting the whole suite - a replayed drop won't reproduce, so re-running is wasted.
    network_outcome = _network_error_outcome(state)
    if network_outcome is not None:
        return network_outcome
    # Mark all failures in this suite as seen to prevent them being re-discovered
    ctx.mark_current_suite_as_seen_in_run()
    return found, True, []


def _classify_suite_error(
    exc: Exception | KeyboardInterrupt | FailureGroup | SkipTest,
    *,
    ctx: StatefulContext,
    engine: EngineContext,
    state: TestingState,
    settings: hypothesis.settings,
) -> SuiteOutcome:
    """Map a state machine failure into the suite status, whether to re-run it, and the events to emit."""
    if isinstance(exc, BudgetExpired):
        # The clock ended the suite. A failure found before it surfaces as its own group, so there is
        # nothing left to report here; the engine reports the limit itself.
        return Status.SUCCESS, False, []
    if isinstance(exc, ServerWentAway):
        return _network_error_outcome(state) or (Status.FAILURE if ctx.seen_in_suite else Status.SUCCESS, False, [])
    if isinstance(exc, KeyboardInterrupt):
        # Raised in the state machine when the stop event is set or it is raised by the user's code
        # that is placed in the base class of the state machine.
        # Therefore, set the stop event to cover the latter case
        engine.stop()
        return Status.INTERRUPTED, False, [events.Interrupted(phase=PhaseName.STATEFUL_TESTING)]
    if isinstance(exc, (SkipTest, unittest.case.SkipTest)):
        # If `explicit` phase is used and there are no examples
        return Status.SKIP, False, []
    if isinstance(exc, FailureGroup):
        return _classify_failure_group(exc, ctx=ctx, engine=engine, state=state)
    if isinstance(exc, Flaky) or is_inconsistent_replay(exc):
        return _classify_flaky(ctx=ctx, engine=engine, state=state)
    if isinstance(exc, Unsatisfiable) and not isinstance(exc, UnsatisfiableSchema) and ctx.completed_scenarios > 0:
        # Sometimes Hypothesis randomly gives up on generating some complex cases. However, if we know that
        # values are possible to generate based on the previous observations, we retry the generation,
        # unless that many scenarios already ran - then further restarts would never end
        return Status.SUCCESS, ctx.completed_scenarios < settings.max_examples, []
    clear_hypothesis_notes(exc)
    # Any other exception is an inner error and the test run should be stopped
    return _network_error_outcome(state) or (
        Status.ERROR,
        False,
        [
            events.NonFatalError(
                error=exc,
                phase=PhaseName.STATEFUL_TESTING,
                label=STATEFUL_TESTS_LABEL,
                related_to_operation=False,
                code_sample=None,
            )
        ],
    )


def _unresponsive_operation_error(
    exc: requests.ConnectionError | ChunkedEncodingError | requests.Timeout, *, case: Case, engine: EngineContext
) -> events.NonFatalError:
    health = engine.health.operations[case.operation.label]
    total = health.completed + health.transport_failures
    error = UnresponsiveOperationError(
        f"{health.transport_failures} of {total} requests failed while other operations answered; "
        "no further requests are sent to this operation"
    )
    transport_kwargs = engine.get_transport_kwargs(operation=case.operation)
    return events.NonFatalError(
        error=error,
        phase=PhaseName.STATEFUL_TESTING,
        label=case.operation.label,
        related_to_operation=True,
        code_sample=build_code_sample(case, exc.request, transport_kwargs),
    )


def _unrecoverable_network_error(
    exc: requests.ConnectionError | ChunkedEncodingError | requests.Timeout, *, case: Case, engine: EngineContext
) -> UnrecoverableNetworkError | None:
    """Describe a fatal transport failure, or `None` when the health monitor absorbs it."""
    reason: str | None = None
    if isinstance(exc, requests.Timeout):
        reason = engine.health.abort_reason(now=time.monotonic())
        if reason is None:
            return None
    transport_kwargs = engine.get_transport_kwargs(operation=case.operation)
    return UnrecoverableNetworkError(
        error=exc, code_sample=build_code_sample(case, exc.request, transport_kwargs), reason=reason
    )


def _raise_if_stopped(engine: EngineContext) -> None:
    """Stop the scenario as soon as possible once the engine is told to stop."""
    if engine.has_to_stop:
        # Say which one stopped it: a spent budget is a planned finish, Ctrl-C is not.
        if engine.has_reached_time_limit and not engine.is_interrupted:
            raise BudgetExpired
        raise KeyboardInterrupt


def _reject_unusable_operation(engine: EngineContext, operation_label: str) -> None:
    use_probability = engine.health.frozen_use_probability(operation_label)
    # Always draw - keeps data-tree topology stable across replays as `use_probability` transitions from 1.0 to <1.0.
    if not current_build_context().data.draw_boolean(p=use_probability):
        reject()
    if engine.health.is_unresponsive(operation_label):
        reject()


def _replay_cached_outcome(ctx: StatefulContext, case: Case) -> bool:
    """Re-raise the error this input produced before; `True` when it already passed."""
    cached = ctx.get_step_outcome(case)
    if isinstance(cached, BaseException):
        raise cached
    return cached is None


def _send_step(machine: APIStateMachine, case: Case, *, engine: EngineContext, event_queue: queue.Queue) -> Response:
    """Send the step's request and validate the response, waiting out rate limits when configured."""
    machine.before_call(case)
    kwargs = machine.get_call_kwargs(case)

    def call() -> Response:
        response = engine.server.track(case, lambda: machine.call(case, **kwargs), transport_kwargs=kwargs)
        machine.after_call(response, case)
        return response

    return call_and_validate_with_retry(
        call_fn=call,
        validate_fn=lambda response: machine.validate_response(response, case),
        auto_mode=engine.config.rate_limit_for(operation=case.operation) == "auto",
        on_delay=lambda delay, retries_left: event_queue.put(
            events.RateLimitRetry(operation=case.operation.label, delay=delay, retries_left=retries_left)
        ),
    )


def _detect_outage(exc: Exception, engine: EngineContext) -> bool:
    """Whether the server went away; an error that only hit the already dead server stops the scenario at once."""
    outage = isinstance(exc, requests.ConnectionError) and engine.detect_server_outage(exc)
    if outage and engine.server.is_after_outage(exc):
        raise ServerWentAway from None
    return outage


def _is_connection_failure(
    exc: Exception,
) -> TypeGuard[requests.ConnectionError | ChunkedEncodingError | requests.Timeout]:
    return isinstance(exc, (requests.ConnectionError, ChunkedEncodingError, requests.Timeout)) and (
        is_unrecoverable_network_error(exc)
    )


def _absorb_transport_failure(
    exc: requests.ConnectionError | ChunkedEncodingError | requests.Timeout,
    *,
    case: Case,
    engine: EngineContext,
    event_queue: queue.Queue,
) -> NoReturn:
    """Skip the step, reporting the operation once when it stops answering."""
    if engine.health.mark_unresponsive(case.operation.label):
        event_queue.put(_unresponsive_operation_error(exc, case=case, engine=engine))
    raise UnsatisfiedAssumption("transport failure absorbed by health monitor") from exc


def _record_network_error(
    exc: Exception, *, case: Case, engine: EngineContext, state: TestingState, event_queue: queue.Queue
) -> None:
    """Keep a connection-level failure for the suite report; a timeout the health monitor absorbs skips the step."""
    # A timeout is per-request: a slow operation shouldn't abort the phase. Connection-level failures
    # (reset, chunked-encoding break) usually mean the server crashed; surface those on the first occurrence.
    if not _is_connection_failure(exc):
        return
    network_error = _unrecoverable_network_error(exc, case=case, engine=engine)
    if network_error is None:
        _absorb_transport_failure(exc, case=case, engine=engine, event_queue=event_queue)
    state.store_unrecoverable_network_error(network_error)


def _remember_step_outcome(
    ctx: StatefulContext, case: Case, outcome: BaseException | None, *, unique_inputs: bool
) -> None:
    if unique_inputs:
        ctx.store_step_outcome(case, outcome)


def _remember_failures(
    ctx: StatefulContext, case: Case, failures: Sequence[BaseException], *, unique_inputs: bool
) -> None:
    for failure in failures:
        _remember_step_outcome(ctx, case, failure, unique_inputs=unique_inputs)


def _parse_step_observations(engine: EngineContext, case: Case, response: Response) -> tuple[Observation, ...]:
    """Parse the 4xx body once, for both link calibration and error feedback."""
    if engine.error_feedback is None and engine.link_calibration is None:
        return ()
    return parse_observations(case.operation, case, response)


def _calibrate_link(
    engine: EngineContext,
    response: Response,
    *,
    observations: tuple[Observation, ...],
    step_input: StepInput | None,
    recorder: ScenarioRecorder,
) -> None:
    """Record this step's outcome against the score of the link that produced it."""
    if step_input is None or engine.link_calibration is None:
        return
    engine.schema.record_link_outcome(
        calibration=engine.link_calibration,
        response=response,
        observations=observations,
        step_input=step_input,
        recorder=recorder,
    )


def _supervise_response(engine: EngineContext, case: Case, response: Response) -> None:
    engine.supervisor.record_response(
        operation_label=case.operation.label,
        status_code=response.status_code,
        is_documented_status=case.operation.responses.find_by_status_code(response.status_code) is not None,
        case=case,
        cache_writer=engine.cache.writer,
    )


def _feed_error_feedback(
    engine: EngineContext,
    case: Case,
    response: Response,
    *,
    observations: tuple[Observation, ...],
    recorder: ScenarioRecorder,
) -> None:
    if engine.error_feedback is None:
        return
    engine.record_error_feedback(
        case=case,
        response=response,
        recorder=recorder,
        observations=observations,
        transport_kwargs=engine.get_transport_kwargs(operation=case.operation),
    )


def _validate_step_response(
    response: Response,
    case: Case,
    *,
    step_input: StepInput | None,
    recorder: ScenarioRecorder,
    additional_checks: tuple[CheckFunction, ...],
    engine: EngineContext,
    ctx: StatefulContext,
    check_context_cache: CheckContextCache,
) -> None:
    """Feed the response to link calibration, the supervisor and error feedback, then run the checks."""
    ctx.collect_metric(case, response)
    observations = _parse_step_observations(engine, case, response)
    _calibrate_link(engine, response, observations=observations, step_input=step_input, recorder=recorder)
    ctx.current_response = response
    _supervise_response(engine, case, response)
    _feed_error_feedback(engine, case, response, observations=observations, recorder=recorder)
    check_ctx = check_context_cache.get_or_create(
        operation=case.operation, ctx=engine, phase="stateful"
    ).to_check_context(
        recorder=recorder,
        response_checks=engine.checks.for_responses(),
        phase=PhaseName.STATEFUL_TESTING,
        auth_enforced_operations=engine.auth_enforced_operations,
    )
    validate_response(
        response=response,
        case=case,
        stateful_ctx=ctx,
        check_ctx=check_ctx,
        checks=check_ctx._checks,
        control=engine.control,
        recorder=recorder,
        additional_checks=additional_checks,
        baseline=engine.config.load_baseline(),
    )


def _begin_suite(engine: EngineContext, suite_recorders: list[ScenarioRecorder]) -> None:
    """Promote observations from the previous suite into the stable read state."""
    if engine.link_calibration is not None:
        engine.link_calibration.begin_iteration()
    engine.health.begin_iteration()
    suite_recorders.clear()
    if engine.error_feedback is not None:
        engine.error_feedback.checkpoint()


def _drain_suite_recorders(engine: EngineContext, suite_recorders: list[ScenarioRecorder]) -> None:
    """Feed the suite's recorders into the pool before the next suite builds its strategies."""
    # Mirrors `record_extra_data_from_recorder` in the unit phase.
    if engine.extra_data_source is not None and suite_recorders:
        _replay_recorders_into_pool(engine.extra_data_source, suite_recorders)


def _close_suite_early(engine: EngineContext, event_queue: queue.Queue, suite_started: events.SuiteStarted) -> bool:
    """Finish the suite before any scenario when the run is interrupted or out of time."""
    if engine.is_interrupted:
        event_queue.put(events.Interrupted(phase=PhaseName.STATEFUL_TESTING))
        event_queue.put(
            events.SuiteFinished(id=suite_started.id, phase=PhaseName.STATEFUL_TESTING, status=Status.INTERRUPTED)
        )
        return True
    if engine.has_reached_time_limit:
        # No scenario ran, so there is nothing this suite can vouch for.
        event_queue.put(events.SuiteFinished(id=suite_started.id, phase=PhaseName.STATEFUL_TESTING, status=Status.SKIP))
        return True
    return False


def _suite_seed(engine: EngineContext, settings: hypothesis.settings) -> int | None:
    """A fresh seed per suite: a retry or a later cycle must not replay an earlier suite."""
    # Deterministic mode skips a generated seed, since any seed takes precedence over `derandomize`.
    return engine.next_stateful_seed() if not settings.derandomize or engine.config.has_explicit_seed else None


def _run_step(
    machine: APIStateMachine,
    case: Case,
    *,
    engine: EngineContext,
    ctx: StatefulContext,
    state: TestingState,
    event_queue: queue.Queue,
    unique_inputs: bool,
) -> StepOutput | None:
    """Send one scenario step and record its outcome; `None` when this input already passed."""
    _raise_if_stopped(engine)
    _reject_unusable_operation(engine, case.operation.label)
    try:
        if unique_inputs and _replay_cached_outcome(ctx, case):
            return None
        response = _send_step(machine, case, engine=engine, event_queue=event_queue)
        result = StepOutput(response, case)
        ctx.step_succeeded()
    except UnsatisfiedAssumption:
        raise
    except FailureGroup as exc:
        _remember_failures(ctx, case, exc.exceptions, unique_inputs=unique_inputs)
        ctx.step_failed()
        raise
    except Exception as exc:
        outage = _detect_outage(exc, engine)
        _record_network_error(exc, case=case, engine=engine, state=state, event_queue=event_queue)
        _remember_step_outcome(ctx, case, exc, unique_inputs=unique_inputs)
        ctx.step_errored()
        if outage:
            raise ServerWentAway from None
        raise
    except KeyboardInterrupt:
        ctx.step_interrupted()
        raise
    except BaseException as exc:
        _remember_step_outcome(ctx, case, exc, unique_inputs=unique_inputs)
        raise exc
    else:
        _remember_step_outcome(ctx, case, None, unique_inputs=unique_inputs)
    return result


def execute_state_machine_loop(
    *,
    state_machine: type[APIStateMachine],
    event_queue: queue.Queue,
    engine: EngineContext,
) -> None:
    """Execute the state machine testing loop."""
    configured_hypothesis_settings = engine.config.get_hypothesis_settings(phase="stateful", apply_ci_profile=False)
    kwargs = _get_hypothesis_settings_kwargs_override(configured_hypothesis_settings)
    hypothesis_settings = hypothesis.settings(configured_hypothesis_settings, **kwargs)
    generation = engine.config.generation_for(phase="stateful")

    ctx = StatefulContext(metric_collector=MetricCollector(metrics=generation.maximize))
    state = TestingState()

    check_context_cache = CheckContextCache()
    # Recorders from every scenario in the current suite. The pool stays frozen during the
    # suite (so Hypothesis shrinking sees a stable strategy); writes are replayed once the
    # suite finishes, before the next iteration's strategies are built.
    suite_recorders: list[ScenarioRecorder] = []

    class _InstrumentedStateMachine(state_machine):  # type: ignore[valid-type,misc]
        """State machine with additional hooks for emitting events."""

        def __init__(self) -> None:
            super().__init__()
            # The state machine creates a fresh `TransitionController` per scenario.
            # Inject the engine's supervisor so transitions targeting operations with
            # a SKIP verdict (consistently-405 operations detected during the unit
            # phases) are filtered out of rule preconditions before Hypothesis selects
            # them.
            self.control.supervisor = engine.supervisor

        def setup(self) -> None:
            self._current_input: StepInput | None = None
            scenario_started = events.ScenarioStarted(label=None, phase=PhaseName.STATEFUL_TESTING, suite_id=suite_id)
            self._started_at = Instant()
            self._scenario_id = scenario_started.id
            event_queue.put(scenario_started)

        def get_call_kwargs(self, case: Case) -> dict[str, Any]:
            return engine.get_transport_kwargs(operation=case.operation)

        def _repr_step(self, rule: Rule, data: dict, result: StepOutput) -> str:
            return ""

        def before_call(self, case: Case) -> None:
            overrides.for_operation(engine.config, operation=case.operation).apply_to(case)
            return super().before_call(case)

        def step(self, input: StepInput) -> StepOutput | None:
            # _current_input is set here and consumed once in validate_response(), then cleared.
            # validate_response() is called at most once per step by the Hypothesis state machine.
            self._current_input = input
            return _run_step(
                self,
                input.case,
                engine=engine,
                ctx=ctx,
                state=state,
                event_queue=event_queue,
                unique_inputs=generation.unique_inputs,
            )

        def validate_response(
            self, response: Response, case: Case, additional_checks: tuple[CheckFunction, ...] = (), **kwargs: Any
        ) -> None:
            step_input = self._current_input
            self._current_input = None
            _validate_step_response(
                response,
                case,
                step_input=step_input,
                recorder=self.recorder,
                additional_checks=additional_checks,
                engine=engine,
                ctx=ctx,
                check_context_cache=check_context_cache,
            )

        def teardown(self) -> None:
            build_ctx = current_build_context()
            event_queue.put(
                events.ScenarioFinished(
                    id=self._scenario_id,
                    suite_id=suite_id,
                    phase=PhaseName.STATEFUL_TESTING,
                    label=None,
                    status=ctx.current_scenario_status or Status.SKIP,
                    recorder=self.recorder,
                    elapsed_time=self._started_at.elapsed,
                    skip_reason=None,
                    is_final=build_ctx.is_final,
                )
            )
            if engine.extra_data_source is not None:
                suite_recorders.append(self.recorder)
            ctx.maximize_metrics()
            ctx.reset_scenario()
            super().teardown()

    try:
        while True:
            # This loop is running until no new failures are found in a single iteration
            _begin_suite(engine, suite_recorders)
            suite_started = events.SuiteStarted(phase=PhaseName.STATEFUL_TESTING)
            suite_id = suite_started.id
            event_queue.put(suite_started)
            if _close_suite_early(engine, event_queue, suite_started):
                break
            suite_status = Status.SUCCESS
            retry = False
            InstrumentedStateMachine = hypothesis.seed(_suite_seed(engine, hypothesis_settings))(
                _InstrumentedStateMachine
            )
            try:
                with catch_warnings(), ignore_hypothesis_output():
                    filterwarnings("ignore", category=HypothesisWarning, message="Generating overly large repr")
                    InstrumentedStateMachine.run(settings=hypothesis_settings)
            except (Exception, KeyboardInterrupt, FailureGroup, SkipTest) as exc:
                suite_status, retry, error_events = _classify_suite_error(
                    exc, ctx=ctx, engine=engine, state=state, settings=hypothesis_settings
                )
                for error_event in error_events:
                    event_queue.put(error_event)
            finally:
                _drain_suite_recorders(engine, suite_recorders)
                event_queue.put(
                    events.SuiteFinished(
                        id=suite_started.id,
                        phase=PhaseName.STATEFUL_TESTING,
                        status=suite_status,
                    )
                )
                ctx.reset()
            if retry:
                continue
            # One clean pass, then hand the budget back: under a time limit the engine repeats the whole
            # sequence, and holding on here would leave every later phase without a turn.
            break

    finally:
        # Each cycle builds new classes; Hypothesis would otherwise keep every finished one alive.
        release_state_machines(_InstrumentedStateMachine, state_machine)


def validate_response(
    *,
    response: Response,
    case: Case,
    stateful_ctx: StatefulContext,
    check_ctx: CheckContext,
    control: ExecutionControl,
    checks: list[CheckFunction],
    recorder: ScenarioRecorder,
    additional_checks: tuple[CheckFunction, ...] = (),
    baseline: Baseline | None = None,
) -> None:
    """Validate the response against the provided checks."""
    checked_by: dict[Failure, str] = {}

    def on_failure(name: str, collected: set[Failure], failure: Failure) -> None:
        checked_by.setdefault(failure, name)
        if stateful_ctx.is_seen_in_suite(failure) or stateful_ctx.is_seen_in_run(failure):
            return
        failure_data = recorder.find_failure_data(parent_id=case.id, failure=failure)

        # Collect the chain of cURL commands needed to reproduce the failure.
        # Some failures (e.g. use-after-free) reference a prior case that may live on a
        # sibling branch; include it so the reproduce isn't missing the triggering step.
        related_case_ids = failure.related_case_ids()
        # Each step is rendered with the headers it sent itself, so the chain reproduces the run as it happened.
        steps = [
            ReproductionStep.from_case(
                chain_case, headers=recorder.find_request_headers(case_id=chain_case.id), verify=failure_data.verify
            )
            for chain_case in recorder.iter_chain_cases(case_id=failure_data.case.id, related_case_ids=related_case_ids)
        ]
        recorder.record_check_failure(
            name=name,
            case_id=failure_data.case.id,
            code_sample="\n".join(step.curl for step in steps),
            failure=failure,
            steps=steps,
        )
        # Known failures are accepted debt, so they must not spend the `--max-failures` budget.
        if not is_known(failure, name, baseline):
            control.count_failure(failure)
            stateful_ctx.has_new_failures_in_suite = True
        stateful_ctx.mark_as_seen_in_suite(failure)
        collected.add(failure)

    def on_success(name: str, case: Case) -> None:
        recorder.record_check_success(name=name, case_id=case.id)

    failures = run_checks(
        case=case,
        response=response,
        ctx=check_ctx,
        checks=tuple(checks) + tuple(additional_checks),
        on_failure=on_failure,
        on_success=on_success,
    )

    new = [failure for failure in failures if not is_known(failure, checked_by[failure], baseline)]
    if new:
        raise FailureGroup(new) from None
