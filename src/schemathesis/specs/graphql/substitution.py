"""In-place substitution of captured pool values into argument literals.

A scalar argument matches when its type, after stripping `NonNull`/`List`
and a trailing `ID`/`Id` suffix, names a captured parent type.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

import graphql

from schemathesis.specs.graphql.extra_data_source import _root_type_for, _unwrap

if TYPE_CHECKING:
    from random import Random

    from schemathesis.specs.graphql.extra_data_source import GraphQLResourcePool

# Probability that captured values replace matching argument literals;
# the remaining 0.3 leaves Hypothesis room to explore fresh values.
SUBSTITUTION_PROBABILITY: Final = 0.7


def substitute_pool_values(
    *,
    operation_node: graphql.OperationDefinitionNode,
    client_schema: graphql.GraphQLSchema,
    pool: GraphQLResourcePool,
    random: Random,
) -> None:
    """Replace matching argument literals with captured pool values."""
    root = _root_type_for(client_schema, operation_node.operation)
    if root is None:
        return
    _walk(operation_node.selection_set, root, pool, random)


def _walk(
    selection_set: graphql.SelectionSetNode | None,
    parent_type: graphql.GraphQLObjectType,
    pool: GraphQLResourcePool,
    random: Random,
) -> None:
    if selection_set is None:
        return
    for selection in selection_set.selections:
        if not isinstance(selection, graphql.FieldNode):
            continue
        field_def = parent_type.fields.get(selection.name.value)
        if field_def is None:
            continue
        for argument in selection.arguments:
            arg_def = field_def.args.get(argument.name.value)
            if arg_def is None:
                continue
            _maybe_substitute(argument, arg_def.type, pool, random)
        unwrapped = _unwrap(field_def.type)
        if isinstance(unwrapped, graphql.GraphQLObjectType):
            _walk(selection.selection_set, unwrapped, pool, random)


def _candidate_parent_type(scalar_name: str) -> str | None:
    # BookID -> Book, UserId -> User. Bare ID and non-ID scalars: no parent.
    if scalar_name == "ID":
        return None
    for suffix in ("ID", "Id"):
        if scalar_name.endswith(suffix) and len(scalar_name) > len(suffix):
            return scalar_name[: -len(suffix)]
    return None


def _maybe_substitute(
    argument: graphql.ArgumentNode,
    arg_type: graphql.GraphQLType,
    pool: GraphQLResourcePool,
    random: Random,
) -> None:
    unwrapped = _unwrap(arg_type)
    if not isinstance(unwrapped, graphql.GraphQLScalarType):
        return
    parent_type_name = _candidate_parent_type(unwrapped.name)
    if parent_type_name is None:
        return
    candidate = pool.draw(parent_type_name=parent_type_name, random=random)
    if candidate is None:
        return
    argument.value = graphql.StringValueNode(value=candidate)
