from functools import partial
from typing import Any, Callable, Dict, Generator, List, Optional, Tuple, cast
from urllib.parse import urlsplit

import attr
import graphql
from hypothesis import strategies as st
from hypothesis.strategies import SearchStrategy
from hypothesis_graphql import strategies as gql_st

from ...checks import not_a_server_error
from ...hooks import HookDispatcher
from ...models import Case, CheckFunction, Endpoint
from ...schemas import BaseSchema
from ...stateful import Feedback
from ...utils import GenericResponse


@attr.s()  # pragma: no mutate
class GraphQLCase(Case):
    def as_requests_kwargs(self, base_url: Optional[str] = None) -> Dict[str, Any]:
        base_url = self._get_base_url(base_url)
        return {"method": self.method, "url": base_url, "json": {"query": self.body}}

    def as_werkzeug_kwargs(self, headers: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
        final_headers = self.headers.copy() if self.headers is not None else {}
        if headers:
            final_headers.update(headers)
        return {
            "method": self.method,
            "path": self.endpoint.schema.get_full_path(self.formatted_path),
            "headers": final_headers,
            "query_string": self.query,
            "json": {"query": self.body},
        }

    def validate_response(
        self,
        response: GenericResponse,
        checks: Tuple[CheckFunction, ...] = (),
    ) -> None:
        checks = checks or (not_a_server_error,)
        return super().validate_response(response, checks)


@attr.s()  # pragma: no mutate
class GraphQLSchema(BaseSchema):
    def get_full_path(self, path: str) -> str:
        return self.base_path

    @property  # pragma: no mutate
    def verbose_name(self) -> str:
        return "GraphQL"

    @property
    def base_path(self) -> str:
        if self.base_url:
            return urlsplit(self.base_url).path
        return self._get_base_path()

    def _get_base_path(self) -> str:
        return cast(str, urlsplit(self.location).path)

    def get_all_endpoints(self) -> Generator[Endpoint, None, None]:
        yield Endpoint(
            base_url=self.location, path=self.base_path, method="POST", schema=self, definition=None  # type: ignore
        )

    def get_case_strategy(
        self, endpoint: Endpoint, hooks: Optional[HookDispatcher] = None, feedback: Optional[Feedback] = None
    ) -> SearchStrategy:
        constructor = partial(GraphQLCase, endpoint=endpoint)
        schema = graphql.build_client_schema(self.raw_schema)
        return st.builds(constructor, body=gql_st.query(schema))

    def get_strategies_from_examples(self, endpoint: Endpoint) -> List[SearchStrategy[Case]]:
        return []

    def get_hypothesis_conversion(self, endpoint: Endpoint, location: str) -> Optional[Callable]:
        return None
