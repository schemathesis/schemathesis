from __future__ import annotations

import enum
import time
from dataclasses import dataclass, field
from difflib import get_close_matches
from enum import unique
from types import SimpleNamespace
from typing import (
    TYPE_CHECKING,
    Any,
    Callable,
    Generator,
    Iterator,
    Mapping,
    NoReturn,
    Sequence,
    TypeVar,
    cast,
)
from urllib.parse import urlsplit, urlunsplit

import graphql
from hypothesis import strategies as st
from hypothesis.strategies import SearchStrategy
from hypothesis_graphql import strategies as gql_st
from requests.structures import CaseInsensitiveDict

from ... import auths
from ...auths import AuthStorage
from ...checks import not_a_server_error
from ...constants import NOT_SET
from ...exceptions import OperationNotFound, OperationSchemaError
from ...generation import DataGenerationMethod, GenerationConfig
from ...hooks import (
    GLOBAL_HOOK_DISPATCHER,
    HookContext,
    HookDispatcher,
    apply_to_all_dispatchers,
    should_skip_operation,
)
from ...internal.result import Ok, Result
from ...models import APIOperation, Case, CheckFunction, OperationDefinition
from ...schemas import APIOperationMap, BaseSchema
from ...stateful import Stateful, StatefulTest
from ...types import Body, Cookies, Headers, NotSet, PathParameters, Query
from ..openapi.constants import LOCATION_TO_CONTAINER
from ._cache import OperationCache
from .scalars import CUSTOM_SCALARS, get_extra_scalar_strategies

if TYPE_CHECKING:
    from ...transports.responses import GenericResponse


@unique
class RootType(enum.Enum):
    QUERY = enum.auto()
    MUTATION = enum.auto()


@dataclass(repr=False)
class GraphQLCase(Case):
    def _get_url(self, base_url: str | None) -> str:
        base_url = self._get_base_url(base_url)
        # Replace the path, in case if the user provided any path parameters via hooks
        parts = list(urlsplit(base_url))
        parts[2] = self.formatted_path
        return urlunsplit(parts)

    def _get_body(self) -> Body | NotSet:
        return self.body if isinstance(self.body, (NotSet, bytes)) else {"query": self.body}

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
    _operation_cache: OperationCache = field(default_factory=OperationCache)

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__}>"

    def __iter__(self) -> Iterator[str]:
        schema = self.client_schema
        for operation_type in (
            schema.query_type,
            schema.mutation_type,
        ):
            if operation_type is not None:
                yield operation_type.name

    def _get_operation_map(self, key: str) -> APIOperationMap:
        cache = self._operation_cache
        map = cache.get_map(key)
        if map is not None:
            return map
        schema = self.client_schema
        for root_type, operation_type in (
            (RootType.QUERY, schema.query_type),
            (RootType.MUTATION, schema.mutation_type),
        ):
            if operation_type and operation_type.name == key:
                map = APIOperationMap(self, {})
                map._data = FieldMap(map, root_type, operation_type)
                cache.insert_map(key, map)
                return map
        raise KeyError(key)

    def on_missing_operation(self, item: str, exc: KeyError) -> NoReturn:
        raw_schema = self.raw_schema["__schema"]
        type_names = [type_def["name"] for type_def in raw_schema.get("types", [])]
        matches = get_close_matches(item, type_names)
        message = f"`{item}` type not found"
        if matches:
            message += f". Did you mean `{matches[0]}`?"
        raise OperationNotFound(message=message, item=item) from exc

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
        self,
        hooks: HookDispatcher | None = None,
    ) -> Generator[Result[APIOperation, OperationSchemaError], None, None]:
        schema = self.client_schema
        for root_type, operation_type in (
            (RootType.QUERY, schema.query_type),
            (RootType.MUTATION, schema.mutation_type),
        ):
            if operation_type is None:
                continue
            for field_name, field_ in operation_type.fields.items():
                operation = self._build_operation(root_type, operation_type, field_name, field_)
                if self._should_skip(operation):
                    continue
                context = HookContext(operation=operation)
                if (
                    should_skip_operation(GLOBAL_HOOK_DISPATCHER, context)
                    or should_skip_operation(self.hooks, context)
                    or (hooks and should_skip_operation(hooks, context))
                ):
                    continue
                yield Ok(operation)

    def _should_skip(
        self,
        operation: APIOperation,
        _ctx_cache: SimpleNamespace = SimpleNamespace(operation=None),
    ) -> bool:
        _ctx_cache.operation = operation
        return not self.filter_set.match(_ctx_cache)

    def _build_operation(
        self,
        root_type: RootType,
        operation_type: graphql.GraphQLObjectType,
        field_name: str,
        field: graphql.GraphQlField,
    ) -> APIOperation:
        return APIOperation(
            base_url=self.get_base_url(),
            path=self.base_path,
            verbose_name=f"{operation_type.name}.{field_name}",
            method="POST",
            app=self.app,
            schema=self,
            # Parameters are not yet supported
            definition=GraphQLOperationDefinition(
                raw=field,
                resolved=field,
                scope="",
                type_=operation_type,
                field_name=field_name,
                root_type=root_type,
            ),
            case_cls=GraphQLCase,
        )

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
            generation_config=generation_config or self.generation_config,
            **kwargs,
        )

    def get_strategies_from_examples(
        self, operation: APIOperation, as_strategy_kwargs: dict[str, Any] | None = None
    ) -> list[SearchStrategy[Case]]:
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
            media_type=media_type or "application/json",
            generation_time=0.0,
        )

    def get_tags(self, operation: APIOperation) -> list[str] | None:
        return None

    def validate(self) -> None:
        return None


@dataclass
class FieldMap(Mapping):
    """Container for accessing API operations.

    Provides a more specific error message if API operation is not found.
    """

    _parent: APIOperationMap
    _root_type: RootType
    _operation_type: graphql.GraphQLObjectType

    __slots__ = ("_parent", "_root_type", "_operation_type")

    def __len__(self) -> int:
        return len(self._operation_type.fields)

    def __iter__(self) -> Iterator[str]:
        return iter(self._operation_type.fields)

    def _init_operation(self, field_name: str) -> APIOperation:
        schema = cast(GraphQLSchema, self._parent._schema)
        cache = schema._operation_cache
        operation = cache.get_operation(field_name)
        if operation is not None:
            return operation
        operation_type = self._operation_type
        field_ = operation_type.fields[field_name]
        operation = schema._build_operation(self._root_type, operation_type, field_name, field_)
        cache.insert_operation(field_name, operation)
        return operation

    def __getitem__(self, item: str) -> APIOperation:
        try:
            return self._init_operation(item)
        except KeyError as exc:
            field_names = list(self._operation_type.fields)
            matches = get_close_matches(item, field_names)
            message = f"`{item}` field not found"
            if matches:
                message += f". Did you mean `{matches[0]}`?"
            raise KeyError(message) from exc


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
    start = time.monotonic()
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
        allow_null=generation_config.graphql_allow_null,
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
        generation_time=time.monotonic() - start,
        media_type="application/json",
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
