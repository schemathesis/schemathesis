from __future__ import annotations  # noqa: I001

import queue
import time
import unittest
from typing import TYPE_CHECKING, Any
from warnings import catch_warnings, filterwarnings

import hypothesis
import requests
from hypothesis import reject
from hypothesis.control import current_build_context
from hypothesis.errors import Flaky, HypothesisWarning, Unsatisfiable, UnsatisfiedAssumption
from hypothesis.stateful import Rule
from requests.exceptions import ChunkedEncodingError

from schemathesis.checks import CheckContext, CheckFunction, run_checks
from schemathesis.core.cache import Kind, request_from_case
from schemathesis.core.error_feedback import observation_fingerprint
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
    UnrecoverableNetworkError,
    clear_hypothesis_notes,
    is_unrecoverable_network_error,
)
from schemathesis.engine.run import PhaseName
from schemathesis.engine._rate_limit_retry import call_and_validate_with_retry
from schemathesis.engine.run.stateful.context import StatefulContext
from schemathesis.engine.recorder import ScenarioRecorder
from schemathesis.generation import overrides
from schemathesis.generation.case import Case
from schemathesis.generation.hypothesis.reporting import ignore_hypothesis_output
from schemathesis.generation.stateful import STATEFUL_TESTS_LABEL
from schemathesis.generation.stateful.state_machine import (
    DEFAULT_STATE_MACHINE_SETTINGS,
    APIStateMachine,
    StepInput,
    StepOutput,
)
from schemathesis.generation.metrics import MetricCollector
from schemathesis.specs.openapi.stateful.link_calibration import record_link_outcome

if TYPE_CHECKING:
    from schemathesis.core.error_feedback.store import Observation
    from schemathesis.resources import ExtraDataSource


def _replay_recorders_into_pool(extra_data_source: ExtraDataSource, recorders: list[ScenarioRecorder]) -> None:
    """Feed every captured interaction from this suite into the pool.

    Mirrors `record_extra_data_from_recorder` in the unit phase: the pool stays frozen during
    Hypothesis runs (shrinking sees a stable strategy) and is refreshed at suite boundaries.
    """
    for recorder in recorders:
        for case_id, interaction in recorder.interactions.items():
            response = interaction.response
            if response is None:
                continue
            case = recorder.cases[case_id].value
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
    if settings.stateful_step_count == hypothesis_default.stateful_step_count:
        kwargs["stateful_step_count"] = DEFAULT_STATE_MACHINE_SETTINGS.stateful_step_count
    if settings.deadline == hypothesis_default.deadline:
        kwargs["deadline"] = DEFAULT_STATE_MACHINE_SETTINGS.deadline
    if settings.suppress_health_check == hypothesis_default.suppress_health_check:
        kwargs["suppress_health_check"] = DEFAULT_STATE_MACHINE_SETTINGS.suppress_health_check
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


