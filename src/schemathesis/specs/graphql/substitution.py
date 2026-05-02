"""In-place substitution of captured pool values into argument literals.

Match priority for a scalar argument: (1) bespoke `<Type>ID` scalar names
the parent type directly; (2) for generic `ID!` arguments, the argument
name's `<entity>Id`/`<entity>Ids`/`<entity>_id`/`<entity>_ids` token names
the parent; (3) a bare `id`/`ids` argument falls back to the enclosing
field's return type. The same rules apply recursively to fields of
input-object arguments and to elements of list-typed arguments.
"""

from __future__ import annotations

from collections.abc import Callable
from random import Random
from typing import TYPE_CHECKING, Final

import graphql

from schemathesis.specs.graphql._helpers import _root_type_for, _unwrap

if TYPE_CHECKING:
    from collections.abc import Iterator

    from schemathesis.specs.graphql.extra_data_source import GraphQLResourcePool

# Probability that captured values replace matching argument literals;
# the remaining 0.3 leaves Hypothesis room to explore fresh values.
SUBSTITUTION_PROBABILITY: Final = 0.7

# Returns a substitution value for the given parent-type name, or None to keep the original.
ValueProvider = Callable[[str, Random], str | None]


def substitute_pool_values(
    *,
    operation_node: graphql.OperationDefinitionNode,
    client_schema: graphql.GraphQLSchema,
    pool: GraphQLResourcePool,
    random: Random,
) -> None:
    """Replace matching argument literals with captured pool values."""

    def provider(parent_type_name: str, random: Random) -> str | None:
        return pool.draw(parent_type_name=parent_type_name, random=random)

    _substitute(operation_node, client_schema, provider, random)


def substitute_bundle_values(
    *,
    operation_node: graphql.OperationDefinitionNode,
    client_schema: graphql.GraphQLSchema,
    bundle_values: dict[str, str],
    random: Random,
    exploration_rate: float = 0.0,
) -> None:
    """Replace id-typed argument literals with values from the given type to id mapping.

    `exploration_rate` is the per-argument probability of leaving the
    strategy-generated value in place, forcing discovery of bugs reachable only
    via unknown ids.
    """

    def provider(parent_type_name: str, random: Random) -> str | None:
        if exploration_rate > 0.0 and random.random() < exploration_rate:
            return None
        return bundle_values.get(parent_type_name)

    _substitute(operation_node, client_schema, provider, random)


def _substitute(
    operation_node: graphql.OperationDefinitionNode,
    client_schema: graphql.GraphQLSchema,
    provider: ValueProvider,
    random: Random,
) -> None:
    root = _root_type_for(client_schema, operation_node.operation)
    if root is None:
        return
    _walk(operation_node.selection_set, root, provider, random)


