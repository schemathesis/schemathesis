from __future__ import annotations

import enum
import time
from collections.abc import Callable, Generator, Iterator, Mapping
from dataclasses import dataclass
from difflib import get_close_matches
from enum import unique
from types import SimpleNamespace
from typing import (
    TYPE_CHECKING,
    Any,
    NoReturn,
    cast,
)
from urllib.parse import urlsplit

from hypothesis import strategies as st
from requests.structures import CaseInsensitiveDict

from schemathesis import auths
from schemathesis.core import NOT_SET, NotSet, Specification
from schemathesis.core.errors import InvalidSchema, OperationNotFound
from schemathesis.core.parameters import ParameterLocation
from schemathesis.core.result import Ok, Result
from schemathesis.generation import GenerationMode
from schemathesis.generation.case import Case
from schemathesis.generation.meta import (
    CaseMetadata,
    ComponentInfo,
    ExamplesPhaseData,
    FuzzingPhaseData,
    GenerationInfo,
    PhaseInfo,
    TestPhase,
)
from schemathesis.hooks import HookContext, HookDispatcher, apply_to_all_dispatchers
from schemathesis.schemas import (
    APIOperation,
    APIOperationMap,
    ApiStatistic,
    BaseSchema,
    OperationDefinition,
)

from .scalars import CUSTOM_SCALARS, get_extra_scalar_strategies

if TYPE_CHECKING:
    import graphql
    from hypothesis.strategies import SearchStrategy

    from schemathesis.auths import AuthContext, AuthStorage
    from schemathesis.resources import ExtraDataSource


@unique
class RootType(enum.Enum):
    QUERY = enum.auto()
    MUTATION = enum.auto()


@dataclass(repr=False)
class GraphQLOperationDefinition(OperationDefinition):
    field_name: str
    type_: graphql.GraphQLType
    root_type: RootType

    __slots__ = ("raw", "field_name", "type_", "root_type")

    def _repr_pretty_(self, *args: Any, **kwargs: Any) -> None: ...

    @property
    def is_query(self) -> bool:
        return self.root_type == RootType.QUERY

    @property
    def is_mutation(self) -> bool:
        return self.root_type == RootType.MUTATION


class GraphQLResponses:
    def find_by_status_code(self, status_code: int) -> None:
        return None  # pragma: no cover

    def add(self, status_code: str, definition: dict[str, Any]) -> None:
        return None  # pragma: no cover