def execute_state_machine_loop(
    *,
    state_machine: type[APIStateMachine],
    event_queue: queue.Queue,
    engine: EngineContext,
) -> None:
    """Execute the state machine testing loop."""
    configured_hypothesis_settings = engine.config.get_hypothesis_settings(phase="stateful")
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
            override = overrides.for_operation(engine.config, operation=case.operation)
            for location in ("query", "headers", "cookies", "path_parameters"):
                entry = getattr(override, location)
                if entry:
                    container = getattr(case, location) or {}
                    container.update(entry)
                    setattr(case, location, container)
            return super().before_call(case)

        def step(self, input: StepInput) -> StepOutput | None:
            # _current_input is set here and consumed once in validate_response(), then cleared.
            # validate_response() is called at most once per step by the Hypothesis state machine.
            self._current_input = input
            # Checking the stop event once inside `step` is sufficient as it is called frequently
            # The idea is to stop the execution as soon as possible
            if engine.has_to_stop:
                raise KeyboardInterrupt

            operation_label = input.case.operation.label
            use_probability = engine.health.frozen_use_probability(operation_label)
            # Always draw — keeps data-tree topology stable across replays as `use_probability` transitions from 1.0 to <1.0.
            if not current_build_context().data.draw_boolean(p=use_probability):
                reject()

            try:
                if generation.unique_inputs:
                    cached = ctx.get_step_outcome(input.case)
                    if isinstance(cached, BaseException):
                        raise cached
                    elif cached is None:
                        return None
                self.before_call(input.case)
                kwargs = self.get_call_kwargs(input.case)
                auto_mode = engine.config.rate_limit_for(operation=input.case.operation) == "auto"

                def call_fn() -> Response:
                    r = self.call(input.case, **kwargs)
                    self.after_call(r, input.case)
                    return r

                final_response = call_and_validate_with_retry(
                    call_fn=call_fn,
                    validate_fn=lambda r: self.validate_response(r, input.case),
                    auto_mode=auto_mode,
                    on_delay=lambda delay, retries_left: event_queue.put(
                        events.RateLimitRetry(
                            operation=input.case.operation.label,
                            delay=delay,
                            retries_left=retries_left,
                        )
                    ),
                )
                result = StepOutput(final_response, input.case)
                ctx.step_succeeded()
                engine.health.record_completion(operation_label=operation_label)
            except UnsatisfiedAssumption:
                raise
            except FailureGroup as exc:
                engine.health.record_completion(operation_label=operation_label)
                if generation.unique_inputs:
                    for failure in exc.exceptions:
                        ctx.store_step_outcome(input.case, failure)
                ctx.step_failed()
                raise
            except Exception as exc:
                # A timeout is per-request: a slow operation shouldn't abort the phase. Connection-level
                # failures (reset, chunked-encoding break) usually mean the server crashed; surface
                # those immediately on the first occurrence.
                if isinstance(
                    exc, requests.ConnectionError | ChunkedEncodingError | requests.Timeout
                ) and is_unrecoverable_network_error(exc):
                    now = time.monotonic()
                    engine.health.record_transport_failure(operation_label=operation_label, now=now)
                    reason: str | None = None
                    if isinstance(exc, requests.Timeout):
                        reason = engine.health.abort_reason(now=now)
                        if reason is None:
                            raise UnsatisfiedAssumption("transport failure absorbed by health monitor") from exc
                    transport_kwargs = engine.get_transport_kwargs(operation=input.case.operation)
                    if exc.request is not None:
                        headers = dict(exc.request.headers)
                    else:
                        headers = {**dict(input.case.headers or {}), **transport_kwargs.get("headers", {})}
                    verify = transport_kwargs.get("verify", True)
                    state.store_unrecoverable_network_error(
                        UnrecoverableNetworkError(
                            error=exc,
                            code_sample=input.case.as_curl_command(headers=headers, verify=verify),
                            reason=reason,
                        )
                    )

                if generation.unique_inputs:
                    ctx.store_step_outcome(input.case, exc)
                ctx.step_errored()
                raise
            except KeyboardInterrupt:
                ctx.step_interrupted()
                raise
            except BaseException as exc:
                if generation.unique_inputs:
                    ctx.store_step_outcome(input.case, exc)
                raise exc
            else:
                if generation.unique_inputs:
                    ctx.store_step_outcome(input.case, None)
            return result

        def validate_response(
            self, response: Response, case: Case, additional_checks: tuple[CheckFunction, ...] = (), **kwargs: Any
        ) -> None:
            ctx.collect_metric(case, response)
            current_input = self._current_input
            self._current_input = None

            # Parse 4xx body once — reused by calibration and error-feedback.
            observations: tuple[Observation, ...] = ()
            if engine.error_feedback is not None or engine.link_calibration is not None:
                observations = parse_observations(case.operation, case, response)

            # Record this step's outcome against the link's score.
            if current_input is not None and engine.link_calibration is not None:
                record_link_outcome(engine.link_calibration, response, observations, current_input, self.recorder)
            ctx.current_response = response

            if engine.error_feedback is not None:
                # Field-level observations steer subsequent positive-mode generation.
                if observations:
                    for observation in observations:
                        engine.error_feedback.record(observation)
                    engine.cache.record(
                        Kind.ERROR_FEEDBACK,
                        case.operation.label,
                        request_from_case(case),
                        observation_keys=[observation_fingerprint(observation) for observation in observations],
                    )
                # Schema-level cross-cutting observations (e.g. auth retries).
                case.operation.schema.record_runtime_observations(
                    store=engine.error_feedback,
                    recorder=self.recorder,
                    case=case,
                    response=response,
                    transport_kwargs=engine.get_transport_kwargs(operation=case.operation),
                    cache_writer=engine.cache.writer,
                )

            cached = check_context_cache.get_or_create(operation=case.operation, ctx=engine, phase="stateful")

            check_ctx = CheckContext(
                override=cached.override,
                auth=cached.auth,
                headers=cached.headers,
                config=cached.config,
                transport_kwargs=cached.transport_kwargs,
                recorder=self.recorder,
                response_checks=engine.checks.for_responses(),
                phase=PhaseName.STATEFUL_TESTING,
            )
            validate_response(
                response=response,
                case=case,
                stateful_ctx=ctx,
                check_ctx=check_ctx,
                checks=check_ctx._checks,
                control=engine.control,
                recorder=self.recorder,
                additional_checks=additional_checks,
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

    seed = engine.config.seed

    while True:
        # Promote observations from the previous run into the stable read state.
        if engine.link_calibration is not None:
            engine.link_calibration.begin_iteration()
        engine.health.begin_iteration()
        suite_recorders.clear()
        # This loop is running until no new failures are found in a single iteration
        if engine.error_feedback is not None:
            engine.error_feedback.checkpoint()
        suite_started = events.SuiteStarted(phase=PhaseName.STATEFUL_TESTING)
        suite_id = suite_started.id
        event_queue.put(suite_started)
        if engine.is_interrupted:
            event_queue.put(events.Interrupted(phase=PhaseName.STATEFUL_TESTING))
            event_queue.put(
                events.SuiteFinished(
                    id=suite_started.id,
                    phase=PhaseName.STATEFUL_TESTING,
                    status=Status.INTERRUPTED,
                )
            )
            break
        suite_status = Status.SUCCESS
        InstrumentedStateMachine = hypothesis.seed(seed)(_InstrumentedStateMachine)
        # Predictably change the seed to avoid re-running the same sequences if tests fail
        # yet have reproducible results
        seed += 1
        try:
            with catch_warnings(), ignore_hypothesis_output():
                filterwarnings("ignore", category=HypothesisWarning, message="Generating overly large repr")
                InstrumentedStateMachine.run(settings=hypothesis_settings)
        except KeyboardInterrupt:
            # Raised in the state machine when the stop event is set or it is raised by the user's code
            # that is placed in the base class of the state machine.
            # Therefore, set the stop event to cover the latter case
            engine.stop()
            suite_status = Status.INTERRUPTED
            event_queue.put(events.Interrupted(phase=PhaseName.STATEFUL_TESTING))
            break
        except unittest.case.SkipTest:
            # If `explicit` phase is used and there are no examples
            suite_status = Status.SKIP
            break
        except FailureGroup as exc:
            # When a check fails, the state machine is stopped
            # The failure is already sent to the queue by the state machine
            # Here we need to either exit or re-run the state machine with this failure marked as known
            suite_status = Status.FAILURE
            if engine.has_reached_the_failure_limit:
                break
            for failure in exc.exceptions:
                ctx.mark_as_seen_in_run(failure)
            continue
        except Flaky:
            # Ignore flakiness
            if engine.has_reached_the_failure_limit:
                break
            stored = state.unrecoverable_network_error
            if stored is not None:
                # Flakiness caused by a transient transport failure: surface it and stop rather than
                # restarting the whole suite — a replayed drop won't reproduce, so re-running is wasted.
                suite_status = Status.ERROR
                event_queue.put(_network_nonfatal_error(stored))
                break
            # Mark all failures in this suite as seen to prevent them being re-discovered
            ctx.mark_current_suite_as_seen_in_run()
            continue
        except Exception as exc:
            if isinstance(exc, Unsatisfiable) and ctx.completed_scenarios > 0:
                # Sometimes Hypothesis randomly gives up on generating some complex cases. However, if we know that
                # values are possible to generate based on the previous observations, we retry the generation
                if ctx.completed_scenarios >= hypothesis_settings.max_examples:
                    # Avoid infinite restarts
                    break
                continue
            clear_hypothesis_notes(exc)
            # Any other exception is an inner error and the test run should be stopped
            suite_status = Status.ERROR
            stored = state.unrecoverable_network_error
            if stored is not None:
                event_queue.put(_network_nonfatal_error(stored))
            else:
                event_queue.put(
                    events.NonFatalError(
                        error=exc,
                        phase=PhaseName.STATEFUL_TESTING,
                        label=STATEFUL_TESTS_LABEL,
                        related_to_operation=False,
                        code_sample=None,
                    )
                )
            break
        finally:
            # Drain this suite's recorders into the pool before the next iteration's strategies
            # are built; mirrors `record_extra_data_from_recorder` in the unit phase.
            if engine.extra_data_source is not None and suite_recorders:
                _replay_recorders_into_pool(engine.extra_data_source, suite_recorders)
            event_queue.put(
                events.SuiteFinished(
                    id=suite_started.id,
                    phase=PhaseName.STATEFUL_TESTING,
                    status=suite_status,
                )
            )
            ctx.reset()
        # Exit on the first successful state machine execution
        break


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
) -> None:
    """Validate the response against the provided checks."""

    def on_failure(name: str, collected: set[Failure], failure: Failure) -> None:
        if stateful_ctx.is_seen_in_suite(failure) or stateful_ctx.is_seen_in_run(failure):
            return
        failure_data = recorder.find_failure_data(parent_id=case.id, failure=failure)

        # Collect the chain of cURL commands needed to reproduce the failure.
        # Some failures (e.g. use-after-free) reference a prior case that may live on a
        # sibling branch; include it so the reproduce isn't missing the triggering step.
        related_case_ids = failure.related_case_ids()
        commands = [
            chain_case.as_curl_command(headers=failure_data.headers, verify=failure_data.verify)
            for chain_case in recorder.iter_chain_cases(case_id=failure_data.case.id, related_case_ids=related_case_ids)
        ]
        recorder.record_check_failure(
            name=name,
            case_id=failure_data.case.id,
            code_sample="\n".join(commands),
            failure=failure,
        )
        control.count_failure()
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

    if failures:
        raise FailureGroup(list(failures)) from None
