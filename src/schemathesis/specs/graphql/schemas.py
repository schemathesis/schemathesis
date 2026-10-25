from __future__ import annotations

import json
from collections.abc import Callable, Generator, Iterator, Mapping
from dataclasses import dataclass
from difflib import get_close_matches
from functools import cached_property
from types import SimpleNamespace
from typing import (
    TYPE_CHECKING,
    Any,
    NoReturn,
    cast,
)
from unittest import SkipTest
from urllib.parse import urlsplit, urlunsplit

import graphql
from hypothesis import strategies as st
from hypothesis.errors import InvalidArgument
from typing_extensions import override

from schemathesis import auths
from schemathesis.core import NOT_SET, Body, NotSet, Specification
from schemathesis.core.errors import InvalidSchema, OperationNotFound
from schemathesis.core.failures import AcceptedNegativeData, Failure, MalformedJson
from schemathesis.core.parameters import ParameterLocation
from schemathesis.core.result import Ok, Result
from schemathesis.core.statistic import ApiStatistic, StatefulInference
from schemathesis.core.timing import Instant
from schemathesis.core.transforms import split_body_path
from schemathesis.core.transport import CallOutcome, is_uninformative_status
from schemathesis.filters import FilterUsage
from schemathesis.generation import GenerationMode
from schemathesis.generation.case import Case
from schemathesis.generation.coverage import GenerationSession
from schemathesis.generation.feedback import NO_FEEDBACK, FeedbackSources
from schemathesis.generation.meta import (
    CaseMetadata,
    ComponentInfo,
    GenerationInfo,
    PhaseInfo,
    TestPhase,
    build_phase_data,
)
from schemathesis.graphql.checks import (
    GraphQLClientError,
    GraphQLSchemaViolation,
    GraphQLServerError,
    UnexpectedGraphQLResponse,
)
from schemathesis.hooks import HookContext, HookDispatcher, apply_to_all_dispatchers
from schemathesis.schemas import (
    APIOperation,
    APIOperationMap,
    BaseSchema,
    OperationDefinition,
)
from schemathesis.transport.prepare import prepare_path

from ._helpers import replace_node
from .dictionaries import resolve_bindings, substitute_dictionaries
from .extra_data_source import GraphQLResourcePool
from .inference import RootType
from .ordering import compute_graphql_layers
from .scalars import CUSTOM_SCALARS, UnknownScalar, get_extra_scalar_strategies, unsupported_scalar_name
from .substitution import SUBSTITUTION_PROBABILITY, substitute_constants, substitute_pool_values
from .validation import is_client_error, parse_payload, validate_graphql_response

if TYPE_CHECKING:
    from random import Random

    from hypothesis.strategies import SearchStrategy

    from schemathesis.auths import AuthContext, AuthStorage
    from schemathesis.config import GenerationConfig
    from schemathesis.core.error_feedback import ErrorFeedbackStore
    from schemathesis.core.spec import ApiSchema
    from schemathesis.core.transport import Response
    from schemathesis.engine.link_calibration import LinkCalibrationState
    from schemathesis.engine.observations import Observations
    from schemathesis.generation.dictionaries import DictionaryDraw
    from schemathesis.generation.stateful.state_machine import APIStateMachine
    from schemathesis.python._constants.pool import ConstantDraw
    from schemathesis.resources import ResourcePool


# Reused on every per-draw call; allocating once avoids ~600ns of `LazyStrategy` construction.
_NONE_STRATEGY: st.SearchStrategy = st.none()

