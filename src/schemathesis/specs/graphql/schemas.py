from __future__ import annotations

import time
from collections.abc import Callable, Generator, Iterator, Mapping
from dataclasses import dataclass
from difflib import get_close_matches
from types import SimpleNamespace
from typing import (
    TYPE_CHECKING,
    Any,
    NoReturn,
    cast,
)
from unittest import SkipTest
from urllib.parse import urlsplit, urlunsplit

from hypothesis import strategies as st
from requests.structures import CaseInsensitiveDict
from typing_extensions import override

from schemathesis import auths
from schemathesis.core import NOT_SET, Body, NotSet, Specification
from schemathesis.core.errors import InvalidSchema, OperationNotFound
from schemathesis.core.parameters import ParameterLocation
from schemathesis.core.result import Ok, Result
from schemathesis.core.statistic import ApiStatistic
from schemathesis.generation import GenerationMode
from schemathesis.generation.case import Case
from schemathesis.generation.meta import (
    CaseMetadata,
    ComponentInfo,
    ExamplesPhaseData,
    FuzzingPhaseData,
    GenerationInfo,
    PhaseInfo,
    StatefulPhaseData,
    TestPhase,
)
from schemathesis.hooks import HookContext, HookDispatcher, apply_to_all_dispatchers
from schemathesis.schemas import (
    APIOperation,
    APIOperationMap,
    BaseSchema,
    OperationDefinition,
)
from schemathesis.transport.prepare import prepare_path

from .extra_data_source import GraphQLResourcePool
from .inference import RootType
from .scalars import CUSTOM_SCALARS, get_extra_scalar_strategies
from .substitution import SUBSTITUTION_PROBABILITY, substitute_pool_values

if TYPE_CHECKING:
    from random import Random

    import graphql
    from hypothesis.strategies import SearchStrategy

    from schemathesis.auths import AuthContext, AuthStorage
    from schemathesis.config import GenerationConfig
    from schemathesis.core.error_feedback import ErrorFeedbackStore
    from schemathesis.core.spec import ApiSchema
    from schemathesis.engine.context import EngineContext
    from schemathesis.engine.link_calibration import LinkCalibrationState
    from schemathesis.engine.run import Phase
    from schemathesis.engine.run.unit._layered_scheduler import LayeredScheduler
    from schemathesis.engine.run.unit._pool import DefaultScheduler
    from schemathesis.generation.stateful.state_machine import APIStateMachine
    from schemathesis.resources import ExtraDataSource


