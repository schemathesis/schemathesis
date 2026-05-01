"""In-place substitution of captured pool values into argument literals.

Match priority for a scalar argument: (1) bespoke `<Type>ID` scalar names
the parent type directly; (2) for generic `ID!` arguments, the argument
name's `<entity>Id`/`<entity>_id` token names the parent; (3) a bare `id`
argument falls back to the enclosing field's return type.
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
        unwrapped_return = _unwrap(field_def.type)
        enclosing_field_type = (
            unwrapped_return.name if isinstance(unwrapped_return, graphql.GraphQLObjectType) else None
        )
        for argument in selection.arguments:
            arg_def = field_def.args.get(argument.name.value)
            if arg_def is None:
                continue
            _maybe_substitute(argument, arg_def.type, enclosing_field_type, pool, random)
        if isinstance(unwrapped_return, graphql.GraphQLObjectType):
            _walk(selection.selection_set, unwrapped_return, pool, random)


def _strip_id_suffix(name: str) -> str | None:
    # `userId`/`userID`/`user_id` -> `user`; bare `id`/`Id`/`ID` or non-id names -> None.
    if name.endswith("_id") and len(name) > len("_id"):
        return name[: -len("_id")]
    for suffix in ("ID", "Id"):
        if name.endswith(suffix) and len(name) > len(suffix):
            return name[: -len(suffix)]
    return None


def _candidate_parent_type(
    *,
    scalar_name: str,
    argument_name: str,
    enclosing_field_type: str | None,
) -> str | None:
    # Bespoke `<Type>ID` scalar names the parent type directly.
    if scalar_name != "ID":
        return _strip_id_suffix(scalar_name)
    # Generic `ID!`: the argument-name token (`userId` -> `User`) names the parent.
    stripped = _strip_id_suffix(argument_name)
    if stripped is not None:
        return stripped[:1].upper() + stripped[1:]
    # Bare `id` falls back to the enclosing field's return type.
    if argument_name == "id":
        return enclosing_field_type
    return None


def _maybe_substitute(
    argument: graphql.ArgumentNode,
    arg_type: graphql.GraphQLType,
    enclosing_field_type: str | None,
    pool: GraphQLResourcePool,
    random: Random,
) -> None:
    unwrapped = _unwrap(arg_type)
    if not isinstance(unwrapped, graphql.GraphQLScalarType):
        return
    parent_type_name = _candidate_parent_type(
        scalar_name=unwrapped.name,
        argument_name=argument.name.value,
        enclosing_field_type=enclosing_field_type,
    )
    if parent_type_name is None:
        return
    candidate = pool.draw(parent_type_name=parent_type_name, random=random)
    if candidate is None:
        return
    argument.value = graphql.StringValueNode(value=candidate)
