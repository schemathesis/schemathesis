from __future__ import annotations  # noqa: I001

import queue
import time
import unittest
from dataclasses import dataclass
from typing import Any
from warnings import catch_warnings

import hypothesis
import requests
from hypothesis.control import current_build_context
from hypothesis.errors import Flaky, Unsatisfiable
from hypothesis.stateful import Rule
from requests.exceptions import ChunkedEncodingError
from requests.structures import CaseInsensitiveDict

from schemathesis.checks import CheckContext, CheckFunction, run_checks
from schemathesis.core.failures import Failure, FailureGroup
from schemathesis.core.transport import Response
from schemathesis.engine import Status, events
from schemathesis.engine.context import EngineContext
from schemathesis.engine.control import ExecutionControl
from schemathesis.engine.errors import (
    TestingState,
    UnrecoverableNetworkError,
    clear_hypothesis_notes,
    is_unrecoverable_network_error,
)
from schemathesis.engine.phases import PhaseName
from schemathesis.engine.phases.stateful.context import StatefulContext
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


@dataclass
class CachedCheckContextData:
    override: Any
    auth: Any
    headers: Any
    config: Any
    transport_kwargs: Any

    __slots__ = ("override", "auth", "headers", "config", "transport_kwargs")


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

    # Caches for validate_response to avoid repeated config lookups per operation
    _check_context_cache: dict[str, CachedCheckContextData] = {}

    class _InstrumentedStateMachine(state_machine):  # type: ignore[valid-type,misc]
        """State machine with additional hooks for emitting events."""

        def setup(self) -> None:
            scenario_started = events.ScenarioStarted(label=None, phase=PhaseName.STATEFUL_TESTING, suite_id=suite_id)
            self._start_time = time.monotonic()
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
            # Checking the stop event once inside `step` is sufficient as it is called frequently
            # The idea is to stop the execution as soon as possible
            if engine.has_to_stop:
                raise KeyboardInterrupt
            try:
                if generation.unique_inputs:
                    cached = ctx.get_step_outcome(input.case)
                    if isinstance(cached, BaseException):
                        raise cached
                    elif cached is None:
                        return None
                result = super().step(input)
                ctx.step_succeeded()
            except FailureGroup as exc:
                if generation.unique_inputs:
                    for failure in exc.exceptions:
                        ctx.store_step_outcome(input.case, failure)
                ctx.step_failed()
                raise
            except Exception as exc:
                if isinstance(
                    exc, (requests.ConnectionError, ChunkedEncodingError, requests.Timeout)
                ) and is_unrecoverable_network_error(exc):
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
            self.recorder.record_response(case_id=case.id, response=response)
            ctx.collect_metric(case, response)
            ctx.current_response = response

            label = case.operation.label
            cached = _check_context_cache.get(label)
            if cached is None:
                headers = engine.config.headers_for(operation=case.operation)
                cached = CachedCheckContextData(
                    override=overrides.for_operation(engine.config, operation=case.operation),
                    auth=engine.config.auth_for(operation=case.operation),
                    headers=CaseInsensitiveDict(headers) if headers else None,
                    config=engine.config.checks_config_for(operation=case.operation, phase="stateful"),
                    transport_kwargs=engine.get_transport_kwargs(operation=case.operation),
                )
                _check_context_cache[label] = cached

            check_ctx = CheckContext(
                override=cached.override,
                auth=cached.auth,
                headers=cached.headers,
                config=cached.config,
                transport_kwargs=cached.transport_kwargs,
                recorder=self.recorder,
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
                    elapsed_time=time.monotonic() - self._start_time,
                    skip_reason=None,
                    is_final=build_ctx.is_final,
                )
            )
            ctx.maximize_metrics()
            ctx.reset_scenario()
            super().teardown()

    seed = engine.config.seed

    while True:
        # This loop is running until no new failures are found in a single iteration
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
            code_sample: str | None = None
            if state.unrecoverable_network_error is not None:
                exc = state.unrecoverable_network_error.error
                code_sample = state.unrecoverable_network_error.code_sample
            event_queue.put(
                events.NonFatalError(
                    error=exc,
                    phase=PhaseName.STATEFUL_TESTING,
                    label=STATEFUL_TESTS_LABEL,
                    related_to_operation=False,
                    code_sample=code_sample,
                )
            )
            break
        finally:
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

        # Collect the whole chain of cURL commands
        commands = []
        parent = recorder.find_parent(case_id=failure_data.case.id)
        while parent is not None:
            commands.append(parent.as_curl_command(headers=failure_data.headers, verify=failure_data.verify))
            parent = recorder.find_parent(case_id=parent.id)
        commands.append(failure_data.case.as_curl_command(headers=failure_data.headers, verify=failure_data.verify))
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