@dataclass
class GraphQLSchema(BaseSchema):
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
        schema = self.client_schema
        for root_type, operation_type in (
            (RootType.QUERY, schema.query_type),
            (RootType.MUTATION, schema.mutation_type),
        ):
            if operation_type and operation_type.name == key:
                map = APIOperationMap(self, {})
                map._data = FieldMap(map, root_type, operation_type)
                return map
        raise KeyError(key)

    def find_operation_by_label(self, label: str) -> APIOperation | None:
        if label.startswith(("Query.", "Mutation.")):
            ty, field = label.split(".", maxsplit=1)
            try:
                return self[ty][field]
            except KeyError:
                return None
        return None

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
    def specification(self) -> Specification:
        return Specification.graphql(version="")

    def apply_auth(self, case: Case, context: AuthContext) -> bool:
        return False

    def create_extra_data_source(self) -> None:
        """Extra data sources are not supported for GraphQL schemas."""
        return None

    @property
    def client_schema(self) -> graphql.GraphQLSchema:
        import graphql

        if not hasattr(self, "_client_schema"):
            self._client_schema = graphql.build_client_schema(self.raw_schema)
        return self._client_schema

    @property
    def base_path(self) -> str:
        if self.config.base_url:
            return urlsplit(self.config.base_url).path
        return self._get_base_path()

    def _get_base_path(self) -> str:
        return cast(str, urlsplit(self.location).path)

    def _measure_statistic(self) -> ApiStatistic:
        statistic = ApiStatistic()
        raw_schema = self.raw_schema["__schema"]
        dummy_operation = APIOperation(
            base_url=self.get_base_url(),
            path=self.base_path,
            label="",
            method="POST",
            schema=self,
            responses=GraphQLResponses(),
            security=None,
            definition=None,  # type: ignore[arg-type, var-annotated]
        )

        for type_name in ("queryType", "mutationType"):
            type_def = raw_schema.get(type_name)
            if type_def is not None:
                query_type_name = type_def["name"]
                for type_def in raw_schema.get("types", []):
                    if type_def["name"] == query_type_name:
                        for field in type_def["fields"]:
                            statistic.operations.total += 1
                            dummy_operation.label = f"{query_type_name}.{field['name']}"
                            if not self._should_skip(dummy_operation):
                                statistic.operations.selected += 1
        return statistic

    def get_all_operations(self) -> Generator[Result[APIOperation, InvalidSchema], None, None]:
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
            label=f"{operation_type.name}.{field_name}",
            method="POST",
            app=self.app,
            schema=self,
            responses=GraphQLResponses(),
            security=None,
            # Parameters are not yet supported
            definition=GraphQLOperationDefinition(
                raw=field,
                type_=operation_type,
                field_name=field_name,
                root_type=root_type,
            ),
        )

    def get_case_strategy(
        self,
        operation: APIOperation,
        hooks: HookDispatcher | None = None,
        auth_storage: AuthStorage | None = None,
        generation_mode: GenerationMode = GenerationMode.POSITIVE,
        **kwargs: Any,
    ) -> SearchStrategy:
        return graphql_cases(
            operation=operation,
            hooks=hooks,
            auth_storage=auth_storage,
            generation_mode=generation_mode,
            **kwargs,
        )

    def get_strategies_from_examples(self, operation: APIOperation, **kwargs: Any) -> list[SearchStrategy[Case]]:
        return []

    def make_case(
        self,
        *,
        operation: APIOperation,
        method: str | None = None,
        path: str | None = None,
        path_parameters: dict[str, Any] | None = None,
        headers: dict[str, Any] | CaseInsensitiveDict | None = None,
        cookies: dict[str, Any] | None = None,
        query: dict[str, Any] | None = None,
        body: list | dict[str, Any] | str | int | float | bool | bytes | NotSet = NOT_SET,
        media_type: str | None = None,
        multipart_content_types: dict[str, str] | None = None,
        meta: CaseMetadata | None = None,
    ) -> Case:
        return Case(
            operation=operation,
            method=method or operation.method.upper(),
            path=path or operation.path,
            path_parameters=path_parameters or {},
            headers=CaseInsensitiveDict() if headers is None else CaseInsensitiveDict(headers),
            cookies=cookies or {},
            query=query or {},
            body=body,
            media_type=media_type or "application/json",
            multipart_content_types=multipart_content_types,
            meta=meta,
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
        operation_type = self._operation_type
        field_ = operation_type.fields[field_name]
        return schema._build_operation(self._root_type, operation_type, field_name, field_)

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


@st.composite  # type: ignore[untyped-decorator]
def graphql_cases(
    draw: Callable,
    *,
    operation: APIOperation,
    hooks: HookDispatcher | None = None,
    auth_storage: auths.AuthStorage | None = None,
    generation_mode: GenerationMode = GenerationMode.POSITIVE,
    path_parameters: NotSet | dict[str, Any] = NOT_SET,
    headers: NotSet | dict[str, Any] = NOT_SET,
    cookies: NotSet | dict[str, Any] = NOT_SET,
    query: NotSet | dict[str, Any] = NOT_SET,
    body: Any = NOT_SET,
    media_type: str | None = None,
    phase: TestPhase = TestPhase.FUZZING,
    # Not supported for GraphQL, passed here to unify interfaces
    extra_data_source: ExtraDataSource | None = None,
) -> Any:
    import graphql
    from hypothesis_graphql import strategies as gql_st

    start = time.monotonic()
    definition = cast(GraphQLOperationDefinition, operation.definition)
    strategy_factory = {
        RootType.QUERY: gql_st.queries,
        RootType.MUTATION: gql_st.mutations,
    }[definition.root_type]
    hook_context = HookContext(operation=operation)
    custom_scalars = {**get_extra_scalar_strategies(), **CUSTOM_SCALARS}
    generation = operation.schema.config.generation_for(operation=operation, phase="fuzzing")
    strategy = strategy_factory(
        operation.schema.client_schema,  # type: ignore[attr-defined]
        fields=[definition.field_name],
        custom_scalars=custom_scalars,
        print_ast=_noop,
        allow_x00=generation.allow_x00,
        allow_null=generation.graphql_allow_null,
        codec=generation.codec,
    )
    strategy = apply_to_all_dispatchers(operation, hook_context, hooks, strategy, "body").map(graphql.print_ast)
    body = draw(strategy)

    path_parameters_ = _generate_parameter(
        ParameterLocation.PATH, path_parameters, draw, operation, hook_context, hooks
    )
    headers_ = _generate_parameter(ParameterLocation.HEADER, headers, draw, operation, hook_context, hooks)
    cookies_ = _generate_parameter(ParameterLocation.COOKIE, cookies, draw, operation, hook_context, hooks)
    query_ = _generate_parameter(ParameterLocation.QUERY, query, draw, operation, hook_context, hooks)

    _phase_data = {
        TestPhase.EXAMPLES: ExamplesPhaseData(
            description="Positive test case",
            parameter=None,
            parameter_location=None,
            location=None,
        ),
        TestPhase.FUZZING: FuzzingPhaseData(
            description="Positive test case",
            parameter=None,
            parameter_location=None,
            location=None,
        ),
    }[phase]
    phase_data = cast(ExamplesPhaseData | FuzzingPhaseData, _phase_data)
    instance = operation.Case(
        path_parameters=path_parameters_,
        headers=headers_,
        cookies=cookies_,
        query=query_,
        body=body,
        _meta=CaseMetadata(
            generation=GenerationInfo(
                time=time.monotonic() - start,
                mode=generation_mode,
            ),
            phase=PhaseInfo(name=phase, data=phase_data),
            components={
                kind: ComponentInfo(mode=generation_mode)
                for kind, value in [
                    (ParameterLocation.QUERY, query_),
                    (ParameterLocation.PATH, path_parameters_),
                    (ParameterLocation.HEADER, headers_),
                    (ParameterLocation.COOKIE, cookies_),
                    (ParameterLocation.BODY, body),
                ]
                if value is not NOT_SET
            },
        ),
        media_type=media_type or "application/json",
    )
    context = auths.AuthContext(
        operation=operation,
        app=operation.app,
    )
    auths.set_on_case(instance, context, auth_storage)
    return instance


def _generate_parameter(
    location: ParameterLocation,
    explicit: NotSet | dict[str, Any],
    draw: Callable,
    operation: APIOperation,
    context: HookContext,
    hooks: HookDispatcher | None,
) -> Any:
    # Schemathesis does not generate anything but `body` for GraphQL, hence use `None`
    container = location.container_name
    if isinstance(explicit, NotSet):
        strategy = apply_to_all_dispatchers(operation, context, hooks, st.none(), container)
    else:
        strategy = apply_to_all_dispatchers(operation, context, hooks, st.just(explicit), container)
    return draw(strategy)


def _noop(node: graphql.Node) -> graphql.Node:
    return node
