"""Provide strategies for given endpoint(s) definition."""
import asyncio
from typing import Any, Callable, Generator, Optional

import hypothesis
import hypothesis.strategies as st
from hypothesis_jsonschema import from_schema

from ._compat import handle_warnings
from .models import Case, Endpoint

PARAMETERS = frozenset(("path_parameters", "headers", "cookies", "query", "body", "form_data"))


def create_test(endpoint: Endpoint, test: Callable, settings: Optional[hypothesis.settings] = None) -> Callable:
    """Create a Hypothesis test."""
    strategy = endpoint.as_strategy()
    wrapped_test = hypothesis.given(case=strategy)(test)
    original_test = get_original_test(test)
    if asyncio.iscoroutinefunction(original_test):
        wrapped_test.hypothesis.inner_test = make_async_test(original_test)  # type: ignore
    if settings is not None:
        wrapped_test = settings(wrapped_test)
    return add_examples(wrapped_test, endpoint)


def get_original_test(test: Callable) -> Callable:
    """Get the original test function even if it is wrapped by `hypothesis.settings` decorator."""
    if getattr(test, "_hypothesis_internal_settings_applied", False):
        # `settings` decorator is applied
        return test._hypothesis_internal_test_function_without_warning  # type: ignore
    return test


def make_async_test(test: Callable) -> Callable:
    def async_run(*args: Any, **kwargs: Any) -> None:
        loop = asyncio.get_event_loop()
        coro = test(*args, **kwargs)
        future = asyncio.ensure_future(coro, loop=loop)
        loop.run_until_complete(future)

    return async_run


def get_examples(endpoint: Endpoint) -> Generator[Case, None, None]:
    for name in PARAMETERS:
        parameter = getattr(endpoint, name)
        if "example" in parameter:
            with handle_warnings():
                other_parameters = {other: from_schema(getattr(endpoint, other)) for other in PARAMETERS - {name}}
                yield st.builds(
                    Case,
                    path=st.just(endpoint.path),
                    method=st.just(endpoint.method),
                    **{name: st.just(parameter["example"])},
                    **other_parameters,
                ).example()


def add_examples(test: Callable, endpoint: Endpoint) -> Callable:
    """Add examples to the Hypothesis test, if they are specified in the schema."""
    for case in get_examples(endpoint):
        test = hypothesis.example(case)(test)
    return test


def get_case_strategy(endpoint: Endpoint) -> st.SearchStrategy:
    """Create a strategy for a complete test case.

    Path & endpoint are static, the others are JSON schemas.
    """
    return st.builds(
        Case,
        path=st.just(endpoint.path),
        method=st.just(endpoint.method),
        path_parameters=from_schema(endpoint.path_parameters),
        headers=from_schema(endpoint.headers),
        cookies=from_schema(endpoint.cookies),
        query=from_schema(endpoint.query),
        body=from_schema(endpoint.body),
        form_data=from_schema(endpoint.form_data),
    )
