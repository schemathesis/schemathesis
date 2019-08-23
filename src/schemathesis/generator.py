"""Provide strategies for given endpoint(s) definition."""
from typing import Any, Dict

import attr
import hypothesis.strategies as st
from hypothesis_jsonschema import from_schema

from .schemas import Endpoint
from .types import Body, ParametersList, PathParameters, Query

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
        path_parameters=get_parameters_strategy(endpoint.path_parameters),
        query=get_parameters_strategy(endpoint.query),
        body=get_parameters_strategy(endpoint.body),
    )


def get_parameters_strategy(parameters: ParametersList) -> st.SearchStrategy:
    # TODO. Fixed dicts? what about optional parameters?
    return st.fixed_dictionaries({item["name"]: get_strategy(item) for item in parameters})


def get_strategy(item: Dict[str, Any]) -> st.SearchStrategy:
    if "schema" in item:
        item = item["schema"]
    if not isinstance(item.get("required"), list):
        item.pop("required", None)
    return from_schema(item)
