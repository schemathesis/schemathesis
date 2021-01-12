from functools import partial
from typing import Any, Dict, Generator, List, Optional, Tuple, cast
from urllib.parse import urlsplit

import attr
import graphql
from hypothesis import strategies as st
from hypothesis.strategies import SearchStrategy
from hypothesis_graphql import strategies as gql_st

from ... import DataGenerationMethod
from ...checks import not_a_server_error
from ...exceptions import InvalidSchema
from ...hooks import HookDispatcher
from ...models import APIOperation, Case, CheckFunction
from ...schemas import BaseSchema
from ...utils import GenericResponse, Ok, Result


@attr.s()  # pragma: no mutate
class GraphQLCase(Case):
    def as_requests_kwargs(
        self, base_url: Optional[str] = None, headers: Optional[Dict[str, str]] = None
    ) -> Dict[str, Any]:
        final_headers = self._get_headers(headers)
        base_url = self._get_base_url(base_url)
        return {"method": self.method, "url": base_url, "json": {"query": self.body}, "headers": final_headers}

    def as_werkzeug_kwargs(self, headers: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
        final_headers = self._get_headers(headers)
        return {
            "method": self.method,
            "path": self.operation.schema.get_full_path(self.formatted_path),
            "headers": final_headers,
            "query_string": self.query,
            "json": {"query": self.body},
        }

    def validate_response(
        self,
        response: GenericResponse,
        checks: Tuple[CheckFunction, ...] = (),
        additional_checks: Tuple[CheckFunction, ...] = (),
    ) -> None:
        checks = checks or (not_a_server_error,)
        checks += additional_checks
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

    def get_all_operations(self) -> Generator[Result[APIOperation, InvalidSchema], None, None]:
        yield Ok(
            APIOperation(
                base_url=self.get_base_url(), path=self.base_path, method="POST", schema=self, definition=None  # type: ignore
            )
        )

    def get_case_strategy(
        self,
        operation: APIOperation,
        hooks: Optional[HookDispatcher] = None,
        data_generation_method: DataGenerationMethod = DataGenerationMethod.default(),
    ) -> SearchStrategy:
        constructor = partial(GraphQLCase, operation=operation)
        schema = graphql.build_client_schema(self.raw_schema)
        return st.builds(constructor, body=gql_st.query(schema))

    def get_strategies_from_examples(self, operation: APIOperation) -> List[SearchStrategy[Case]]:
        return []
