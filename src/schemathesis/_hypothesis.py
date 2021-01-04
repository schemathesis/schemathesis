"""High-level API for creating Hypothesis tests."""
import asyncio
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

import hypothesis
from hypothesis import Phase
from hypothesis import strategies as st
from hypothesis.errors import Unsatisfiable
from hypothesis.strategies import SearchStrategy
from hypothesis.utils.conventions import InferType
from hypothesis_jsonschema._canonicalise import HypothesisRefResolutionError

from .constants import DEFAULT_DEADLINE, DataGenerationMethod
from .exceptions import InvalidSchema
from .hooks import GLOBAL_HOOK_DISPATCHER, HookContext, HookDispatcher
from .models import APIOperation, Case

GivenInput = Union[SearchStrategy, InferType]


def create_test(
    *,
    operation: APIOperation,
    test: Callable,
    settings: Optional[hypothesis.settings] = None,
    seed: Optional[int] = None,
    data_generation_method: DataGenerationMethod = DataGenerationMethod.default(),
    _given_args: Tuple[GivenInput, ...] = (),
    _given_kwargs: Optional[Dict[str, GivenInput]] = None,
) -> Callable:
    """Create a Hypothesis test."""
    hook_dispatcher = getattr(test, "_schemathesis_hooks", None)
    strategy = operation.as_strategy(hooks=hook_dispatcher, data_generation_method=data_generation_method)
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
    existing_settings = getattr(wrapped_test, "_hypothesis_internal_use_settings", None)
    if existing_settings and Phase.explicit in existing_settings.phases:
        wrapped_test = add_examples(wrapped_test, operation, hook_dispatcher=hook_dispatcher)
    return wrapped_test


def setup_default_deadline(wrapped_test: Callable) -> None:
    # Quite hacky, but it is the simplest way to set up the default deadline value without affecting non-Schemathesis
    # tests globally
    existing_settings = getattr(wrapped_test, "_hypothesis_internal_use_settings", None)
    if existing_settings is not None and existing_settings.deadline == hypothesis.settings.default.deadline:
        new_settings = hypothesis.settings(existing_settings, deadline=DEFAULT_DEADLINE)
        wrapped_test._hypothesis_internal_use_settings = new_settings  # type: ignore


def make_async_test(test: Callable) -> Callable:
    def async_run(*args: Any, **kwargs: Any) -> None:
        loop = asyncio.get_event_loop()
        coro = test(*args, **kwargs)
        future = asyncio.ensure_future(coro, loop=loop)
        loop.run_until_complete(future)

    return async_run


def add_examples(test: Callable, operation: APIOperation, hook_dispatcher: Optional[HookDispatcher] = None) -> Callable:
    """Add examples to the Hypothesis test, if they are specified in the schema."""
    try:
        examples: List[Case] = [get_single_example(strategy) for strategy in operation.get_strategies_from_examples()]
    except (InvalidSchema, HypothesisRefResolutionError, Unsatisfiable):
        # Invalid schema:
        # In this case, the user didn't pass `--validate-schema=false` and see an error in the output anyway,
        # and no tests will be executed. For this reason, examples can be skipped
        # Recursive references: This test will be skipped anyway
        # Unsatisfiable:
        # The underlying schema is not satisfiable and test will raise an error for the same reason.
        # Skipping this exception here allows us to continue the testing process for other operations.
        # Still, we allow to run user-defined hooks
        examples = []
    context = HookContext(operation)  # context should be passed here instead
    GLOBAL_HOOK_DISPATCHER.dispatch("before_add_examples", context, examples)
    operation.schema.hooks.dispatch("before_add_examples", context, examples)
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
