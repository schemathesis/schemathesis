from __future__ import annotations

from collections.abc import Callable
from functools import partial
from typing import TYPE_CHECKING

from schemathesis.generation.hypothesis.reporting import FilterCaseTracker
from schemathesis.hooks import GLOBAL_HOOK_DISPATCHER, HookContext, HookDispatcher, _should_skip_hook

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
    strategy = _apply_dispatcher(strategy, GLOBAL_HOOK_DISPATCHER, operation)
    strategy = _apply_dispatcher(strategy, operation.schema.hooks, operation)
    if local is not None:
        strategy = _apply_dispatcher(strategy, local, operation)
    return strategy


def _apply_dispatcher(
    strategy: SearchStrategy[Case],
    dispatcher: HookDispatcher,
    operation: APIOperation,
) -> SearchStrategy[Case]:
    context = HookContext(operation=operation)
    for hook in dispatcher.get_all_by_name("before_generate_case"):
        if _should_skip_hook(hook, context):
            continue
        strategy = hook(context, strategy)
    for hook in dispatcher.get_all_by_name("filter_case"):
        if _should_skip_hook(hook, context):
            continue
        bound_hook = partial(hook, context)
        if operation.filter_case_tracker is None:
            operation.filter_case_tracker = FilterCaseTracker()
        tracker = operation.filter_case_tracker

        def _tracking_filter(case: Case, _hook: Callable = bound_hook, _tracker: FilterCaseTracker = tracker) -> bool:
            result = _hook(case)
            _tracker.record(result)
            return result

        strategy = strategy.filter(_tracking_filter)
    for hook in dispatcher.get_all_by_name("map_case"):
        if _should_skip_hook(hook, context):
            continue
        hook = partial(hook, context)
        strategy = strategy.map(hook)
    for hook in dispatcher.get_all_by_name("flatmap_case"):
        if _should_skip_hook(hook, context):
            continue
        hook = partial(hook, context)
        strategy = strategy.flatmap(hook)
    return strategy