def _walk(
    selection_set: graphql.SelectionSetNode | None,
    parent_type: graphql.GraphQLObjectType,
    provider: ValueProvider,
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
            new_value = _substitute_value(
                argument.value, arg_def.type, argument.name.value, enclosing_field_type, provider, random
            )
            if new_value is not None:
                argument.value = new_value
        if isinstance(unwrapped_return, graphql.GraphQLObjectType):
            _walk(selection.selection_set, unwrapped_return, provider, random)


def _substitute_value(
    value: graphql.ValueNode,
    value_type: graphql.GraphQLType,
    name: str,
    enclosing_field_type: str | None,
    provider: ValueProvider,
    random: Random,
) -> graphql.ValueNode | None:
    """Return a substituted value if the provider returns a candidate; otherwise None."""
    inner = value_type
    while isinstance(inner, graphql.GraphQLNonNull):
        inner = inner.of_type
    if isinstance(inner, graphql.GraphQLList) and isinstance(value, graphql.ListValueNode):
        new_values = list(value.values)
        replaced_any = False
        for index, element in enumerate(new_values):
            replaced = _substitute_value(element, inner.of_type, name, enclosing_field_type, provider, random)
            if replaced is not None:
                new_values[index] = replaced
                replaced_any = True
        return graphql.ListValueNode(values=tuple(new_values)) if replaced_any else None
    if isinstance(inner, graphql.GraphQLScalarType):
        parent_type_name = _candidate_parent_type(
            scalar_name=inner.name,
            argument_name=name,
            enclosing_field_type=enclosing_field_type,
        )
        if parent_type_name is None:
            return None
        candidate = provider(parent_type_name, random)
        if candidate is None:
            return None
        return graphql.StringValueNode(value=candidate)
    if isinstance(inner, graphql.GraphQLInputObjectType) and isinstance(value, graphql.ObjectValueNode):
        new_fields = list(value.fields)
        replaced_any = False
        for index, field_node in enumerate(new_fields):
            field_def = inner.fields.get(field_node.name.value)
            if field_def is None:
                continue
            replaced = _substitute_value(
                field_node.value, field_def.type, field_node.name.value, None, provider, random
            )
            if replaced is not None:
                new_fields[index] = graphql.ObjectFieldNode(name=field_node.name, value=replaced)
                replaced_any = True
        return graphql.ObjectValueNode(fields=tuple(new_fields)) if replaced_any else None
    return None


def _strip_id_suffix(name: str) -> str | None:
    # `userId`/`userIds`/`userID`/`userIDs`/`user_id`/`user_ids` -> `user`; bare or non-id names -> None.
    for suffix in ("_ids", "_id"):
        if name.endswith(suffix) and len(name) > len(suffix):
            return name[: -len(suffix)]
    for suffix in ("IDs", "Ids", "ID", "Id"):
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
    # Bare `id`/`ids` falls back to the enclosing field's return type.
    if argument_name in ("id", "ids"):
        return enclosing_field_type
    return None


def iter_operation_pool_values(
    operation_node: graphql.OperationDefinitionNode,
    client_schema: graphql.GraphQLSchema,
) -> Iterator[tuple[str, str]]:
    """Yield `(parent_type_name, value)` tuples for id-typed scalar literals in an operation's arguments."""
    root = _root_type_for(client_schema, operation_node.operation)
    if root is None:
        return
    yield from _iter_walk(operation_node.selection_set, root)


def _iter_walk(
    selection_set: graphql.SelectionSetNode | None,
    parent_type: graphql.GraphQLObjectType,
) -> Iterator[tuple[str, str]]:
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
            yield from _iter_value(argument.value, arg_def.type, argument.name.value, enclosing_field_type)
        if isinstance(unwrapped_return, graphql.GraphQLObjectType):
            yield from _iter_walk(selection.selection_set, unwrapped_return)


def _iter_value(
    value: graphql.ValueNode,
    value_type: graphql.GraphQLType,
    name: str,
    enclosing_field_type: str | None,
) -> Iterator[tuple[str, str]]:
    inner = value_type
    while isinstance(inner, graphql.GraphQLNonNull):
        inner = inner.of_type
    if isinstance(inner, graphql.GraphQLList) and isinstance(value, graphql.ListValueNode):
        for element in value.values:
            yield from _iter_value(element, inner.of_type, name, enclosing_field_type)
        return
    if isinstance(inner, graphql.GraphQLScalarType) and isinstance(value, graphql.StringValueNode):
        parent_type_name = _candidate_parent_type(
            scalar_name=inner.name,
            argument_name=name,
            enclosing_field_type=enclosing_field_type,
        )
        if parent_type_name is not None:
            yield parent_type_name, value.value
        return
    if isinstance(inner, graphql.GraphQLInputObjectType) and isinstance(value, graphql.ObjectValueNode):
        for field_node in value.fields:
            field_def = inner.fields.get(field_node.name.value)
            if field_def is None:
                continue
            yield from _iter_value(field_node.value, field_def.type, field_node.name.value, None)
