"""Provide strategies for given endpoint(s) definition."""
from typing import Callable

import attr
import hypothesis.strategies as st
from hypothesis import given
from hypothesis_jsonschema import from_schema

from .schemas import Endpoint
from .types import Body, Headers, PathParameters, Query


@attr.s(slots=True)
class Case:
    """A single test case parameters."""

    path: str = attr.ib()
    method: str = attr.ib()
    path_parameters: PathParameters = attr.ib()
    headers: Headers = attr.ib()
    query: Query = attr.ib()
    body: Body = attr.ib()

    @property
    def formatted_path(self) -> str:
        # pylint: disable=not-a-mapping
        return self.path.format(**self.path_parameters)


def create_hypothesis_test(endpoint: Endpoint, test: Callable) -> Callable:
    """Create a Hypothesis test."""
    strategy = get_case_strategy(endpoint)
    return given(case=strategy)(test)


def get_case_strategy(endpoint: Endpoint) -> st.SearchStrategy:
    return st.builds(
        Case,
        path=st.just(endpoint.path),
        method=st.just(endpoint.method),
        path_parameters=from_schema(endpoint.path_parameters),
        headers=from_schema(endpoint.headers),
        query=from_schema(endpoint.query),
        body=from_schema(endpoint.body),
    )
