from __future__ import annotations

import queue
from dataclasses import replace
from datetime import timedelta
from typing import TYPE_CHECKING, Any

import hypothesis
from hypothesis.control import current_build_context
from hypothesis.errors import Flaky, Unsatisfiable
from hypothesis.stateful import Rule

from schemathesis.checks import CheckContext, CheckFunction, run_checks
from schemathesis.core.failures import Failure, FailureGroup
from schemathesis.core.result import Err, Ok
from schemathesis.core.transport import Response
from schemathesis.generation.case import Case
from schemathesis.generation.hypothesis import DEFAULT_DEADLINE
from schemathesis.generation.hypothesis.reporting import ignore_hypothesis_output
from schemathesis.generation.targets import TargetMetricCollector
from schemathesis.runner import Status, events
from schemathesis.runner.context import EngineContext
from schemathesis.runner.control import ExecutionControl
from schemathesis.runner.models import Check, Request
from schemathesis.runner.phases import PhaseName
from schemathesis.runner.phases.stateful.context import RunnerContext
from schemathesis.stateful.state_machine import DEFAULT_STATE_MACHINE_SETTINGS, APIStateMachine, Direction, StepResult

if TYPE_CHECKING:
    from schemathesis.generation.case import Case


def _get_hypothesis_settings_kwargs_override(settings: hypothesis.settings) -> dict[str, Any]:
    """Get the settings that should be overridden to match the defaults for API state machines."""
    kwargs = {}
    hypothesis_default = hypothesis.settings()
    if settings.phases == hypothesis_default.phases:
        kwargs["phases"] = DEFAULT_STATE_MACHINE_SETTINGS.phases
    if settings.stateful_step_count == hypothesis_default.stateful_step_count:
        kwargs["stateful_step_count"] = DEFAULT_STATE_MACHINE_SETTINGS.stateful_step_count
    if settings.deadline in (hypothesis_default.deadline, timedelta(milliseconds=DEFAULT_DEADLINE)):
        kwargs["deadline"] = DEFAULT_STATE_MACHINE_SETTINGS.deadline
    if settings.suppress_health_check == hypothesis_default.suppress_health_check:
        kwargs["suppress_health_check"] = DEFAULT_STATE_MACHINE_SETTINGS.suppress_health_check
    return kwargs


