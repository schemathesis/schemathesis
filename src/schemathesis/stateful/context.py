from __future__ import annotations

import traceback
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Tuple, Type, Union

from ..exceptions import CheckFailed
from ..targets import TargetMetricCollector
from . import events

if TYPE_CHECKING:
    from ..models import Case, Check
    from ..transports.responses import GenericResponse

FailureKey = Union[Type[CheckFailed], Tuple[str, int]]


def _failure_cache_key(exc: CheckFailed | AssertionError) -> FailureKey:
    """Create a ket to identify unique failures."""
    from hypothesis.internal.escalation import get_trimmed_traceback

    # For CheckFailed, we already have all distinctive information about the failure, which is contained
    # in the exception type itself.
    if isinstance(exc, CheckFailed):
        return exc.__class__

    # Assertion come from the user's code and we may try to group them by location
    tb = get_trimmed_traceback(exc)
    filename, lineno, *_ = traceback.extract_tb(tb)[-1]
    return (filename, lineno)


@dataclass
class RunnerContext:
    """Mutable context for state machine execution."""

    # All seen failure keys, both grouped and individual ones
    seen_in_run: set[FailureKey] = field(default_factory=set)
    # Failures keys seen in the current suite
    seen_in_suite: set[FailureKey] = field(default_factory=set)
    # Unique failures collected in the current suite
    failures_for_suite: list[Check] = field(default_factory=list)
    # All checks executed in the current run
    checks_for_step: list[Check] = field(default_factory=list)
    # Status of the current step
    current_step_status: events.StepStatus | None = None
    # The currently processed response
    current_response: GenericResponse | None = None
    # Total number of failures
    failures_count: int = 0
    # The total number of completed test scenario
    completed_scenarios: int = 0
    # Metrics collector for targeted testing
    metric_collector: TargetMetricCollector = field(default_factory=lambda: TargetMetricCollector(targets=[]))

    @property
    def current_scenario_status(self) -> events.ScenarioStatus:
        if self.current_step_status == events.StepStatus.SUCCESS:
            return events.ScenarioStatus.SUCCESS
        elif self.current_step_status == events.StepStatus.FAILURE:
            return events.ScenarioStatus.FAILURE
        elif self.current_step_status == events.StepStatus.ERROR:
            return events.ScenarioStatus.ERROR
        elif self.current_step_status == events.StepStatus.INTERRUPTED:
            return events.ScenarioStatus.INTERRUPTED
        return events.ScenarioStatus.REJECTED

    def reset_scenario(self) -> None:
        self.completed_scenarios += 1
        self.current_step_status = None
        self.current_response = None

    def reset_step(self) -> None:
        self.checks_for_step = []

    def step_succeeded(self) -> None:
        self.current_step_status = events.StepStatus.SUCCESS

    def step_failed(self) -> None:
        self.current_step_status = events.StepStatus.FAILURE

    def step_errored(self) -> None:
        self.current_step_status = events.StepStatus.ERROR

    def step_interrupted(self) -> None:
        self.current_step_status = events.StepStatus.INTERRUPTED

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

    def mark_current_suite_as_seen_in_run(self) -> None:
        self.seen_in_run.update(self.seen_in_suite)

    def is_seen_in_run(self, exc: CheckFailed | AssertionError) -> bool:
        key = _failure_cache_key(exc)
        return key in self.seen_in_run

    def is_seen_in_suite(self, exc: CheckFailed | AssertionError) -> bool:
        key = _failure_cache_key(exc)
        return key in self.seen_in_suite

    def add_failed_check(self, check: Check) -> None:
        self.failures_for_suite.append(check)
        self.failures_count += 1

    def collect_metric(self, case: Case, response: GenericResponse) -> None:
        self.metric_collector.store(case, response)

    def maximize_metrics(self) -> None:
        self.metric_collector.maximize()

    def reset(self) -> None:
        self.failures_for_suite = []
        self.seen_in_suite.clear()
        self.reset_scenario()
        self.metric_collector.reset()