# Query fields every Apollo Federation subgraph must expose. They belong to the federation gateway
# protocol rather than to the API under test, so they stay out of the tested surface unless asked for.
FEDERATION_FIELDS = frozenset({"_entities", "_service"})


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

    @property
    def status_codes(self) -> tuple[str, ...]:
        return ()


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
    def build_state_machine(
        self,
        *,
        feedback: FeedbackSources,
        link_calibration: LinkCalibrationState | None,
    ) -> type[APIStateMachine]:
        # GraphQL stateful transitions consume neither feedback nor link calibration.
        return self.as_state_machine()

    @override
    def apply_stateful_inference(self, observations: Observations | None) -> StatefulInference:
        # All GraphQL transitions are derived from schema structure (no `links` keyword equivalent),
        # so inference accounts for the entire population.
        count = self.analysis.transition_count
        return StatefulInference(inferred=count, total=count, selected=count)

    @override
    def create_extra_data_source(self) -> GraphQLResourcePool:
        return GraphQLResourcePool(client_schema=self.client_schema, handles=self.analysis.handles)

    @override
    def build_request_url(self, case: Case, base_url: str) -> str:
        # Schemas loaded from files have no operation path, so the endpoint is the base URL itself.
        if not case.path:
            return base_url
        parts = list(urlsplit(base_url))
        parts[2] = prepare_path(case.path, case.path_parameters)
        return urlunsplit(parts)

    @override
    def build_url_template(self, case: Case, base_url: str) -> str:
        return self.build_request_url(case, base_url)

    @override
    def prepare_request_body(self, case: Case) -> Body:
        body = case.body
        if isinstance(body, (NotSet, bytes)):
            return body
        return {"query": body}

    @override
    def iter_coverage_cases(
        self,
        operation: APIOperation,
        *,
        generation_modes: list[GenerationMode],
        generation_config: GenerationConfig,
        extra_data_source: ResourcePool | None = None,
        error_feedback: ErrorFeedbackStore | None = None,
        session: GenerationSession | None = None,
    ) -> Iterator[Case]:
        # GraphQL has no coverage phase yet; the schema-level case enumerator is empty.
        return iter(())

    @override
    def get_operation_layers(self, operations: list[APIOperation]) -> list[list[APIOperation]]:

        layers = compute_graphql_layers(operations)
        # Single role: layering would add no information, so keep the schema order.
        return layers if len(layers) > 1 else []

    @cached_property
    def client_schema(self) -> graphql.GraphQLSchema:

        return graphql.build_client_schema(cast("graphql.IntrospectionQuery", self.raw_schema))

    @property
    @override
    def base_path(self) -> str:
        if self.config.base_url:
            return urlsplit(self.config.base_url).path
        return self.declared_base_path()

    @override
    def declared_base_path(self) -> str:
        parts = urlsplit(self.location or "")
        # A `file://` location points at the schema file, not at the GraphQL endpoint.
        if parts.scheme not in ("", "http", "https"):
            return ""
        return parts.path

    @override
    def _measure_statistic(self) -> ApiStatistic:
        statistic = ApiStatistic()
        raw_schema = self.raw_schema["__schema"]
        dummy_operation: APIOperation = APIOperation(
            base_url=self.get_base_url(),
            path=self.base_path,
            label="",
            method="post",
            schema=self,
            responses=GraphQLResponses(),
            security=None,
            definition=OperationDefinition(raw={}),
        )

        usage = FilterUsage(self.filter_set) if not self.filter_set.is_empty() else None
        for type_name, root_type in (("queryType", RootType.QUERY), ("mutationType", RootType.MUTATION)):
            type_def = raw_schema.get(type_name)
            if type_def is not None:
                query_type_name = type_def["name"]
                for type_def in raw_schema.get("types", []):
                    if type_def["name"] == query_type_name:
                        for field in type_def["fields"]:
                            dummy_operation.label = f"{query_type_name}.{field['name']}"
                            if self._is_hidden_federation_field(root_type, field["name"], dummy_operation):
                                continue
                            statistic.operations.total += 1
                            if not self._should_skip(dummy_operation):
                                statistic.operations.selected += 1
                            if usage is not None:
                                usage.record(SimpleNamespace(operation=dummy_operation))
        if usage is not None:
            statistic.unmatched_filters = usage.results()
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
                if self._is_hidden_federation_field(root_type, field_name, operation) or self._should_skip(operation):
                    continue
                yield Ok(operation)

    def _is_hidden_federation_field(self, root_type: RootType, field_name: str, operation: APIOperation) -> bool:
        return (
            root_type == RootType.QUERY
            and field_name in FEDERATION_FIELDS
            and not self.filter_set.is_explicitly_included(operation)
        )

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
        field: graphql.GraphQLField,
    ) -> APIOperation:
        return APIOperation(
            base_url=self.get_base_url(),
            path=self.base_path,
            label=f"{operation_type.name}.{field_name}",
            method="post",
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
        feedback: FeedbackSources = NO_FEEDBACK,
        *,
        path_parameters: dict[str, Any] | None = None,
        headers: dict[str, Any] | None = None,
        cookies: dict[str, Any] | None = None,
        query: dict[str, Any] | None = None,
        body: Any = NOT_SET,
        media_type: str | None = None,
        phase: TestPhase = TestPhase.FUZZING,
    ) -> SearchStrategy[Case]:
        return graphql_cases(
            operation=operation,
            hooks=hooks,
            auth_storage=auth_storage,
            generation_mode=generation_mode,
            feedback=feedback,
            path_parameters=path_parameters,
            headers=headers,
            cookies=cookies,
            query=query,
            body=body,
            media_type=media_type,
            phase=phase,
        )

    @override
    def get_strategies_from_examples(
        self, operation: APIOperation, feedback: FeedbackSources = NO_FEEDBACK, **kwargs: Any
    ) -> list[SearchStrategy[Case]]:
        return []

    @override
    def _case_media_type(self, operation: APIOperation, body: Body, media_type: str | None) -> str | None:
        return media_type or "application/json"

    @override
    def get_tags(self, operation: APIOperation) -> list[str] | None:
        return None

    @override
    def get_operation_id(self, operation: APIOperation) -> str | None:
        return None

    @override
    def is_deprecated(self, operation: APIOperation) -> bool:
        return operation.definition.raw.deprecation_reason is not None

    @override
    def validate(self) -> None:
        return None

    @override
    def classify_call_outcome(self, case: Case, response: Response) -> CallOutcome:
        """Decide what the API did with a positive case, from the body rather than the status code.

        A GraphQL server answers the same status whether it served the query or refused it, and an
        `errors` entry means the operation was not carried out in full.
        """
        if is_uninformative_status(response.status_code):
            return CallOutcome.UNINFORMATIVE
        try:
            payload = parse_payload(response)
        except ValueError:
            # Not a GraphQL response at all; it says nothing about the data that was sent.
            return CallOutcome.UNINFORMATIVE
        if not isinstance(payload, dict):
            return CallOutcome.UNINFORMATIVE
        if payload.get("errors"):
            return CallOutcome.REJECTED
        # `data` is absent when the request was refused before execution, and `null` when execution
        # began but nothing survived it.
        return CallOutcome.ACCEPTED if payload.get("data") is not None else CallOutcome.REJECTED

    @override
    def evaluate_server_error(self, case: Case, response: Response) -> None:

        failure = _response_failure(case, response)
        if isinstance(failure, GraphQLServerError):
            raise failure

    @override
    def evaluate_negative_data(self, case: Case, response: Response) -> None:
        failure = _response_failure(case, response)
        if isinstance(failure, AcceptedNegativeData):
            raise failure

    @override
    def evaluate_positive_data(self, case: Case, response: Response) -> None:

        failure = _response_failure(case, response)
        if isinstance(failure, GraphQLClientError):
            raise failure

    @override
    def validate_response(
        self,
        operation: APIOperation,
        response: Response,
        *,
        case: Case | None = None,
    ) -> bool | None:

        failure = _response_failure(case if case is not None else operation.Case(), response)
        if isinstance(failure, (MalformedJson, UnexpectedGraphQLResponse, GraphQLSchemaViolation)):
            raise failure
        return None


