from __future__ import annotations

import queue
import threading
import traceback
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Iterator, Tuple, Type, Union

from hypothesis.errors import Flaky

from . import events

from ..exceptions import CheckFailed, get_grouped_exception

if TYPE_CHECKING:
    import hypothesis

    from ..models import Case, Check, CheckFunction
    from ..transports.responses import GenericResponse
    from .state_machine import APIStateMachine, Direction, StepResult

EVENT_QUEUE_TIMEOUT = 0.01


def _default_checks_factory() -> tuple[CheckFunction, ...]:
    from ..checks import ALL_CHECKS
    from ..specs.openapi.checks import use_after_free

    return ALL_CHECKS + (use_after_free,)


def _get_default_hypothesis_settings_kwargs() -> dict[str, Any]:
    import hypothesis

    return {"phases": (hypothesis.Phase.generate,), "deadline": None}


def _default_hypothesis_settings_factory() -> hypothesis.settings:
    # To avoid importing hypothesis at the module level
    import hypothesis

    return hypothesis.settings(**_get_default_hypothesis_settings_kwargs())


@dataclass
class StatefulTestRunnerConfig:
    """Configuration for the stateful test runner."""

    # Checks to run against each response
    checks: tuple[CheckFunction, ...] = field(default_factory=_default_checks_factory)
    # Hypothesis settings for state machine execution
    hypothesis_settings: hypothesis.settings = field(default_factory=_default_hypothesis_settings_factory)
    # Whether to stop the execution after the first failure
    exit_first: bool = False

    def __post_init__(self) -> None:
        import hypothesis

        kwargs = _get_hypothesis_settings_kwargs_override(self.hypothesis_settings)
        if kwargs:
            self.hypothesis_settings = hypothesis.settings(self.hypothesis_settings, **kwargs)


def _get_hypothesis_settings_kwargs_override(settings: hypothesis.settings) -> dict[str, Any]:
    """Get the settings that should be overridden to match the defaults for API state machines."""
    import hypothesis

    kwargs = {}
    hypothesis_default = hypothesis.settings()
    state_machine_default = _default_hypothesis_settings_factory()
    if settings.phases == hypothesis_default.phases:
        kwargs["phases"] = state_machine_default.phases
    if settings.deadline == hypothesis_default.deadline:
        kwargs["deadline"] = state_machine_default.deadline
    return kwargs


@dataclass
class StatefulTestRunner:
    """Stateful test runner for the given state machine.

    By default, the test runner executes the state machine in a loop until there are no new failures are found.
    The loop is executed in a separate thread for more control over the execution.
    """

    # State machine class to use
    state_machine: Type[APIStateMachine]
    # Test runner configuration that defines the runtime behavior
    config: StatefulTestRunnerConfig = field(default_factory=StatefulTestRunnerConfig)
    # Event to stop the execution
    stop_event: threading.Event = field(default_factory=threading.Event)
    # Queue to communicate with the state machine execution
    event_queue: queue.Queue = field(default_factory=queue.Queue)

    def execute(self) -> Iterator[events.StatefulEvent]:
        """Execute a test run for a state machine."""
        self.stop_event.clear()

        yield events.BeforeRun()

        runner_thread = threading.Thread(
            target=_execute_state_machine_loop,
            kwargs={
                "state_machine": self.state_machine,
                "event_queue": self.event_queue,
                "config": self.config,
                "stop_event": self.stop_event,
            },
        )
        runner_thread.start()

        run_status = events.RunStatus.SUCCESS

        try:
            while True:
                try:
                    event = self.event_queue.get(timeout=EVENT_QUEUE_TIMEOUT)
                    if isinstance(event, events.AfterSuite):
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

        yield events.AfterRun(status=run_status)

        runner_thread.join()

    def stop(self) -> None:
        """Stop the execution of the state machine."""
        self.stop_event.set()


def _execute_state_machine_loop(
    *,
    state_machine: Type[APIStateMachine],
    event_queue: queue.Queue,
    config: StatefulTestRunnerConfig,
    stop_event: threading.Event,
) -> None:
    """Execute the state machine testing loop."""
    from hypothesis import reporting

    failures = FailureRegistry()

    # State machine is instrumented to send events to the queue
    # Otherwise, Hypothesis does not provide a way to hook into the process
    step_status = events.StepStatus.SUCCESS

    class InstrumentedStateMachine(state_machine):  # type: ignore[valid-type,misc]
        def setup(self) -> None:
            event_queue.put(events.BeforeScenario())
            super().setup()

        def step(self, case: Case, previous: tuple[StepResult, Direction] | None = None) -> StepResult:
            # Checking the stop event once inside `step` is sufficient as it is called frequently
            # The idea is to stop the execution as soon as possible
            if stop_event.is_set():
                raise KeyboardInterrupt
            event_queue.put(events.BeforeStep())
            nonlocal step_status

            step_status = events.StepStatus.SUCCESS
            try:
                result = super().step(case, previous)
            except CheckFailed:
                step_status = events.StepStatus.FAILURE
                raise
            except Exception:
                step_status = events.StepStatus.ERROR
                raise
            finally:
                event_queue.put(events.AfterStep(status=step_status))
            return result

        def validate_response(
            self, response: GenericResponse, case: Case, additional_checks: tuple[CheckFunction, ...] = ()
        ) -> None:
            validate_response(response, case, failures, config.checks, additional_checks)

        def teardown(self) -> None:
            if step_status == events.StepStatus.SUCCESS:
                scenario_status = events.ScenarioStatus.SUCCESS
            elif step_status == events.StepStatus.FAILURE:
                scenario_status = events.ScenarioStatus.FAILURE
            else:
                scenario_status = events.ScenarioStatus.ERROR
            event_queue.put(events.AfterScenario(scenario_status))
            super().teardown()

    while True:
        if stop_event.is_set():
            event_queue.put(events.AfterSuite(status=events.SuiteStatus.INTERRUPTED, failures=[]))
            break
        # This loop is running until no new failures are found in a single iteration
        event_queue.put(events.BeforeSuite())
        suite_status = events.SuiteStatus.SUCCESS
        try:
            with reporting.with_reporter(lambda _: None):  # type: ignore
                InstrumentedStateMachine.run(settings=config.hypothesis_settings)
        except KeyboardInterrupt:
            # Raised in the state machine when the stop event is set or it is raised by the user's code
            # that is placed in the base class of the state machine.
            # Therefore, set the stop event to cover the latter case
            stop_event.set()
            suite_status = events.SuiteStatus.INTERRUPTED
            break
        except CheckFailed as exc:
            # When a check fails, the state machine is stopped
            # The failure is already sent to the queue by the state machine
            # Here we need to either exit or re-run the state machine with this failure marked as known
            suite_status = events.SuiteStatus.FAILURE
            if config.exit_first:
                break
            failures.mark_as_seen_in_run(exc)
            continue
        except Flaky:
            suite_status = events.SuiteStatus.FAILURE
            continue
        except Exception as exc:
            # Any other exception is an inner error and the test run should be stopped
            suite_status = events.SuiteStatus.ERROR
            event_queue.put(events.Error(exc))
            break
        finally:
            checks = failures.take_checks_for_suite()
            event_queue.put(events.AfterSuite(status=suite_status, failures=checks))
        # Exit on the first successful state machine execution
        break


