from __future__ import annotations

import queue
import threading
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from datetime import timedelta
from typing import TYPE_CHECKING, Any, Generator, Iterator

import hypothesis
import requests
from hypothesis.control import current_build_context
from hypothesis.errors import Flaky, Unsatisfiable

from schemathesis.checks import CheckContext, CheckFunction
from schemathesis.core.failures import FailureGroup
from schemathesis.core.transport import Response
from schemathesis.generation.hypothesis import DEFAULT_DEADLINE
from schemathesis.generation.hypothesis.reporting import ignore_hypothesis_output
from schemathesis.generation.targets import TargetMetricCollector
from schemathesis.runner.config import EngineConfig
from schemathesis.runner.control import ExecutionControl
from schemathesis.stateful.graph import ExecutionGraph

from . import events
from .context import RunnerContext
from .validation import validate_response

if TYPE_CHECKING:
    from hypothesis.stateful import Rule

    from schemathesis.generation.case import Case

    from .state_machine import APIStateMachine, Direction, StepResult

EVENT_QUEUE_TIMEOUT = 0.01
DEFAULT_STATE_MACHINE_SETTINGS = hypothesis.settings(
    phases=[hypothesis.Phase.generate],
    deadline=None,
    stateful_step_count=6,
    suppress_health_check=list(hypothesis.HealthCheck),
)


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


@dataclass
class StatefulTestRunner:
    """Stateful test runner for the given state machine.

    By default, the test runner executes the state machine in a loop until there are no new failures are found.
    The loop is executed in a separate thread for better control over the execution and reporting.
    """

    # State machine class to use
    state_machine: type[APIStateMachine]
    # Test runner configuration that defines the runtime behavior
    config: EngineConfig
    control: ExecutionControl
    session: requests.Session
    # Queue to communicate with the state machine execution
    event_queue: queue.Queue = field(default_factory=queue.Queue)

    def execute(self) -> Iterator[events.StatefulEvent]:
        """Execute a test run for a state machine."""
        yield events.RunStarted(state_machine=self.state_machine)

        kwargs = _get_hypothesis_settings_kwargs_override(self.config.execution.hypothesis_settings)
        if kwargs:
            config = replace(
                self.config,
                execution=replace(
                    self.config.execution,
                    hypothesis_settings=hypothesis.settings(self.config.execution.hypothesis_settings, **kwargs),
                ),
            )
        else:
            config = self.config
        runner_thread = threading.Thread(
            target=_execute_state_machine_loop,
            kwargs={
                "state_machine": self.state_machine,
                "event_queue": self.event_queue,
                "config": config,
                "control": self.control,
                "session": self.session,
            },
        )
        run_status = events.RunStatus.SUCCESS

        with thread_manager(runner_thread):
            try:
                while True:
                    try:
                        event = self.event_queue.get(timeout=EVENT_QUEUE_TIMEOUT)
                        # Set the run status based on the suite status
                        # ERROR & INTERRUPTED statuses are terminal, therefore they should not be overridden
                        if isinstance(event, events.SuiteFinished):
                            if event.status == events.SuiteStatus.FAILURE:
                                run_status = events.RunStatus.FAILURE
                            elif event.status == events.SuiteStatus.ERROR:
                                run_status = events.RunStatus.ERROR
                            elif event.status == events.SuiteStatus.INTERRUPTED:
                                run_status = events.RunStatus.INTERRUPTED
                        yield event
                    except queue.Empty:
                        if not runner_thread.is_alive():
                            break
            except KeyboardInterrupt:
                # Immediately notify the runner thread to stop, even though that the event will be set below in `finally`
                self.stop()
                run_status = events.RunStatus.INTERRUPTED
                yield events.Interrupted()
            finally:
                self.stop()

            yield events.RunFinished(status=run_status)

    def stop(self) -> None:
        """Stop the execution of the state machine."""
        self.control.stop()


@contextmanager
def thread_manager(thread: threading.Thread) -> Generator[None, None, None]:
    thread.start()
    try:
        yield
    finally:
        thread.join()


