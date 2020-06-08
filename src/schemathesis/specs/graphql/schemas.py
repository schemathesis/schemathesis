from functools import partial

import attr
import graphql
import requests
from hypothesis import strategies as st
from hypothesis_graphql import strategies as gql_st

from ...schemas import BaseSchema


@attr.s()  # pragma: no mutate
class GraphQLCase:
    path: str = attr.ib()  # pragma: no mutate
    data: str = attr.ib()  # pragma: no mutate

    def call(self) -> requests.Response:
        return requests.post(self.path, json={"query": self.data})


@attr.s(slots=True)  # pragma: no mutate
class GraphQLQuery:
    path: str = attr.ib()  # pragma: no mutate
    schema: graphql.GraphQLSchema = attr.ib()  # pragma: no mutate

    def as_strategy(self) -> st.SearchStrategy[GraphQLCase]:
        constructor = partial(GraphQLCase, path=self.path)
        return st.builds(constructor, data=gql_st.query(self.schema))


@attr.s()  # pragma: no mutate
class GraphQLSchema(BaseSchema):
    schema: graphql.GraphQLSchema = attr.ib(init=False)  # pragma: no mutate

    def __attrs_post_init__(self) -> None:
        self.schema = graphql.build_client_schema(self.raw_schema)

    @property  # pragma: no mutate
    def verbose_name(self) -> str:
        return "GraphQL"

    @property
    def query(self) -> GraphQLQuery:
        return GraphQLQuery(path=self.location or "", schema=self.schema)