def _response_failure(case: Case, response: Response) -> Failure | None:
    """The failure a GraphQL response carries; each check raises the kinds it owns."""
    # `not_a_server_error` reports a 5xx by its status, so its body is not judged here.
    if response.status_code >= 500:
        return None
    is_negative_mode = case.meta is not None and case.meta.generation.mode.is_negative
    try:
        data = parse_payload(response)
    except json.JSONDecodeError as exc:
        return MalformedJson.from_exception(operation=case.operation.label, exc=exc)
    if is_negative_mode and isinstance(data, dict):
        if not data.get("errors"):
            description = ""
            if case.meta and case.meta.phase.data.description:
                description = f"\n{case.meta.phase.data.description}"
            return AcceptedNegativeData(
                operation=case.operation.label,
                message=f"Invalid data should have been rejected by GraphQL schema validation{description}",
                status_code=response.status_code,
                expected_statuses=[],
            )
        if is_client_error(data):
            return None
    try:
        validate_graphql_response(case, data)
    except GraphQLClientError as exc:
        return None if is_negative_mode else exc
    except Failure as exc:
        return exc
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


@st.composite
def graphql_cases(
    draw: Callable,
    *,
    operation: APIOperation,
    hooks: HookDispatcher | None = None,
    auth_storage: auths.AuthStorage | None = None,
    generation_mode: GenerationMode = GenerationMode.POSITIVE,
    path_parameters: NotSet | dict[str, Any] | None = NOT_SET,
    headers: NotSet | dict[str, Any] | None = NOT_SET,
    cookies: NotSet | dict[str, Any] | None = NOT_SET,
    query: NotSet | dict[str, Any] | None = NOT_SET,
    body: Any = NOT_SET,
    media_type: str | None = None,
    phase: TestPhase = TestPhase.FUZZING,
    # GraphQL draws from the resource pool and extracted constants; it has no use for error feedback.
    feedback: FeedbackSources = NO_FEEDBACK,
    mutate_ast: Callable[[graphql.OperationDefinitionNode, Random], graphql.OperationDefinitionNode] | None = None,
) -> Any:
    from hypothesis_graphql import Mode as GqlMode
    from hypothesis_graphql import strategies as gql_st

    started_at = Instant()
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
        ast_node = _draw_ast(draw, strategy)
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
        ast_node = _draw_ast(draw, fallback_strategy)

    operation_node = next(
        (d for d in ast_node.definitions if isinstance(d, graphql.OperationDefinitionNode)),
        None,
    )
    constants_draws: tuple[ConstantDraw, ...] = ()
    dictionary_draws: tuple[DictionaryDraw, ...] = ()
    if operation_node is not None:
        original_operation_node = operation_node
        # A captured identifier would overwrite the argument the negative strategy deliberately
        # corrupted, turning the query valid and making the server's acceptance look like a bug.
        if isinstance(feedback.extra_data_source, GraphQLResourcePool) and effective_mode.is_positive:
            random_source = draw(st.randoms())
            if random_source.random() < SUBSTITUTION_PROBABILITY:
                operation_node = substitute_pool_values(
                    operation_node=operation_node,
                    client_schema=operation.schema.client_schema,
                    pool=feedback.extra_data_source,
                    random=random_source,
                    schema_index=operation.schema.analysis.schema_index,
                )
        # A dictionary entry is explicit user intent, so it overwrites whatever the resource pool
        # put in the same argument - and a harvested constant does not overwrite it in turn.
        if effective_mode.is_positive:
            dictionary_bindings = resolve_bindings(operation, generation)
            if not dictionary_bindings.is_empty:
                operation_node, applied_dictionary_draws = substitute_dictionaries(
                    operation_node=operation_node,
                    client_schema=operation.schema.client_schema,
                    bindings=dictionary_bindings,
                    operation_label=operation.label,
                    random=draw(st.randoms()),
                )
                dictionary_draws = tuple(applied_dictionary_draws)
        if feedback.constants_value_source is not None and effective_mode.is_positive:
            operation_node, applied_constants_draws = substitute_constants(
                operation_node=operation_node,
                client_schema=operation.schema.client_schema,
                pool=feedback.constants_value_source,
                random=draw(st.randoms()),
                skip=frozenset(
                    tuple(split_body_path(draw_.body_path)) for draw_ in dictionary_draws if draw_.body_path is not None
                ),
            )
            constants_draws = tuple(applied_constants_draws)
        if mutate_ast is not None:
            # Hypothesis-driven randoms favor tiny values and would skip captured ids far above the exploration rate.
            operation_node = mutate_ast(operation_node, draw(st.randoms(use_true_random=True)))
        if operation_node is not original_operation_node:
            ast_node = replace_node(
                ast_node,
                definitions=tuple(
                    operation_node if definition is original_operation_node else definition
                    for definition in ast_node.definitions
                ),
            )
    body = graphql.print_ast(ast_node)

    path_parameters_ = _generate_parameter(
        ParameterLocation.PATH, path_parameters, draw, operation, hook_context, hooks
    )
    headers_ = _generate_parameter(ParameterLocation.HEADER, headers, draw, operation, hook_context, hooks)
    cookies_ = _generate_parameter(ParameterLocation.COOKIE, cookies, draw, operation, hook_context, hooks)
    query_ = _generate_parameter(ParameterLocation.QUERY, query, draw, operation, hook_context, hooks)

    description = "Positive test case" if effective_mode == GenerationMode.POSITIVE else "Negative test case"
    instance = operation.Case(
        path_parameters=path_parameters_,
        headers=headers_,
        cookies=cookies_,
        query=query_,
        body=body,
        _meta=CaseMetadata(
            generation=GenerationInfo(
                time=started_at.elapsed,
                mode=effective_mode,
            ),
            phase=PhaseInfo(name=phase, data=build_phase_data(phase, description)),
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
            constants_draws=constants_draws,
            dictionary_draws=dictionary_draws,
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
    explicit: NotSet | dict[str, Any] | None,
    draw: Callable,
    operation: APIOperation,
    context: HookContext,
    hooks: HookDispatcher | None,
) -> Any:
    # Schemathesis does not generate anything but `body` for GraphQL, hence use `None`
    container = location.container_name
    if explicit is None or isinstance(explicit, NotSet):
        strategy = apply_to_all_dispatchers(operation, context, hooks, _NONE_STRATEGY, container)
    else:
        strategy = apply_to_all_dispatchers(operation, context, hooks, st.just(explicit), container)
    return draw(strategy)


def _draw_ast(draw: Callable, strategy: SearchStrategy[graphql.DocumentNode]) -> graphql.DocumentNode:
    """Draw a query AST, reporting a scalar with no registered strategy as such."""
    try:
        return draw(strategy)
    except InvalidArgument as error:
        scalar_name = unsupported_scalar_name(error)
        if scalar_name is None:
            raise
        raise UnknownScalar(scalar_name) from None


def _noop(node: graphql.DocumentNode) -> graphql.DocumentNode:
    return node


if TYPE_CHECKING:
    # Verify structural conformance to the spec-agnostic protocol; mypy fails here
    # if a method is renamed or its signature drifts from `ApiSchema`.
    def _verify_api_schema_protocol(schema: GraphQLSchema) -> ApiSchema:
        return schema
