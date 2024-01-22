from __future__ import annotations
import enum
from dataclasses import dataclass
from enum import unique
from typing import (
    Any,
    Callable,
    Generator,
    Sequence,
    TypeVar,
    cast,
    TYPE_CHECKING,
)
from urllib.parse import urlsplit, unquote, urljoin, quote

import graphql
import requests
from hypothesis import strategies as st
from hypothesis.strategies import SearchStrategy
from hypothesis_graphql import strategies as gql_st
from requests.structures import CaseInsensitiveDict

from ..openapi.constants import LOCATION_TO_CONTAINER
from ... import auths
from ...auths import AuthStorage
from ...checks import not_a_server_error
from ...generation import DataGenerationMethod, GenerationConfig
from ...exceptions import OperationSchemaError
from ...constants import NOT_SET
from ...hooks import (
    GLOBAL_HOOK_DISPATCHER,
    HookContext,
    HookDispatcher,
    apply_to_all_dispatchers,
    should_skip_operation,
)
from ...internal.result import Result, Ok
from ...models import APIOperation, Case, CheckFunction, OperationDefinition
from ...schemas import BaseSchema
from ...stateful import Stateful, StatefulTest
from ...types import Body, Cookies, Headers, NotSet, PathParameters, Query
from .scalars import CUSTOM_SCALARS, get_extra_scalar_strategies

if TYPE_CHECKING:
    from ...transports.responses import GenericResponse


@unique
class RootType(enum.Enum):
    QUERY = enum.auto()
    MUTATION = enum.auto()


@dataclass(repr=False)
class GraphQLCase(Case):
    def as_requests_kwargs(self, base_url: str | None = None, headers: dict[str, str] | None = None) -> dict[str, Any]:
        final_headers = self._get_headers(headers)
        base_url = self._get_base_url(base_url)
        formatted_path = self.formatted_path.lstrip("/")
        url = unquote(urljoin(base_url, quote(formatted_path)))
        kwargs: dict[str, Any] = {
            "method": self.method,
            "url": url,
            "headers": final_headers,
            "cookies": self.cookies,
            "params": self.query,
        }
        # There is no direct way to have bytes here, but it is a useful pattern to support.
        # It also unifies GraphQLCase with its Open API counterpart where bytes may come from external examples
        if isinstance(self.body, bytes):
            kwargs["data"] = self.body
            # Assume that the payload is JSON, not raw GraphQL queries
            kwargs["headers"].setdefault("Content-Type", "application/json")
        else:
            kwargs["json"] = {"query": self.body}
        return kwargs

    def as_werkzeug_kwargs(self, headers: dict[str, str] | None = None) -> dict[str, Any]:
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
        checks: tuple[CheckFunction, ...] = (),
        additional_checks: tuple[CheckFunction, ...] = (),
        excluded_checks: tuple[CheckFunction, ...] = (),
        code_sample_style: str | None = None,
    ) -> None:
        checks = checks or (not_a_server_error,)
        checks += additional_checks
        checks = tuple(check for check in checks if check not in excluded_checks)
        return super().validate_response(response, checks, code_sample_style=code_sample_style)

    def call_asgi(
        self,
        app: Any = None,
        base_url: str | None = None,
        headers: dict[str, str] | None = None,
        **kwargs: Any,
    ) -> requests.Response:
        return super().call_asgi(app=app, base_url=base_url, headers=headers, **kwargs)


C = TypeVar("C", bound=Case)


@dataclass
class GraphQLOperationDefinition(OperationDefinition):
    field_name: str
    type_: graphql.GraphQLType
    root_type: RootType

    @property
    def is_query(self) -> bool:
        return self.root_type == RootType.QUERY

    @property
    def is_mutation(self) -> bool:
        return self.root_type == RootType.MUTATION


