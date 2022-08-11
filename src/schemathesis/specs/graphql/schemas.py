# Pylint bug - `Callable` is used below
# pylint: disable=unused-import
import enum
from enum import unique
from typing import Any, Callable, Dict, Generator, List, Optional, Sequence, Tuple, Type, TypeVar, Union, cast
from urllib.parse import urlsplit

import attr
import graphql
import requests
from hypothesis import strategies as st
from hypothesis.strategies import SearchStrategy
from hypothesis_graphql import strategies as gql_st
from requests.structures import CaseInsensitiveDict

from ... import auth
from ...auth import AuthStorage
from ...checks import not_a_server_error
from ...constants import DataGenerationMethod
from ...exceptions import InvalidSchema
from ...hooks import HookDispatcher
from ...models import APIOperation, Case, CheckFunction, OperationDefinition
from ...schemas import BaseSchema
from ...stateful import Stateful, StatefulTest
from ...types import Body, Cookies, Headers, NotSet, PathParameters, Query
from ...utils import NOT_SET, GenericResponse, Ok, Result
from .scalars import CUSTOM_SCALARS


@unique
class RootType(enum.Enum):
    QUERY = enum.auto()
    MUTATION = enum.auto()


@attr.s(slots=True, repr=False)  # pragma: no mutate
class GraphQLCase(Case):
    def as_requests_kwargs(
        self, base_url: Optional[str] = None, headers: Optional[Dict[str, str]] = None
    ) -> Dict[str, Any]:
        final_headers = self._get_headers(headers)
        base_url = self._get_base_url(base_url)
        kwargs: Dict[str, Any] = {"method": self.method, "url": base_url, "headers": final_headers}
        # There is no direct way to have bytes here, but it is a useful pattern to support.
        # It also unifies GraphQLCase with its Open API counterpart where bytes may come from external examples
        if isinstance(self.body, bytes):
            kwargs["data"] = self.body
            # Assume that the payload is JSON, not raw GraphQL queries
            kwargs["headers"].setdefault("Content-Type", "application/json")
        else:
            kwargs["json"] = {"query": self.body}
        return kwargs

    def as_werkzeug_kwargs(self, headers: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
        final_headers = self._get_headers(headers)
        return {
            "method": self.method,
            "path": self.operation.schema.get_full_path(self.formatted_path),
            # Convert to a regular dictionary, as we use `CaseInsensitiveDict` which is not supported by Werkzeug
            "headers": dict(final_headers),
            "query_string": self.query,
            "json": {"query": self.body},
        }

    def validate_response(
        self,
        response: GenericResponse,
        checks: Tuple[CheckFunction, ...] = (),
        additional_checks: Tuple[CheckFunction, ...] = (),
        code_sample_style: Optional[str] = None,
    ) -> None:
        checks = checks or (not_a_server_error,)
        checks += additional_checks
        return super().validate_response(response, checks, code_sample_style=code_sample_style)

    def call_asgi(
        self,
        app: Any = None,
        base_url: Optional[str] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs: Any,
    ) -> requests.Response:
        return super().call_asgi(app=app, base_url=base_url, headers=headers, **kwargs)


C = TypeVar("C", bound=Case)


@attr.s()
class GraphQLOperationDefinition(OperationDefinition):
    field_name: str = attr.ib()
    type_: graphql.GraphQLType = attr.ib()
    root_type: RootType = attr.ib()


@attr.s()  # pragma: no mutate
class GraphQLSchema(BaseSchema):
    def get_full_path(self, path: str) -> str:
        return self.base_path

    @property  # pragma: no mutate
    def verbose_name(self) -> str:
        return "GraphQL"

    @property
    def client_schema(self) -> graphql.GraphQLSchema:
        if not hasattr(self, "_client_schema"):
            # pylint: disable=attribute-defined-outside-init
            self._client_schema = graphql.build_client_schema(self.raw_schema)
        return self._client_schema

    @property
    def base_path(self) -> str:
        if self.base_url:
            return urlsplit(self.base_url).path
        return self._get_base_path()

    def _get_base_path(self) -> str:
        return cast(str, urlsplit(self.location).path)

    @property
    def operations_count(self) -> int:
        raw_schema = self.raw_schema["__schema"]
        total = 0
        for type_name in ("queryType", "mutationType"):
            type_def = raw_schema.get(type_name)
            if type_def is not None:
                query_type_name = type_def["name"]
                for type_def in raw_schema.get("types", []):
                    if type_def["name"] == query_type_name:
                        total += len(type_def["fields"])
        return total

    def get_all_operations(self) -> Generator[Result[APIOperation, InvalidSchema], None, None]:
        schema = self.client_schema
        for root_type, operation_type in (
            (RootType.QUERY, schema.query_type),
            (RootType.MUTATION, schema.mutation_type),
        ):
            if operation_type is None:
                continue
            for field_name, definition in operation_type.fields.items():
                yield Ok(
                    APIOperation(
                        base_url=self.get_base_url(),
                        path=self.base_path,
                        verbose_name=f"{operation_type.name}.{field_name}",
                        method="POST",
                        app=self.app,
                        schema=self,
                        # Parameters are not yet supported
                        definition=GraphQLOperationDefinition(
                            raw=definition,
                            resolved=definition,
                            scope="",
                            parameters=[],
                            type_=operation_type,
                            field_name=field_name,
                            root_type=root_type,
                        ),
                        case_cls=GraphQLCase,
                    )
                )

    def get_case_strategy(
        self,
        operation: APIOperation,
        hooks: Optional[HookDispatcher] = None,
        auth_storage: Optional[AuthStorage] = None,
        data_generation_method: DataGenerationMethod = DataGenerationMethod.default(),
    ) -> SearchStrategy:
        return get_case_strategy(
            operation=operation,
            client_schema=self.client_schema,
            hooks=hooks,
            auth_storage=auth_storage,
            data_generation_method=data_generation_method,
        )

    def get_strategies_from_examples(self, operation: APIOperation) -> List[SearchStrategy[Case]]:
        return []

    def get_stateful_tests(
        self, response: GenericResponse, operation: APIOperation, stateful: Optional[Stateful]
    ) -> Sequence[StatefulTest]:
        return []

    def make_case(
        self,
        *,
        case_cls: Type[C],
        operation: APIOperation,
        path_parameters: Optional[PathParameters] = None,
        headers: Optional[Headers] = None,
        cookies: Optional[Cookies] = None,
        query: Optional[Query] = None,
        body: Union[Body, NotSet] = NOT_SET,
        media_type: Optional[str] = None,
    ) -> C:
        return case_cls(
            operation=operation,
            path_parameters=path_parameters,
            headers=CaseInsensitiveDict(headers) if headers is not None else headers,
            cookies=cookies,
            query=query,
            body=body,
            media_type=media_type,
        )


@st.composite  # type: ignore
def get_case_strategy(
    draw: Callable,
    operation: APIOperation,
    client_schema: graphql.GraphQLSchema,
    hooks: Optional[HookDispatcher] = None,
    auth_storage: Optional[AuthStorage] = None,
    data_generation_method: DataGenerationMethod = DataGenerationMethod.default(),
) -> Any:
    definition = cast(GraphQLOperationDefinition, operation.definition)
    strategy = {
        RootType.QUERY: gql_st.queries,
        RootType.MUTATION: gql_st.mutations,
    }[definition.root_type]
    body = draw(strategy(client_schema, fields=[definition.field_name], custom_scalars=CUSTOM_SCALARS))
    instance = GraphQLCase(body=body, operation=operation, data_generation_method=data_generation_method)  # type: ignore
    context = auth.AuthContext(
        operation=operation,
        app=operation.app,
    )
    auth.set_on_case(instance, context, auth_storage)
    return instance