def execute_state_machine_loop(
    *,
    state_machine: type[APIStateMachine],
    event_queue: queue.Queue,
    engine: EngineContext,
) -> None:
    """Execute the state machine testing loop."""
    kwargs = _get_hypothesis_settings_kwargs_override(engine.config.execution.hypothesis_settings)
    if kwargs:
        config = replace(
            engine.config,
            execution=replace(
                engine.config.execution,
                hypothesis_settings=hypothesis.settings(engine.config.execution.hypothesis_settings, **kwargs),
            ),
        )
    else:
        config = engine.config

    ctx = RunnerContext(metric_collector=TargetMetricCollector(targets=config.execution.targets))

    transport_kwargs = engine.transport_kwargs
    check_ctx = engine.check_context

    class _InstrumentedStateMachine(state_machine):  # type: ignore[valid-type,misc]
        """State machine with additional hooks for emitting events."""

        def setup(self) -> None:
            build_ctx = current_build_context()
            scenario_started = events.ScenarioStarted(
                phase=PhaseName.STATEFUL_TESTING, suite_id=suite_id, is_final=build_ctx.is_final
            )
            self._scenario_id = scenario_started.id
            event_queue.put(Ok(scenario_started))
            self._execution_graph = check_ctx.execution_graph

        def get_call_kwargs(self, case: Case) -> dict[str, Any]:
            return transport_kwargs

        def _repr_step(self, rule: Rule, data: dict, result: StepResult) -> str:
            return ""

        if config.override is not None:

            def before_call(self, case: Case) -> None:
                for location, entry in config.override.for_operation(case.operation).items():  # type: ignore[union-attr]
                    if entry:
                        container = getattr(case, location) or {}
                        container.update(entry)
                        setattr(case, location, container)
                return super().before_call(case)

        def step(self, case: Case, previous: tuple[StepResult, Direction] | None = None) -> StepResult | None:
            # Checking the stop event once inside `step` is sufficient as it is called frequently
            # The idea is to stop the execution as soon as possible
            if engine.control.is_stopped:
                raise KeyboardInterrupt
            event_queue.put(
                Ok(
                    events.StepStarted(
                        phase=PhaseName.STATEFUL_TESTING, suite_id=suite_id, scenario_id=self._scenario_id
                    )
                )
            )
            try:
                if config.execution.dry_run:
                    return None
                if config.execution.unique_data:
                    cached = ctx.get_step_outcome(case)
                    if isinstance(cached, BaseException):
                        raise cached
                    elif cached is None:
                        return None
                result = super().step(case, previous)
                ctx.step_succeeded()
            except FailureGroup as exc:
                if config.execution.unique_data:
                    for failure in exc.exceptions:
                        ctx.store_step_outcome(case, failure)
                ctx.step_failed()
                raise
            except Exception as exc:
                if config.execution.unique_data:
                    ctx.store_step_outcome(case, exc)
                ctx.step_errored()
                raise
            except KeyboardInterrupt:
                ctx.step_interrupted()
                raise
            except BaseException as exc:
                if config.execution.unique_data:
                    ctx.store_step_outcome(case, exc)
                raise exc
            else:
                if config.execution.unique_data:
                    ctx.store_step_outcome(case, None)
            finally:
                transition_id: events.TransitionId | None
                if previous is not None:
                    transition = previous[1]
                    transition_id = events.TransitionId(
                        name=transition.name,
                        status_code=transition.status_code,
                        source=transition.operation.label,
                    )
                else:
                    transition_id = None
                event_queue.put(
                    Ok(
                        events.StepFinished(
                            phase=PhaseName.STATEFUL_TESTING,
                            suite_id=suite_id,
                            scenario_id=self._scenario_id,
                            status=ctx.current_step_status,
                            transition_id=transition_id,
                            target=case.operation.label,
                            case=case,
                            response=ctx.current_response,
                            checks=ctx.checks_for_step,
                        )
                    )
                )
                ctx.reset_step()
            return result

        def validate_response(
            self, response: Response, case: Case, additional_checks: tuple[CheckFunction, ...] = ()
        ) -> None:
            ctx.collect_metric(case, response)
            ctx.current_response = response
            validate_response(
                response=response,
                case=case,
                runner_ctx=ctx,
                check_ctx=check_ctx,
                checks=config.execution.checks,
                control=engine.control,
                additional_checks=additional_checks,
            )

        def teardown(self) -> None:
            build_ctx = current_build_context()
            event_queue.put(
                Ok(
                    events.ScenarioFinished(
                        phase=PhaseName.STATEFUL_TESTING,
                        suite_id=suite_id,
                        status=ctx.current_scenario_status,
                        is_final=build_ctx.is_final,
                    )
                )
            )
            ctx.maximize_metrics()
            ctx.reset_scenario()
            super().teardown()

    if config.execution.seed is not None:
        InstrumentedStateMachine = hypothesis.seed(config.execution.seed)(_InstrumentedStateMachine)
    else:
        InstrumentedStateMachine = _InstrumentedStateMachine

    while True:
        # This loop is running until no new failures are found in a single iteration
        suite_started = events.SuiteStarted(phase=PhaseName.STATEFUL_TESTING)
        suite_id = suite_started.id
        event_queue.put(Ok(suite_started))
        if engine.control.is_stopped:
            event_queue.put(
                Ok(events.SuiteFinished(phase=PhaseName.STATEFUL_TESTING, status=Status.INTERRUPTED, failures=[]))
            )
            break
        suite_status = Status.SUCCESS
        try:
            with ignore_hypothesis_output():  # type: ignore
                InstrumentedStateMachine.run(settings=config.execution.hypothesis_settings)
        except KeyboardInterrupt:
            # Raised in the state machine when the stop event is set or it is raised by the user's code
            # that is placed in the base class of the state machine.
            # Therefore, set the stop event to cover the latter case
            engine.control.stop()
            suite_status = Status.INTERRUPTED
            break
        except FailureGroup as exc:
            # When a check fails, the state machine is stopped
            # The failure is already sent to the queue by the state machine
            # Here we need to either exit or re-run the state machine with this failure marked as known
            suite_status = Status.FAILURE
            if engine.control.is_stopped:
                break  # type: ignore[unreachable]
            for failure in exc.exceptions:
                ctx.mark_as_seen_in_run(failure)
            continue
        except Flaky:
            suite_status = Status.FAILURE
            if engine.control.is_stopped:
                break  # type: ignore[unreachable]
            # Mark all failures in this suite as seen to prevent them being re-discovered
            ctx.mark_current_suite_as_seen_in_run()
            continue
        except Exception as exc:
            if isinstance(exc, Unsatisfiable) and ctx.completed_scenarios > 0:
                # Sometimes Hypothesis randomly gives up on generating some complex cases. However, if we know that
                # values are possible to generate based on the previous observations, we retry the generation
                if ctx.completed_scenarios >= config.execution.hypothesis_settings.max_examples:
                    # Avoid infinite restarts
                    break
                continue
            # Any other exception is an inner error and the test run should be stopped
            suite_status = Status.ERROR
            event_queue.put(Err(exc))
            break
        finally:
            event_queue.put(
                Ok(
                    events.SuiteFinished(
                        phase=PhaseName.STATEFUL_TESTING, status=suite_status, failures=ctx.failures_for_suite
                    )
                )
            )
            ctx.reset()
        # Exit on the first successful state machine execution
        break


def validate_response(
    *,
    response: Response,
    case: Case,
    runner_ctx: RunnerContext,
    check_ctx: CheckContext,
    control: ExecutionControl,
    checks: list[CheckFunction],
    additional_checks: tuple[CheckFunction, ...] = (),
) -> None:
    """Validate the response against the provided checks."""
    results = runner_ctx.checks_for_step

    def on_failure(name: str, collected: set[Failure], failure: Failure) -> None:
        if runner_ctx.is_seen_in_suite(failure) or runner_ctx.is_seen_in_run(failure):
            return
        failed_check = Check(
            name=name,
            status=Status.FAILURE,
            request=Request.from_prepared_request(response.request),
            response=response,
            case=case,
            failure=failure,
        )
        results.append(failed_check)
        control.count_failure()
        runner_ctx.add_failed_check(failed_check)
        runner_ctx.mark_as_seen_in_suite(failure)
        collected.add(failure)

    def on_success(name: str, case: Case) -> None:
        results.append(
            Check(
                name=name,
                status=Status.SUCCESS,
                request=Request.from_prepared_request(response.request),
                response=response,
                case=case,
            )
        )

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
