"""Provide strategies for given endpoint(s) definition."""
import asyncio
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

import hypothesis
from hypothesis import strategies as st
from hypothesis.strategies import SearchStrategy
from hypothesis.utils.conventions import InferType

from .constants import DEFAULT_DEADLINE, DataGenerationMethod
from .exceptions import InvalidSchema
from .hooks import GLOBAL_HOOK_DISPATCHER, HookContext, HookDispatcher
from .models import Case, Endpoint
from .stateful import Feedback, Stateful

GivenInput = Union[SearchStrategy, InferType]


def create_test(
    *,
    endpoint: Endpoint,
    test: Callable,
    settings: Optional[hypothesis.settings] = None,
    seed: Optional[int] = None,
    data_generation_method: DataGenerationMethod = DataGenerationMethod.default(),
    media_type: Optional[str] = None,
    _given_args: Tuple[GivenInput, ...] = (),
    _given_kwargs: Optional[Dict[str, GivenInput]] = None,
) -> Callable:
    """Create a Hypothesis test."""
    hook_dispatcher = getattr(test, "_schemathesis_hooks", None)
    feedback: Optional[Feedback]
    if endpoint.schema.stateful == Stateful.links:
        feedback = Feedback(endpoint.schema.stateful, endpoint)
    else:
        feedback = None
    strategy = endpoint.as_strategy(
        hooks=hook_dispatcher, feedback=feedback, data_generation_method=data_generation_method, media_type=media_type
    )
    _given_kwargs = (_given_kwargs or {}).copy()
    _given_kwargs.setdefault("case", strategy)
    wrapped_test = hypothesis.given(*_given_args, **_given_kwargs)(test)
    if seed is not None:
        wrapped_test = hypothesis.seed(seed)(wrapped_test)
    if asyncio.iscoroutinefunction(test):
        wrapped_test.hypothesis.inner_test = make_async_test(test)  # type: ignore
    setup_default_deadline(wrapped_test)
    if settings is not None:
        wrapped_test = settings(wrapped_test)
    wrapped_test._schemathesis_feedback = feedback  # type: ignore
    return add_examples(wrapped_test, endpoint, hook_dispatcher=hook_dispatcher)


def setup_default_deadline(wrapped_test: Callable) -> None:
    # Quite hacky, but it is the simplest way to set up the default deadline value without affecting non-Schemathesis
    # tests globally
    existing_settings = getattr(wrapped_test, "_hypothesis_internal_use_settings", None)
    if existing_settings is not None and existing_settings.deadline == hypothesis.settings.default.deadline:
        new_settings = hypothesis.settings(existing_settings, deadline=DEFAULT_DEADLINE)
        wrapped_test._hypothesis_internal_use_settings = new_settings  # type: ignore


def make_test_or_exception(
    endpoint: Endpoint,
    func: Callable,
    media_type: Optional[str],
    settings: Optional[hypothesis.settings] = None,
    seed: Optional[int] = None,
    data_generation_method: DataGenerationMethod = DataGenerationMethod.default(),
) -> Union[Callable, InvalidSchema]:
    try:
        return create_test(
            endpoint=endpoint,
            test=func,
            settings=settings,
            seed=seed,
            data_generation_method=data_generation_method,
            media_type=media_type,
        )
    except InvalidSchema as exc:
        return exc


def make_async_test(test: Callable) -> Callable:
    def async_run(*args: Any, **kwargs: Any) -> None:
        loop = asyncio.get_event_loop()
        coro = test(*args, **kwargs)
        future = asyncio.ensure_future(coro, loop=loop)
        loop.run_until_complete(future)

    return async_run


def add_examples(test: Callable, endpoint: Endpoint, hook_dispatcher: Optional[HookDispatcher] = None) -> Callable:
    """Add examples to the Hypothesis test, if they are specified in the schema."""
    examples: List[Case] = [get_single_example(strategy) for strategy in endpoint.get_strategies_from_examples()]
    context = HookContext(endpoint)  # context should be passed here instead
    GLOBAL_HOOK_DISPATCHER.dispatch("before_add_examples", context, examples)
    endpoint.schema.hooks.dispatch("before_add_examples", context, examples)
    if hook_dispatcher:
        hook_dispatcher.dispatch("before_add_examples", context, examples)
    for example in examples:
        test = hypothesis.example(case=example)(test)
    return test


def get_single_example(strategy: st.SearchStrategy[Case]) -> Case:
    @hypothesis.given(strategy)  # type: ignore
    @hypothesis.settings(  # type: ignore
        database=None,
        max_examples=1,
        deadline=None,
        verbosity=hypothesis.Verbosity.quiet,
        phases=(hypothesis.Phase.generate,),
        suppress_health_check=hypothesis.HealthCheck.all(),
    )
    def example_generating_inner_function(ex: Case) -> None:
        examples.append(ex)

    examples: List[Case] = []
    example_generating_inner_function()
    return examples[0]