# Reused on every per-draw call; allocating once avoids ~600ns of `LazyStrategy` construction.
_NONE_STRATEGY: st.SearchStrategy = st.none()


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
    @override
    def __post_init__(self) -> None:
        super().__post_init__()
        from schemathesis.specs.graphql.analysis import GraphQLAnalysis

        self.analysis = GraphQLAnalysis(self)

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__}>"

    @override
    def __iter__(self) -> Iterator[str]:
        schema = self.client_schema
        for operation_type in (
            schema.query_type,
            schema.mutation_type,
        ):
            if operation_type is not None:
                yield operation_type.name

    @override
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

    @override
    def find_operation_by_label(self, label: str) -> APIOperation | None:
        if label.startswith(("Query.", "Mutation.")):
            ty, field = label.split(".", maxsplit=1)
            try:
                return self[ty][field]
            except KeyError:
                return None
        return None

    @override
    def on_missing_operation(self, item: str, exc: KeyError) -> NoReturn:
        raw_schema = self.raw_schema["__schema"]
        type_names = [type_def["name"] for type_def in raw_schema.get("types", [])]
        matches = get_close_matches(item, type_names)
        message = f"`{item}` type not found"
        if matches:
            message += f". Did you mean `{matches[0]}`?"
        raise OperationNotFound(message=message, item=item) from exc

    @override
    def get_full_path(self, path: str) -> str:
        return self.base_path

    @property
    @override
    def specification(self) -> Specification:
        return Specification.graphql(version="")

    @override
    def apply_auth(self, case: Case, context: AuthContext) -> bool:
        return False

    @override
    def as_state_machine(self) -> type[APIStateMachine]:
        from schemathesis.specs.graphql.stateful import create_state_machine

        return create_state_machine(self)

    @override
    def _build_state_machine(
        self,
        *,
        error_feedback: ErrorFeedbackStore | None,
        link_calibration: LinkCalibrationState | None,
    ) -> type[APIStateMachine]:
        # `error_feedback` and `link_calibration` are OpenAPI-specific; GraphQL strategies
        # and stateful transitions don't consume either signal.
        return self.as_state_machine()

    @override
    def apply_stateful_inference(self, ctx: EngineContext) -> int:
        # All GraphQL transitions are derived from schema structure (no `links` keyword equivalent),
        # so the entire selected count is reported through the engine's `inferred` channel.
        return self.analysis.transition_count

    @override
    def create_extra_data_source(self) -> GraphQLResourcePool:
        return GraphQLResourcePool(client_schema=self.client_schema)

    @override
    def build_request_url(self, case: Case, base_url: str) -> str:
        parts = list(urlsplit(base_url))
        parts[2] = prepare_path(case.path, case.path_parameters)
        return urlunsplit(parts)

    @override
    def prepare_request_body(self, body: Body) -> Body:
        if isinstance(body, NotSet | bytes):
            return body
        return {"query": body}

    @override
    def iter_coverage_cases(
        self,
        operation: APIOperation,
        *,
        generation_modes: list[GenerationMode],
        generation_config: GenerationConfig,
        extra_data_source: ExtraDataSource | None = None,
        error_feedback: ErrorFeedbackStore | None = None,
    ) -> Iterator[Case]:
        # GraphQL has no coverage phase yet; the schema-level case enumerator is empty.
        return iter(())

    @override
    def get_unit_scheduler(
        self,
        operations: list[Result[APIOperation, InvalidSchema]],
        phase: Phase,
    ) -> DefaultScheduler | LayeredScheduler:
        from schemathesis.engine.run.unit._layered_scheduler import LayeredScheduler
        from schemathesis.engine.run.unit._pool import DefaultScheduler, split_results
        from schemathesis.specs.graphql.ordering import compute_graphql_layers

        successes, errors = split_results(operations)
        if not successes:
            return DefaultScheduler(operations=operations)

        layers = compute_graphql_layers(successes)
        if len(layers) == 1:
            # Single role: layering would add no information.
            return DefaultScheduler(operations=operations)

        return LayeredScheduler(layers, errors=errors)

    @property
    def client_schema(self) -> graphql.GraphQLSchema:
        import graphql

        if not hasattr(self, "_client_schema"):
            self._client_schema = graphql.build_client_schema(self.raw_schema)
        return self._client_schema

    @property
    @override
    def base_path(self) -> str:
        if self.config.base_url:
            return urlsplit(self.config.base_url).path
        return self._get_base_path()

    @override
    def _get_base_path(self) -> str:
        return cast(str, urlsplit(self.location).path)

    @override
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

    @override
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

    @override
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

    @override
    def get_strategies_from_examples(self, operation: APIOperation, **kwargs: Any) -> list[SearchStrategy[Case]]:
        return []

    @override
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
        body: Body = NOT_SET,
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

    @override
    def get_tags(self, operation: APIOperation) -> list[str] | None:
        return None

    @override
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
    error_feedback: ErrorFeedbackStore | None = None,
    mutate_ast: Callable[[graphql.OperationDefinitionNode, Random], None] | None = None,
) -> Any:
    import graphql
    from hypothesis.errors import InvalidArgument
    from hypothesis_graphql import Mode as GqlMode
    from hypothesis_graphql import strategies as gql_st

    start = time.monotonic()
    definition = cast(GraphQLOperationDefinition, operation.definition)
    strategy_factory = {
        RootType.QUERY: gql_st.queries,
        RootType.MUTATION: gql_st.mutations,
    }[definition.root_type]
    hook_context = HookContext(operation=operation)
    custom_scalars = {**get_extra_scalar_strategies(), **CUSTOM_SCALARS}
    generation = operation.schema.config.generation_for(operation=operation, phase=phase.value)
    gql_mode = GqlMode.NEGATIVE if generation_mode == GenerationMode.NEGATIVE else GqlMode.POSITIVE
    effective_mode = generation_mode
    strategy = strategy_factory(
        operation.schema.client_schema,
        fields=[definition.field_name],
        custom_scalars=custom_scalars,
        print_ast=_noop,
        allow_x00=generation.allow_x00,
        allow_null=generation.graphql_allow_null,
        codec=generation.codec,
        mode=gql_mode,
    )
    strategy = apply_to_all_dispatchers(operation, hook_context, hooks, strategy, "body")
    try:
        ast_node = draw(strategy)
    except InvalidArgument:
        # Negative mode is not possible for this operation (no required arguments or scalar types to violate)
        if generation.modes == [GenerationMode.NEGATIVE]:
            raise SkipTest("Impossible to generate negative test cases for this GraphQL operation") from None
        # Fall back to positive mode when both modes are enabled
        effective_mode = GenerationMode.POSITIVE
        fallback_strategy = strategy_factory(
            operation.schema.client_schema,
            fields=[definition.field_name],
            custom_scalars=custom_scalars,
            print_ast=_noop,
            allow_x00=generation.allow_x00,
            allow_null=generation.graphql_allow_null,
            codec=generation.codec,
            mode=GqlMode.POSITIVE,
        )
        fallback_strategy = apply_to_all_dispatchers(operation, hook_context, hooks, fallback_strategy, "body")
        ast_node = draw(fallback_strategy)

    operation_node = next(
        (d for d in ast_node.definitions if isinstance(d, graphql.OperationDefinitionNode)),
        None,
    )
    if operation_node is not None:
        if isinstance(extra_data_source, GraphQLResourcePool):
            random_source = draw(st.randoms())
            if random_source.random() < SUBSTITUTION_PROBABILITY:
                substitute_pool_values(
                    operation_node=operation_node,
                    client_schema=operation.schema.client_schema,
                    pool=extra_data_source,
                    random=random_source,
                )
        if mutate_ast is not None:
            mutate_ast(operation_node, draw(st.randoms()))
    body = graphql.print_ast(ast_node)

    path_parameters_ = _generate_parameter(
        ParameterLocation.PATH, path_parameters, draw, operation, hook_context, hooks
    )
    headers_ = _generate_parameter(ParameterLocation.HEADER, headers, draw, operation, hook_context, hooks)
    cookies_ = _generate_parameter(ParameterLocation.COOKIE, cookies, draw, operation, hook_context, hooks)
    query_ = _generate_parameter(ParameterLocation.QUERY, query, draw, operation, hook_context, hooks)

    description = "Positive test case" if effective_mode == GenerationMode.POSITIVE else "Negative test case"
    _phase_data = {
        TestPhase.EXAMPLES: ExamplesPhaseData(
            description=description,
            parameter=None,
            parameter_location=None,
            location=None,
        ),
        TestPhase.FUZZING: FuzzingPhaseData(
            description=description,
            parameter=None,
            parameter_location=None,
            location=None,
        ),
        TestPhase.STATEFUL: StatefulPhaseData(
            description=description,
            parameter=None,
            parameter_location=None,
            location=None,
        ),
    }[phase]
    phase_data = cast(ExamplesPhaseData | FuzzingPhaseData | StatefulPhaseData, _phase_data)
    instance = operation.Case(
        path_parameters=path_parameters_,
        headers=headers_,
        cookies=cookies_,
        query=query_,
        body=body,
        _meta=CaseMetadata(
            generation=GenerationInfo(
                time=time.monotonic() - start,
                mode=effective_mode,
            ),
            phase=PhaseInfo(name=phase, data=phase_data),
            components={
                kind: ComponentInfo(mode=effective_mode)
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
        strategy = apply_to_all_dispatchers(operation, context, hooks, _NONE_STRATEGY, container)
    else:
        strategy = apply_to_all_dispatchers(operation, context, hooks, st.just(explicit), container)
    return draw(strategy)


def _noop(node: graphql.Node) -> graphql.Node:
    return node


if TYPE_CHECKING:
    # Verify structural conformance to the spec-agnostic protocol; mypy fails here
    # if a method is renamed or its signature drifts from `ApiSchema`.
    def _verify_api_schema_protocol(schema: GraphQLSchema) -> ApiSchema:
        return schema
