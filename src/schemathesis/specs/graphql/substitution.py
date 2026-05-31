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

from schemathesis.core.text import to_pascal_case, to_snake_case
from schemathesis.specs.graphql._helpers import _root_type_for, _unwrap
from schemathesis.specs.graphql.handles import HANDLE_SCALARS, Handle, SchemaIndex

if TYPE_CHECKING:
    from collections.abc import Iterator

    from schemathesis.specs.graphql.extra_data_source import GraphQLResourcePool

# Probability that captured values replace matching argument literals;
# the remaining 0.3 leaves Hypothesis room to explore fresh values.
SUBSTITUTION_PROBABILITY: Final = 0.7

# Returns a substitution value for the given handle, or None to keep the original.
ValueProvider = Callable[[Handle, Random], str | None]


def substitute_pool_values(
    *,
    operation_node: graphql.OperationDefinitionNode,
    client_schema: graphql.GraphQLSchema,
    pool: GraphQLResourcePool,
    random: Random,
    schema_index: SchemaIndex | None = None,
) -> None:
    """Replace matching argument literals with captured pool values."""

    def provider(handle: Handle, random: Random) -> str | None:
        return pool.draw(handle=handle, random=random)

    _substitute(operation_node, client_schema, provider, random, schema_index)


def substitute_bundle_values(
    *,
    operation_node: graphql.OperationDefinitionNode,
    client_schema: graphql.GraphQLSchema,
    bundle_values: dict[Handle, str],
    random: Random,
    exploration_rate: float = 0.0,
    schema_index: SchemaIndex | None = None,
) -> None:
    """Replace handle-typed argument literals with values from the given handle to value mapping.

    `exploration_rate` is the per-argument probability of leaving the
    strategy-generated value in place, forcing discovery of bugs reachable only
    via unknown ids.
    """

    def provider(handle: Handle, random: Random) -> str | None:
        if exploration_rate > 0.0 and random.random() < exploration_rate:
            return None
        return bundle_values.get(handle)

    _substitute(operation_node, client_schema, provider, random, schema_index)


def _substitute(
    operation_node: graphql.OperationDefinitionNode,
    client_schema: graphql.GraphQLSchema,
    provider: ValueProvider,
    random: Random,
    index: SchemaIndex | None,
) -> None:
    root = _root_type_for(client_schema, operation_node.operation)
    if root is None:
        return
    _walk(operation_node.selection_set, root, provider, random, index)


