from __future__ import annotations

import time
from typing import Any, Callable, cast

import graphql
from hypothesis import strategies as st, settings
from hypothesis_graphql import strategies as gql_st

from schemathesis import auths
from schemathesis.auths import AuthStorage
from schemathesis.core import NOT_SET
from schemathesis.generation import GenerationConfig
from schemathesis.generation.hypothesis import generator
from schemathesis.generation.meta import (
    CaseMetadata,
    ComponentInfo,
    ComponentKind,
    GenerationInfo,
    PhaseInfo,
)
from schemathesis.generation.modes import GenerationMode
from schemathesis.graphql.specification import GraphQl, RootType
from schemathesis.hooks import HookContext, HookDispatcher, apply_to_all_dispatchers
from schemathesis.specification.interface import ApiOperation
from schemathesis.specs.graphql.scalars import CUSTOM_SCALARS, get_extra_scalar_strategies
from schemathesis.specs.openapi.constants import LOCATION_TO_CONTAINER


@generator("graphql")
@st.composite  # type: ignore
def cases(
    draw: st.DrawFn,
    operation: ApiOperation[GraphQl],
    hooks: HookDispatcher | None = None,
    auth_storage: AuthStorage | None = None,
    generation_mode: GenerationMode = GenerationMode.default(),
    generation_config: GenerationConfig | None = None,
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
        operation.data.schema,
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

    instance = operation.Case(
        path_parameters=path_parameters_,
        headers=headers_,
        cookies=cookies_,
        query=query_,
        body=body,
        meta=CaseMetadata(
            generation=GenerationInfo(
                time=time.monotonic() - start,
                mode=generation_mode,
            ),
            phase=PhaseInfo.generate(),
            components={
                kind: ComponentInfo(mode=generation_mode)
                for kind, value in [
                    (ComponentKind.QUERY, query_),
                    (ComponentKind.PATH_PARAMETERS, path_parameters_),
                    (ComponentKind.HEADERS, headers_),
                    (ComponentKind.COOKIES, cookies_),
                    (ComponentKind.BODY, body),
                ]
                if value is not NOT_SET
            },
        ),
        media_type="application/json",
    )  # type: ignore
    context = auths.AuthContext(
        operation=operation,
        app=operation.app,
    )
    auths.set_on_case(instance, context, auth_storage)
    return instance


def _generate_parameter(
    location: str, draw: Callable, operation: ApiOperation, context: HookContext, hooks: HookDispatcher | None
) -> Any:
    # Schemathesis does not generate anything but `body` for GraphQL, hence use `None`
    container = LOCATION_TO_CONTAINER[location]
    strategy = apply_to_all_dispatchers(operation, context, hooks, st.none(), container)
    return draw(strategy)


def _noop(node: graphql.Node) -> graphql.Node:
    return node
