from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from schemathesis.generation.hypothesis.reporting import FilterCaseTracker
from schemathesis.hooks import GLOBAL_HOOK_DISPATCHER, HookContext, HookDispatcher

if TYPE_CHECKING:
    from hypothesis.strategies import SearchStrategy

    from schemathesis.generation.case import Case
    from schemathesis.schemas import APIOperation


def apply_case_hooks(
    strategy: SearchStrategy[Case],
    operation: APIOperation,
    local: HookDispatcher | None,
) -> SearchStrategy[Case]:
    """Layer case hooks onto `strategy` in dispatcher order: global, schema, local."""
    for dispatcher in case_hook_dispatchers(operation, local):
        strategy = _apply_dispatcher(strategy, dispatcher, operation)
    return strategy


def case_hook_dispatchers(operation: APIOperation, local: HookDispatcher | None) -> list[HookDispatcher]:
    """Dispatchers whose case hooks apply to `operation`, in application order."""
    dispatchers = [GLOBAL_HOOK_DISPATCHER, operation.schema.hooks]
    if local is not None:
        dispatchers.append(local)
    return dispatchers


def _apply_dispatcher(
    strategy: SearchStrategy[Case],
    dispatcher: HookDispatcher,
    operation: APIOperation,
) -> SearchStrategy[Case]:
    def track_filter(hook: Callable[[Case], bool]) -> Callable[[Case], bool]:
        if operation.filter_case_tracker is None:
            operation.filter_case_tracker = FilterCaseTracker()
        tracker = operation.filter_case_tracker

        def _tracking_filter(case: Case) -> bool:
            result = hook(case)
            tracker.record(result)
            return result

        return _tracking_filter

    return dispatcher.apply_to_container(
        strategy, "case", HookContext(operation=operation), filter_wrapper=track_filter
    )