def _execute_state_machine_loop(
    *,
    state_machine: type[APIStateMachine],
    event_queue: queue.Queue,
    config: EngineConfig,
    control: ExecutionControl,
    session: requests.Session,
) -> None:
    """Execute the state machine testing loop."""
    from requests.structures import CaseInsensitiveDict

    ctx = RunnerContext(metric_collector=TargetMetricCollector(targets=config.execution.targets))

    call_kwargs: dict[str, Any] = {
        "session": session,
        "headers": config.network.headers,
        "timeout": config.network.timeout,
        "verify": config.network.tls_verify,
        "cert": config.network.cert,
    }
    if config.network.proxy is not None:
        call_kwargs["proxies"] = {"all": config.network.proxy}
    # TODO: Pass it from the main engine
    check_ctx = CheckContext(
        override=config.override,
        auth=config.network.auth,
        headers=CaseInsensitiveDict(config.network.headers) if config.network.headers else None,
        config=config.checks_config,
        transport_kwargs=call_kwargs,
        # TODO: Pass it from the main engine
        execution_graph=ExecutionGraph(),
    )

    class _InstrumentedStateMachine(state_machine):  # type: ignore[valid-type,misc]
        """State machine with additional hooks for emitting events."""

        def setup(self) -> None:
            build_ctx = current_build_context()
            event_queue.put(events.ScenarioStarted(is_final=build_ctx.is_final))
            self._execution_graph = check_ctx.execution_graph

        def get_call_kwargs(self, case: Case) -> dict[str, Any]:
            return call_kwargs

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
            if control.is_stopped:
                raise KeyboardInterrupt
            event_queue.put(events.StepStarted())
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
                    events.StepFinished(
                        status=ctx.current_step_status,
                        transition_id=transition_id,
                        target=case.operation.label,
                        case=case,
                        response=ctx.current_response,
                        checks=ctx.checks_for_step,
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
                additional_checks=additional_checks,
            )

        def teardown(self) -> None:
            build_ctx = current_build_context()
            event_queue.put(
                events.ScenarioFinished(
                    status=ctx.current_scenario_status,
                    is_final=build_ctx.is_final,
                )
            )
            ctx.maximize_metrics()
            ctx.reset_scenario()
            super().teardown()

    if config.execution.seed is not None:
        InstrumentedStateMachine = hypothesis.seed(config.execution.seed)(_InstrumentedStateMachine)
    else:
        InstrumentedStateMachine = _InstrumentedStateMachine

    def should_stop() -> bool:
        # TODO: Count failures directly on `control` + use its `control.is_stopped` instead
        return control.max_failures is not None and ctx.failures_count >= control.max_failures

    while True:
        # This loop is running until no new failures are found in a single iteration
        event_queue.put(events.SuiteStarted())
        if control.is_stopped:
            event_queue.put(events.SuiteFinished(status=events.SuiteStatus.INTERRUPTED, failures=[]))
            break
        suite_status = events.SuiteStatus.SUCCESS
        try:
            with ignore_hypothesis_output():  # type: ignore
                InstrumentedStateMachine.run(settings=config.execution.hypothesis_settings)
        except KeyboardInterrupt:
            # Raised in the state machine when the stop event is set or it is raised by the user's code
            # that is placed in the base class of the state machine.
            # Therefore, set the stop event to cover the latter case
            control.stop()
            suite_status = events.SuiteStatus.INTERRUPTED
            break
        except FailureGroup as exc:
            # When a check fails, the state machine is stopped
            # The failure is already sent to the queue by the state machine
            # Here we need to either exit or re-run the state machine with this failure marked as known
            suite_status = events.SuiteStatus.FAILURE
            if should_stop():
                break
            for failure in exc.exceptions:
                ctx.mark_as_seen_in_run(failure)
            continue
        except Flaky:
            suite_status = events.SuiteStatus.FAILURE
            if should_stop():
                break
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
            suite_status = events.SuiteStatus.ERROR
            event_queue.put(events.Errored(exception=exc))
            break
        finally:
            event_queue.put(events.SuiteFinished(status=suite_status, failures=ctx.failures_for_suite))
            ctx.reset()
        # Exit on the first successful state machine execution
        break
