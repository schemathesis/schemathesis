"""Substitution of user-configured dictionary entries into GraphQL argument literals.

Bindings are keyed `<Type>.<field>.<argument>`, where `<Type>` is the type that declares the
field, so a nested field's arguments are addressed exactly like a root field's. `*` stands for
any type or field, a dotted suffix descends into input objects, and `[*]` targets list elements.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

import graphql

from schemathesis.config._dictionaries import (
    WILDCARD,
    DictionaryDefinition,
    EntryValue,
    GraphQLBindingPath,
    ParameterDictionaryBinding,
    coerce_entries_for_type,
    is_graphql_binding_key,
    parse_graphql_path,
)
from schemathesis.generation.dictionaries import DictionaryDraw
from schemathesis.specs.graphql._helpers import _root_type_for, _unwrap
from schemathesis.specs.graphql.substitution import _fits_graphql_float, _fits_graphql_int, scalar_value_node

if TYPE_CHECKING:
    from random import Random

    from schemathesis.config import GenerationConfig
    from schemathesis.schemas import APIOperation

# GraphQL scalar name -> the JSON Schema type its entries are coerced to. Scalars outside this
# map (`Boolean`, enums, custom scalars) have no dictionary support.
_SCALAR_TYPE_KEYS: Final[dict[str, str]] = {
    "String": "string",
    "ID": "string",
    "Int": "integer",
    "Float": "number",
}


@dataclass(slots=True, frozen=True)
class _ArgumentBinding:
    path: GraphQLBindingPath
    dictionary: DictionaryDefinition
    probability: float


@dataclass(slots=True, frozen=True)
class OperationBindings:
    """Dictionary bindings that can apply to one operation, most specific first."""

    arguments: tuple[_ArgumentBinding, ...]
    type_wide: dict[str, tuple[DictionaryDefinition, float]]

    @property
    def is_empty(self) -> bool:
        return not self.arguments and not self.type_wide


_NO_BINDINGS: Final = OperationBindings(arguments=(), type_wide={})


def resolve_bindings(operation: APIOperation, generation: GenerationConfig) -> OperationBindings:
    # Precedence mirrors OpenAPI: op-specific argument > global argument > type-wide. Among argument
    # bindings, naming both the type and the field beats a `*` in either position.
    config = operation.schema.config
    dictionaries = config.dictionaries
    if not dictionaries:
        return _NO_BINDINGS
    operation_config = config.operations.get_for_operation(operation)
    ranked: list[tuple[tuple[int, int], _ArgumentBinding]] = []
    seen: set[str] = set()
    for scope, source in ((1, operation_config.parameters), (0, config.parameters)):
        for key, binding in source.items():
            if key in seen or not isinstance(binding, ParameterDictionaryBinding) or not is_graphql_binding_key(key):
                continue
            seen.add(key)
            path = parse_graphql_path(key)
            named = int(path.type_name != WILDCARD) + int(path.field_name != WILDCARD)
            ranked.append(
                (
                    (scope, named),
                    _ArgumentBinding(
                        path=path,
                        dictionary=dictionaries[binding.dictionary],
                        probability=binding.probability,
                    ),
                )
            )
    ranked.sort(key=lambda item: item[0], reverse=True)
    type_wide = {
        type_key: (dictionaries[binding.dictionary], binding.probability)
        for type_key, binding in generation.dictionaries.items()
    }
    return OperationBindings(arguments=tuple(binding for _, binding in ranked), type_wide=type_wide)


@dataclass(slots=True, frozen=True)
class _Context:
    bindings: OperationBindings
    operation_label: str
    random: Random
    draws: list[DictionaryDraw]


def substitute_dictionaries(
    *,
    operation_node: graphql.OperationDefinitionNode,
    client_schema: graphql.GraphQLSchema,
    bindings: OperationBindings,
    operation_label: str,
    random: Random,
) -> list[DictionaryDraw]:
    """Replace argument literals with configured dictionary entries. Returns the draws applied."""
    root = _root_type_for(client_schema, operation_node.operation)
    if root is None:
        return []
    context = _Context(bindings=bindings, operation_label=operation_label, random=random, draws=[])
    _walk(operation_node.selection_set, root, context, ())
    return context.draws


def _walk(
    selection_set: graphql.SelectionSetNode | None,
    parent_type: graphql.GraphQLObjectType,
    context: _Context,
    path: tuple[str, ...],
) -> None:
    assert selection_set is not None, "the operation root and object-typed fields always carry a selection set"
    for selection in selection_set.selections:
        # Arguments inside inline fragments (interface/union selections) are not substituted.
        if not isinstance(selection, graphql.FieldNode):
            continue
        field_def = parent_type.fields.get(selection.name.value)
        if field_def is None:
            # Meta-fields such as `__typename` are valid selections but absent from the type's fields.
            continue
        field_path = (*path, selection.name.value)
        field = (parent_type.name, selection.name.value)
        for argument in selection.arguments:
            argument_definition = field_def.args.get(argument.name.value)
            assert argument_definition is not None, "a generated argument exists on its field"
            replaced = _substitute_value(
                argument.value,
                argument_definition.type,
                context,
                field,
                (argument.name.value,),
                (*field_path, argument.name.value),
            )
            if replaced is not None:
                argument.value = replaced
        unwrapped_return = _unwrap(field_def.type)
        if isinstance(unwrapped_return, graphql.GraphQLObjectType):
            _walk(selection.selection_set, unwrapped_return, context, field_path)


def _substitute_value(
    value: graphql.ValueNode,
    value_type: graphql.GraphQLType,
    context: _Context,
    field: tuple[str, str],
    argument_path: tuple[str, ...],
    body_path: tuple[str, ...],
) -> graphql.ValueNode | None:
    inner = value_type
    while isinstance(inner, graphql.GraphQLNonNull):
        inner = inner.of_type
    if isinstance(inner, graphql.GraphQLList) and isinstance(value, graphql.ListValueNode):
        # List elements reuse the argument's `body_path`; individual elements are not distinguished.
        new_values = list(value.values)
        replaced_any = False
        for index, element in enumerate(new_values):
            replaced = _substitute_value(element, inner.of_type, context, field, (*argument_path, WILDCARD), body_path)
            if replaced is not None:
                new_values[index] = replaced
                replaced_any = True
        return graphql.ListValueNode(values=tuple(new_values)) if replaced_any else None
    if isinstance(inner, graphql.GraphQLScalarType):
        return _scalar_node(inner.name, context, field, argument_path, body_path)
    if isinstance(inner, graphql.GraphQLInputObjectType) and isinstance(value, graphql.ObjectValueNode):
        new_fields = list(value.fields)
        replaced_any = False
        for index, field_node in enumerate(new_fields):
            field_def = inner.fields.get(field_node.name.value)
            assert field_def is not None, "a generated input-object field exists on its input type"
            replaced = _substitute_value(
                field_node.value,
                field_def.type,
                context,
                field,
                (*argument_path, field_node.name.value),
                (*body_path, field_node.name.value),
            )
            if replaced is not None:
                new_fields[index] = graphql.ObjectFieldNode(name=field_node.name, value=replaced)
                replaced_any = True
        return graphql.ObjectValueNode(fields=tuple(new_fields)) if replaced_any else None
    return None


def _scalar_node(
    scalar_name: str,
    context: _Context,
    field: tuple[str, str],
    argument_path: tuple[str, ...],
    body_path: tuple[str, ...],
) -> graphql.ValueNode | None:
    type_key = _SCALAR_TYPE_KEYS.get(scalar_name)
    if type_key is None:
        return None
    found = _find_binding(context, field, argument_path, type_key)
    if found is None:
        return None
    dictionary, probability = found
    if context.random.random() >= probability:
        return None
    eligible = _eligible_entries(dictionary, scalar_name, type_key)
    if not eligible:
        return None
    entry_index, entry_value = context.random.choice(eligible)
    context.draws.append(
        DictionaryDraw(
            dictionary=dictionary.name,
            source_kind=dictionary.source_kind,
            source_path=dictionary.source_path,
            entry_index=entry_index,
            operation_label=context.operation_label,
            parameter_location="body",
            parameter_name=argument_path[-1] if argument_path[-1] != WILDCARD else argument_path[-2],
            value=entry_value,
            matches_schema=True,
            body_path="/" + "/".join(body_path),
        )
    )
    return scalar_value_node(scalar_name, entry_value)


def _find_binding(
    context: _Context, field: tuple[str, str], argument_path: tuple[str, ...], type_key: str
) -> tuple[DictionaryDefinition, float] | None:
    type_name, field_name = field
    for binding in context.bindings.arguments:
        path = binding.path
        if (
            path.type_name in (WILDCARD, type_name)
            and path.field_name in (WILDCARD, field_name)
            and path.argument == argument_path
        ):
            return binding.dictionary, binding.probability
    return context.bindings.type_wide.get(type_key)


def _eligible_entries(
    dictionary: DictionaryDefinition, scalar_name: str, type_key: str
) -> list[tuple[int, EntryValue]]:
    entries = coerce_entries_for_type(dictionary.entries, type_key)
    if scalar_name == "Int":
        return [entry for entry in entries if _fits_graphql_int(entry[1])]
    if scalar_name == "Float":
        return [entry for entry in entries if _fits_graphql_float(entry[1])]
    return list(entries)