@dataclass
class GraphQLSchema(BaseSchema):
    def get_full_path(self, path: str) -> str:
        return self.base_path

    @property
    def verbose_name(self) -> str:
        return "GraphQL"

    @property
    def client_schema(self) -> graphql.GraphQLSchema:
        if not hasattr(self, "_client_schema"):
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

    @property
    def links_count(self) -> int:
        # Links are not supported for GraphQL
        return 0

    def get_all_operations(
        self, hooks: HookDispatcher | None = None
    ) -> Generator[Result[APIOperation, OperationSchemaError], None, None]:
        schema = self.client_schema
        for root_type, operation_type in (
            (RootType.QUERY, schema.query_type),
            (RootType.MUTATION, schema.mutation_type),
        ):
            if operation_type is None:
                continue
            for field_name, definition in operation_type.fields.items():
                operation: APIOperation = APIOperation(
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
                context = HookContext(operation=operation)
                if (
                    should_skip_operation(GLOBAL_HOOK_DISPATCHER, context)
                    or should_skip_operation(self.hooks, context)
                    or (hooks and should_skip_operation(hooks, context))
                ):
                    continue
                yield Ok(operation)

    def get_case_strategy(
        self,
        operation: APIOperation,
        hooks: HookDispatcher | None = None,
        auth_storage: AuthStorage | None = None,
        data_generation_method: DataGenerationMethod = DataGenerationMethod.default(),
        generation_config: GenerationConfig | None = None,
        **kwargs: Any,
    ) -> SearchStrategy:
        return get_case_strategy(
            operation=operation,
            client_schema=self.client_schema,
            hooks=hooks,
            auth_storage=auth_storage,
            data_generation_method=data_generation_method,
            generation_config=generation_config,
            **kwargs,
        )

    def get_strategies_from_examples(self, operation: APIOperation) -> list[SearchStrategy[Case]]:
        return []

    def get_stateful_tests(
        self, response: GenericResponse, operation: APIOperation, stateful: Stateful | None
    ) -> Sequence[StatefulTest]:
        return []

    def make_case(
        self,
        *,
        case_cls: type[C],
        operation: APIOperation,
        path_parameters: PathParameters | None = None,
        headers: Headers | None = None,
        cookies: Cookies | None = None,
        query: Query | None = None,
        body: Body | NotSet = NOT_SET,
        media_type: str | None = None,
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
    hooks: HookDispatcher | None = None,
    auth_storage: AuthStorage | None = None,
    data_generation_method: DataGenerationMethod = DataGenerationMethod.default(),
    generation_config: GenerationConfig | None = None,
    **kwargs: Any,
) -> Any:
    definition = cast(GraphQLOperationDefinition, operation.definition)
    strategy_factory = {
        RootType.QUERY: gql_st.queries,
        RootType.MUTATION: gql_st.mutations,
    }[definition.root_type]
    hook_context = HookContext(operation)
    generation_config = generation_config or GenerationConfig()
    custom_scalars = {**get_extra_scalar_strategies(), **CUSTOM_SCALARS}
    strategy = strategy_factory(
        client_schema,
        fields=[definition.field_name],
        custom_scalars=custom_scalars,
        print_ast=_noop,  # type: ignore
        allow_x00=generation_config.allow_x00,
        codec=generation_config.codec,
    )
    strategy = apply_to_all_dispatchers(operation, hook_context, hooks, strategy, "body").map(graphql.print_ast)
    body = draw(strategy)

    path_parameters_ = _generate_parameter("path", draw, operation, hook_context, hooks)
    headers_ = _generate_parameter("header", draw, operation, hook_context, hooks)
    cookies_ = _generate_parameter("cookie", draw, operation, hook_context, hooks)
    query_ = _generate_parameter("query", draw, operation, hook_context, hooks)

    instance = GraphQLCase(
        path_parameters=path_parameters_,
        headers=headers_,
        cookies=cookies_,
        query=query_,
        body=body,
        operation=operation,
        data_generation_method=data_generation_method,
    )  # type: ignore
    context = auths.AuthContext(
        operation=operation,
        app=operation.app,
    )
    auths.set_on_case(instance, context, auth_storage)
    return instance


def _generate_parameter(
    location: str, draw: Callable, operation: APIOperation, context: HookContext, hooks: HookDispatcher | None
) -> Any:
    # Schemathesis does not generate anything but `body` for GraphQL, hence use `None`
    container = LOCATION_TO_CONTAINER[location]
    strategy = apply_to_all_dispatchers(operation, context, hooks, st.none(), container)
    return draw(strategy)


def _noop(node: graphql.Node) -> graphql.Node:
    return node
