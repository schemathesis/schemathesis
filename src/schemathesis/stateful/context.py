from __future__ import annotations

import traceback
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Tuple, Type, Union

from ..exceptions import CheckFailed
from . import events

if TYPE_CHECKING:
    from ..models import Check

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
    # Status of the current step
    current_step_status: events.StepStatus = events.StepStatus.SUCCESS

    @property
    def current_scenario_status(self) -> events.ScenarioStatus:
        if self.current_step_status == events.StepStatus.SUCCESS:
            return events.ScenarioStatus.SUCCESS
        elif self.current_step_status == events.StepStatus.FAILURE:
            return events.ScenarioStatus.FAILURE
        return events.ScenarioStatus.ERROR

    def reset_step_status(self) -> None:
        self.current_step_status = events.StepStatus.SUCCESS

    def step_failed(self) -> None:
        self.current_step_status = events.StepStatus.FAILURE

    def step_errored(self) -> None:
        self.current_step_status = events.StepStatus.ERROR

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

    def reset(self) -> None:
        self.failures_for_suite = []
        self.seen_in_suite.clear()
        self.reset_step_status()
