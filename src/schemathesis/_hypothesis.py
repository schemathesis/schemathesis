"""Provide strategies for given endpoint(s) definition."""
from typing import Callable, Generator

import hypothesis.strategies as st
from hypothesis import example, given
from hypothesis._strategies import just
from hypothesis_jsonschema import from_schema

from .models import Case, Endpoint

PARAMETERS = frozenset(("path_parameters", "headers", "cookies", "query", "body"))


def create_test(endpoint: Endpoint, test: Callable) -> Callable:
    """Create a Hypothesis test."""
    strategy = get_case_strategy(endpoint)
    wrapped_test = given(case=strategy)(test)
    return add_examples(wrapped_test, endpoint)


def get_examples(endpoint: Endpoint) -> Generator[Case, None, None]:
    for name in PARAMETERS:
        parameter = getattr(endpoint, name)
        if "example" in parameter:
            other_parameters = {other: from_schema(getattr(endpoint, other)) for other in PARAMETERS - {name}}
            yield st.builds(
                Case,
                path=st.just(endpoint.path),
                method=st.just(endpoint.method),
                **{name: just(parameter["example"])},
                **other_parameters,
            ).example()


def add_examples(test: Callable, endpoint: Endpoint) -> Callable:
    """Add examples to the Hypothesis test, if they are specified in the schema."""
    for case in get_examples(endpoint):
        test = example(case)(test)
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
    )