def _walk(
    selection_set: graphql.SelectionSetNode | None,
    parent_type: graphql.GraphQLObjectType,
    provider: ValueProvider,
    random: Random,
    index: SchemaIndex | None,
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
            argument_definition = field_def.args.get(argument.name.value)
            if argument_definition is None:
                continue
            new_value = _substitute_value(
                argument.value,
                argument_definition.type,
                argument.name.value,
                enclosing_field_type,
                provider,
                random,
                index,
            )
            if new_value is not None:
                argument.value = new_value
        if isinstance(unwrapped_return, graphql.GraphQLObjectType):
            _walk(selection.selection_set, unwrapped_return, provider, random, index)


def _substitute_value(
    value: graphql.ValueNode,
    value_type: graphql.GraphQLType,
    name: str,
    enclosing_field_type: str | None,
    provider: ValueProvider,
    random: Random,
    index: SchemaIndex | None,
) -> graphql.ValueNode | None:
    """Return a substituted value if the provider returns a candidate; otherwise None."""
    inner = value_type
    while isinstance(inner, graphql.GraphQLNonNull):
        inner = inner.of_type
    if isinstance(inner, graphql.GraphQLList) and isinstance(value, graphql.ListValueNode):
        new_values = list(value.values)
        replaced_any = False
        for list_index, element in enumerate(new_values):
            replaced = _substitute_value(element, inner.of_type, name, enclosing_field_type, provider, random, index)
            if replaced is not None:
                new_values[list_index] = replaced
                replaced_any = True
        return graphql.ListValueNode(values=tuple(new_values)) if replaced_any else None
    if isinstance(inner, graphql.GraphQLScalarType):
        handle = candidate_handle(
            scalar_name=inner.name,
            argument_name=name,
            enclosing_field_type=enclosing_field_type,
            index=index,
        )
        if handle is None:
            return None
        candidate = provider(handle, random)
        if candidate is None:
            return None
        return graphql.StringValueNode(value=candidate)
    if isinstance(inner, graphql.GraphQLInputObjectType) and isinstance(value, graphql.ObjectValueNode):
        new_fields = list(value.fields)
        replaced_any = False
        for field_index, field_node in enumerate(new_fields):
            field_def = inner.fields.get(field_node.name.value)
            if field_def is None:
                continue
            replaced = _substitute_value(
                field_node.value, field_def.type, field_node.name.value, None, provider, random, index
            )
            if replaced is not None:
                new_fields[field_index] = graphql.ObjectFieldNode(name=field_node.name, value=replaced)
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


# Identifier-bearing suffixes a non-id handle field may carry (precision gate).
IDENTIFIER_SUFFIXES: Final = ("Id", "Uuid", "Guid", "Path", "Slug", "Key", "Code", "Ref", "Name", "Arn")
# Free-text fields are never eligible handles even with an identifier suffix.
FREE_TEXT_FIELDS: Final = frozenset({"description", "comment", "body", "content", "text", "message", "summary", "note"})
# Leading role qualifiers stripped before naming the candidate type (`targetProject` -> `Project`).
ROLE_PREFIXES: Final = ("target", "source", "new", "old", "parent", "child")
# Bare argument names that identify a record by a non-id field of the enclosing return type
# (`product(slug:): Product`). Deliberately excludes `name` to favor precision.
BARE_IDENTIFIER_NAMES: Final = frozenset(
    {"slug", "sku", "code", "key", "token", "uuid", "guid", "arn", "handle", "externalreference"}
)


def candidate_handle(
    *,
    scalar_name: str,
    argument_name: str,
    enclosing_field_type: str | None,
    index: SchemaIndex | None = None,
) -> Handle | None:
    """Resolve a scalar argument to the producer Handle it should be filled from."""
    if scalar_name != "ID":
        stripped = _strip_id_suffix(scalar_name)
        if stripped is not None:
            return Handle(stripped, "id")
    else:
        stripped = _strip_id_suffix(argument_name)
        if stripped is not None:
            return Handle(stripped[:1].upper() + stripped[1:], "id")
        if argument_name in ("id", "ids") and enclosing_field_type is not None:
            return Handle(enclosing_field_type, "id")
    if index is None or scalar_name not in HANDLE_SCALARS:
        return None
    if enclosing_field_type is not None and argument_name.lower() in BARE_IDENTIFIER_NAMES:
        bare = _bare_field_handle(argument_name, enclosing_field_type, index)
        if bare is not None:
            return bare
    return _lexical_handle(argument_name, index)


def _bare_field_handle(argument_name: str, enclosing_field_type: str, index: SchemaIndex) -> Handle | None:
    cue = argument_name.lower()
    best: tuple[int, int, str] | None = None
    chosen: str | None = None
    for field in index.leaf_string_id_fields(enclosing_field_type):
        rank = _field_rank(field, cue)
        if rank is None:
            continue
        key = (rank, len(field), field)
        if best is None or key < best:
            best, chosen = key, field
    return Handle(enclosing_field_type, chosen) if chosen is not None else None


def _lexical_handle(argument_name: str, index: SchemaIndex) -> Handle | None:
    tokens = [token for token in to_snake_case(argument_name).split("_") if token]
    if tokens and tokens[0] in ROLE_PREFIXES:
        tokens = tokens[1:]
    if len(tokens) < 2:
        return None
    type_name = to_pascal_case(tokens[0])
    if not index.has_object_type(type_name):
        return None
    cue = "".join(tokens[1:]).lower()
    best: tuple[int, int, str] | None = None
    chosen: str | None = None
    for field in index.leaf_string_id_fields(type_name):
        rank = _field_rank(field, cue)
        if rank is None:
            continue
        key = (rank, len(field), field)
        if best is None or key < best:
            best, chosen = key, field
    return Handle(type_name, chosen) if chosen is not None else None


def _field_rank(field: str, cue: str) -> int | None:
    """Precision-gated rank of a candidate field against the cue; lower is better, None means ineligible."""
    lower = field.lower()
    if lower in FREE_TEXT_FIELDS:
        return None
    has_identifier_suffix = any(field.endswith(suffix) for suffix in IDENTIFIER_SUFFIXES)
    if lower == cue:
        return 0
    if not has_identifier_suffix:
        return None
    for suffix in IDENTIFIER_SUFFIXES:
        if field.endswith(suffix) and suffix.lower() == cue:
            return 1
    if cue.endswith(lower):
        return 2
    return None


def iter_operation_pool_values(
    operation_node: graphql.OperationDefinitionNode,
    client_schema: graphql.GraphQLSchema,
) -> Iterator[tuple[Handle, str]]:
    """Yield `(handle, value)` tuples for id-typed scalar literals in an operation's arguments."""
    root = _root_type_for(client_schema, operation_node.operation)
    if root is None:
        return
    yield from _iter_walk(operation_node.selection_set, root)


def _iter_walk(
    selection_set: graphql.SelectionSetNode | None,
    parent_type: graphql.GraphQLObjectType,
) -> Iterator[tuple[Handle, str]]:
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
            argument_definition = field_def.args.get(argument.name.value)
            if argument_definition is None:
                continue
            yield from _iter_value(argument.value, argument_definition.type, argument.name.value, enclosing_field_type)
        if isinstance(unwrapped_return, graphql.GraphQLObjectType):
            yield from _iter_walk(selection.selection_set, unwrapped_return)


def _iter_value(
    value: graphql.ValueNode,
    value_type: graphql.GraphQLType,
    name: str,
    enclosing_field_type: str | None,
) -> Iterator[tuple[Handle, str]]:
    inner = value_type
    while isinstance(inner, graphql.GraphQLNonNull):
        inner = inner.of_type
    if isinstance(inner, graphql.GraphQLList) and isinstance(value, graphql.ListValueNode):
        for element in value.values:
            yield from _iter_value(element, inner.of_type, name, enclosing_field_type)
        return
    if isinstance(inner, graphql.GraphQLScalarType) and isinstance(value, graphql.StringValueNode):
        handle = candidate_handle(
            scalar_name=inner.name,
            argument_name=name,
            enclosing_field_type=enclosing_field_type,
            index=None,
        )
        if handle is not None:
            yield handle, value.value
        return
    if isinstance(inner, graphql.GraphQLInputObjectType) and isinstance(value, graphql.ObjectValueNode):
        for field_node in value.fields:
            field_def = inner.fields.get(field_node.name.value)
            if field_def is None:
                continue
            yield from _iter_value(field_node.value, field_def.type, field_node.name.value, None)
