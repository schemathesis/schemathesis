from __future__ import annotations

import queue
import threading
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Generator, Iterator, Type

import hypothesis
import requests
from hypothesis.control import current_build_context
from hypothesis.errors import Flaky, Unsatisfiable
from hypothesis.stateful import Rule

from ..exceptions import CheckFailed
from ..targets import TargetMetricCollector
from . import events
from .config import StatefulTestRunnerConfig
from .context import RunnerContext
from .validation import validate_response

if TYPE_CHECKING:
    from ..models import Case, CheckFunction
    from ..transports.responses import GenericResponse
    from .state_machine import APIStateMachine, Direction, StepResult

EVENT_QUEUE_TIMEOUT = 0.01


@dataclass
class StatefulTestRunner:
    """Stateful test runner for the given state machine.

    By default, the test runner executes the state machine in a loop until there are no new failures are found.
    The loop is executed in a separate thread for better control over the execution and reporting.
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

        yield events.RunStarted(state_machine=self.state_machine)

        runner_thread = threading.Thread(
            target=_execute_state_machine_loop,
            kwargs={
                "state_machine": self.state_machine,
                "event_queue": self.event_queue,
                "config": self.config,
                "stop_event": self.stop_event,
            },
        )
        run_status = events.RunStatus.SUCCESS

        with thread_manager(runner_thread):
            try:
                while True:
                    try:
                        event = self.event_queue.get(timeout=EVENT_QUEUE_TIMEOUT)
                        # Set the run status based on the suite status
                        # ERROR & INTERRPUTED statuses are terminal, therefore they should not be overridden
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
        self.stop_event.set()


@contextmanager
def thread_manager(thread: threading.Thread) -> Generator[None, None, None]:
    thread.start()
    try:
        yield
    finally:
        thread.join()


def _execute_state_machine_loop(
    *,
    state_machine: Type[APIStateMachine],
    event_queue: queue.Queue,
    config: StatefulTestRunnerConfig,
    stop_event: threading.Event,
) -> None:
    """Execute the state machine testing loop."""
    from hypothesis import reporting

    from ..transports import RequestsTransport

    ctx = RunnerContext(metric_collector=TargetMetricCollector(targets=config.targets))

    call_kwargs: dict[str, Any] = {"headers": config.headers}
    if isinstance(state_machine.schema.transport, RequestsTransport):
        call_kwargs["timeout"] = config.request.prepared_timeout
        call_kwargs["verify"] = config.request.tls_verify
        call_kwargs["cert"] = config.request.cert
        if config.request.proxy is not None:
            call_kwargs["proxies"] = {"all": config.request.proxy}
        session = requests.Session()
        if config.auth is not None:
            session.auth = config.auth
        call_kwargs["session"] = session

    class _InstrumentedStateMachine(state_machine):  # type: ignore[valid-type,misc]
        """State machine with additional hooks for emitting events."""

        def setup(self) -> None:
            build_ctx = current_build_context()
            event_queue.put(events.ScenarioStarted(is_final=build_ctx.is_final))
            super().setup()

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
            if stop_event.is_set():
                raise KeyboardInterrupt
            event_queue.put(events.StepStarted())
            try:
                if config.dry_run:
                    return None
                result = super().step(case, previous)
                ctx.step_succeeded()
            except CheckFailed:
                ctx.step_failed()
                raise
            except Exception:
                ctx.step_errored()
                raise
            except KeyboardInterrupt:
                ctx.step_interrupted()
                raise
            finally:
                transition_id: events.TransitionId | None
                if previous is not None:
                    transition = previous[1]
                    transition_id = events.TransitionId(
                        name=transition.name,
                        status_code=transition.status_code,
                        source=transition.operation.verbose_name,
                    )
                else:
                    transition_id = None
                event_queue.put(
                    events.StepFinished(
                        status=ctx.current_step_status,
                        transition_id=transition_id,
                        target=case.operation.verbose_name,
                        case=case,
                        response=ctx.current_response,
                        checks=ctx.checks_for_step,
                    )
                )
                ctx.reset_step()
            return result

        def validate_response(
            self, response: GenericResponse, case: Case, additional_checks: tuple[CheckFunction, ...] = ()
        ) -> None:
            ctx.collect_metric(case, response)
            ctx.current_response = response
            validate_response(
                response=response,
                case=case,
                ctx=ctx,
                checks=config.checks,
                additional_checks=additional_checks,
                max_response_time=config.max_response_time,
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

    if config.seed is not None:
        InstrumentedStateMachine = hypothesis.seed(config.seed)(_InstrumentedStateMachine)
    else:
        InstrumentedStateMachine = _InstrumentedStateMachine

    def should_stop() -> bool:
        return config.exit_first or (config.max_failures is not None and ctx.failures_count >= config.max_failures)

    while True:
        # This loop is running until no new failures are found in a single iteration
        event_queue.put(events.SuiteStarted())
        if stop_event.is_set():
            event_queue.put(events.SuiteFinished(status=events.SuiteStatus.INTERRUPTED, failures=[]))
            break
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
            if should_stop():
                break
            ctx.mark_as_seen_in_run(exc)
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
                if ctx.completed_scenarios >= config.hypothesis_settings.max_examples:
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