FailureKey = Union[Type[CheckFailed], Tuple[str, int, str]]


def _failure_cache_key(exc: CheckFailed | AssertionError) -> FailureKey:
    from hypothesis.internal.escalation import get_trimmed_traceback

    # For CheckFailed, we already have all distinctive information about the failure, which is contained
    # in the exception type itself.
    if isinstance(exc, CheckFailed):
        return exc.__class__

    # Assertion come from the user's code and we may try to group them by location and message
    tb = get_trimmed_traceback(exc)
    filename, lineno, *_ = traceback.extract_tb(tb)[-1]
    return (filename, lineno, str(exc))


@dataclass
class FailureRegistry:
    """Registry for the failures that occurred during the state machine execution."""

    # All seen failures, both grouped and individual ones
    seen_in_run: set[FailureKey] = field(default_factory=set)
    # Failures seen in the current suite
    seen_in_suite: set[FailureKey] = field(default_factory=set)
    checks_for_suite: list[Check] = field(default_factory=list)

    def mark_as_seen_in_run(self, exc: CheckFailed) -> None:
        key = _failure_cache_key(exc)
        self.seen_in_run.add(key)
        causes = exc.causes or ()
        for cause in causes:
            key = _failure_cache_key(cause)
            self.seen_in_run.add(key)

    def mark_as_seen_in_suite(self, exc: CheckFailed | AssertionError) -> None:
        key = _failure_cache_key(exc)
        self.seen_in_suite.add(key)

    def is_seen_in_run(self, exc: CheckFailed | AssertionError) -> bool:
        key = _failure_cache_key(exc)
        return key in self.seen_in_run

    def is_seen_in_suite(self, exc: CheckFailed | AssertionError) -> bool:
        key = _failure_cache_key(exc)
        return key in self.seen_in_suite

    def add_failed_check(self, check: Check) -> None:
        self.checks_for_suite.append(check)

    def take_checks_for_suite(self) -> list[Check]:
        checks = self.checks_for_suite
        self.checks_for_suite = []
        self.seen_in_suite.clear()
        return checks


def validate_response(
    response: GenericResponse,
    case: Case,
    failures: FailureRegistry,
    checks: tuple[CheckFunction, ...],
    additional_checks: tuple[CheckFunction, ...] = (),
) -> None:
    from .._compat import MultipleFailures
    from ..models import Check, Status

    exceptions: list[CheckFailed | AssertionError] = []

    def on_failed_check(exc: CheckFailed) -> None:
        exceptions.append(exc)
        if failures.is_seen_in_suite(exc):
            return
        failures.add_failed_check(
            Check(
                name=name,
                value=Status.failure,
                response=response,
                elapsed=response.elapsed.total_seconds(),
                example=copied_case,
                message=str(exc),
                context=exc.context,
                request=None,
            )
        )
        failures.mark_as_seen_in_suite(exc)

    def on_failed_custom_assertion(exc: AssertionError) -> None:
        exceptions.append(exc)
        if failures.is_seen_in_suite(exc):
            return
        message = str(exc) or f"Custom check failed: `{name}`"
        failures.add_failed_check(
            Check(
                name=name,
                value=Status.failure,
                response=response,
                elapsed=response.elapsed.total_seconds(),
                example=copied_case,
                message=message,
                context=None,
                request=None,
            )
        )
        failures.mark_as_seen_in_suite(exc)

    for check in checks + additional_checks:
        name = check.__name__
        copied_case = case.partial_deepcopy()
        try:
            check(response, copied_case)
        except CheckFailed as exc:
            if failures.is_seen_in_run(exc):
                continue
            on_failed_check(exc)
        except AssertionError as exc:
            if failures.is_seen_in_run(exc):
                continue
            on_failed_custom_assertion(exc)
        except MultipleFailures as exc:
            for exception in exc.exceptions:
                if failures.is_seen_in_run(exception):
                    continue
                on_failed_check(exception)

    if exceptions:
        raise get_grouped_exception(case.operation.verbose_name, *exceptions)(causes=tuple(exceptions))
