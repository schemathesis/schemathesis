"""Provide strategies for given endpoint(s) definition."""
import attr
import hypothesis.strategies as st
from hypothesis_jsonschema import from_schema

from .schemas import Endpoint
from .types import Body, PathParameters, Query

# TODO. Better naming


@attr.s(slots=True)
class Case:
    """A single test case parameters."""

    path: str = attr.ib()
    path_parameters: PathParameters = attr.ib()
    method: str = attr.ib()
    query: Query = attr.ib()
    body: Body = attr.ib()

    @property
    def formatted_path(self) -> str:
        # pylint: disable=not-a-mapping
        return self.path.format(**self.path_parameters)


def get_case_strategy(endpoint: Endpoint) -> st.SearchStrategy:
    return st.builds(
        Case,
        path=st.just(endpoint.path),
        method=st.just(endpoint.method),
        path_parameters=from_schema(endpoint.path_parameters),
        query=from_schema(endpoint.query),
        body=from_schema(endpoint.body),
    )
