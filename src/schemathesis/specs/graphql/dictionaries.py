"""Substitution of user-configured dictionary entries into GraphQL argument literals."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

import graphql
import jsonschema_rs

from schemathesis.config._dictionaries import (
    WILDCARD,
    DictionaryDefinition,
    DictionaryEntry,
    EntrySchema,
    EntryValue,
    GraphQLBindingPath,
    ParameterDictionaryBinding,
    coerce_entries_for_type,
    is_graphql_binding_key,
    parse_graphql_path,
)
from schemathesis.core.errors import InvalidSchema
from schemathesis.core.jsonschema import CANONICALIZE_DRAFT_BY_VALIDATOR
from schemathesis.core.transforms import format_body_path
from schemathesis.generation.dictionaries import DictionaryDraw, draw_paired_entries, pair_dictionaries
from schemathesis.generation.jsonschema.builder import EMPTY_STRATEGY, build
from schemathesis.specs.graphql._helpers import (
    _root_type_for,
    _unwrap,
    replace_field,
    replace_node,
    replace_selections,
)
from schemathesis.specs.graphql.substitution import fits_graphql_float, fits_graphql_int, scalar_value_node

if TYPE_CHECKING:
    from random import Random

    from hypothesis import strategies as st

    from schemathesis.config import GenerationConfig
    from schemathesis.core.jsonschema.types import JsonValue
    from schemathesis.schemas import APIOperation

    # Hypothesis' `draw`, as far as suggested schemas use it.
    DrawSchemaValue = Callable[[st.SearchStrategy[JsonValue]], JsonValue]

# JSON Schema type that entries for each scalar are coerced to; `Boolean`, enums and custom scalars get no dictionaries.
_SCALAR_TYPE_KEYS: Final[dict[str, str]] = {
    "String": "string",
    "ID": "string",
    "Int": "integer",
    "Float": "number",
}
# Suggested schemas are written for no document in particular, so they are read by the latest draft.
_SCHEMA_DRAFT: Final = CANONICALIZE_DRAFT_BY_VALIDATOR[jsonschema_rs.Draft202012Validator]


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
    suggestions = operation.schema.suggestions
    dictionaries = {**suggestions.dictionaries, **config.dictionaries}
    if not dictionaries:
        return _NO_BINDINGS
    operation_config = config.operations.get_for_operation(operation)
    ranked: list[tuple[tuple[int, int], _ArgumentBinding]] = []
    seen: set[str] = set()
    sources = (operation_config.parameters, config.parameters, *suggestions.parameters(operation.label))
    # The user's bindings rank above suggested values.
    for scope, source in zip(range(1, 1 - len(sources), -1), sources, strict=True):
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
    # Draws values for suggested schemas; without it they are left out.
    draw: DrawSchemaValue | None
    draws: list[DictionaryDraw]


def substitute_dictionaries(
    *,
    operation_node: graphql.OperationDefinitionNode,
    client_schema: graphql.GraphQLSchema,
    bindings: OperationBindings,
    operation_label: str,
    random: Random,
    draw: DrawSchemaValue | None = None,
) -> tuple[graphql.OperationDefinitionNode, list[DictionaryDraw]]:
    """Replace argument literals with configured dictionary entries; also return the applied draws."""
    root = _root_type_for(client_schema, operation_node.operation)
    if root is None:
        return operation_node, []
    context = _Context(bindings=bindings, operation_label=operation_label, random=random, draw=draw, draws=[])
    selection_set = _walk(operation_node.selection_set, root, context, ())
    if selection_set is not operation_node.selection_set:
        operation_node = replace_node(operation_node, selection_set=selection_set)
    return operation_node, context.draws


def _walk(
    selection_set: graphql.SelectionSetNode | None,
    parent_type: graphql.GraphQLObjectType,
    context: _Context,
    path: tuple[str, ...],
) -> graphql.SelectionSetNode:
    assert selection_set is not None, "the operation root and object-typed fields always carry a selection set"
    selections = list(selection_set.selections)
    for index, selection in enumerate(selection_set.selections):
        # Arguments inside inline fragments (interface/union selections) are not substituted.
        if not isinstance(selection, graphql.FieldNode):
            continue
        field_def = parent_type.fields.get(selection.name.value)
        if field_def is None:
            # Meta-fields such as `__typename` are valid selections but absent from the type's fields.
            continue
        field_path = (*path, selection.name.value)
        field = (parent_type.name, selection.name.value)
        paired = _draw_pairs(selection, field_def, field, field_path, context)
        arguments = list(selection.arguments or ())
        for position, argument in enumerate(selection.arguments or ()):
            if argument.name.value in paired:
                arguments[position] = replace_node(argument, value=paired[argument.name.value])
                continue
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
                arguments[position] = replace_node(argument, value=replaced)
        nested = selection.selection_set
        unwrapped_return = _unwrap(field_def.type)
        if isinstance(unwrapped_return, graphql.GraphQLObjectType):
            nested = _walk(selection.selection_set, unwrapped_return, context, field_path)
        selections[index] = replace_field(selection, arguments, nested)
    return replace_selections(selection_set, selections)


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
    eligible: list[tuple[int, EntryValue | EntrySchema]] = [*_eligible_entries(dictionary, scalar_name, type_key)]
    if context.draw is not None:
        eligible.extend(
            (entry.index, entry.value) for entry in dictionary.entries if isinstance(entry.value, EntrySchema)
        )
    if not eligible:
        return None
    entry_index, picked = context.random.choice(eligible)
    if isinstance(picked, EntrySchema):
        assert context.draw is not None
        drawn = _schema_value(context.draw, picked, entry_index, scalar_name, type_key)
        if drawn is None:
            return None
        entry_value = drawn
    else:
        entry_value = picked
    _record(context, dictionary, entry_index, entry_value, argument_path, body_path)
    return scalar_value_node(scalar_name, entry_value)


def _schema_value(
    draw: DrawSchemaValue, schema: EntrySchema, entry_index: int, scalar_name: str, type_key: str
) -> EntryValue | None:
    """A value the suggested schema admits, as the scalar takes it; `None` when there is none or it does not fit."""
    try:
        strategy = build(json.loads(schema.text), draft=_SCHEMA_DRAFT, formats={})
    except InvalidSchema:
        return None
    if strategy is EMPTY_STRATEGY:
        return None
    value = draw(strategy)
    # An entry is a text or a number, like the values written in a dictionary.
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        return None
    fitting = _fitting((DictionaryEntry(value=value, index=entry_index),), scalar_name, type_key)
    return fitting[0][1] if fitting else None


def _draw_pairs(
    selection: graphql.FieldNode,
    field_def: graphql.GraphQLField,
    field: tuple[str, str],
    field_path: tuple[str, ...],
    context: _Context,
) -> dict[str, graphql.ValueNode]:
    """Values for scalar arguments bound to dictionaries whose entries remember each other, drawn together."""
    bound: dict[str, tuple[_ArgumentBinding, str]] = {}
    for argument in selection.arguments or ():
        name = argument.name.value
        argument_type = graphql.get_nullable_type(field_def.args[name].type)
        binding = _find_argument_binding(context, field, (name,))
        if (
            binding is not None
            and isinstance(argument_type, graphql.GraphQLScalarType)
            and argument_type.name in _SCALAR_TYPE_KEYS
        ):
            bound[name] = (binding, argument_type.name)
    # Only a dictionary whose entries remember values of another one leads a pair.
    if not any(binding.dictionary.origin for binding, _ in bound.values()):
        return {}
    # The first argument bound to a dictionary takes part in pairing; another one bound to it draws on its own.
    argument_for: dict[str, str] = {}
    for name, (binding, _) in bound.items():
        argument_for.setdefault(binding.dictionary.name, name)
    # Values converted to each argument's type, by entry index.
    eligible: dict[str, dict[int, EntryValue]] = {}
    for name in argument_for.values():
        binding, scalar_name = bound[name]
        eligible[name] = dict(_eligible_entries(binding.dictionary, scalar_name, _SCALAR_TYPE_KEYS[scalar_name]))
    pairs = pair_dictionaries(
        {
            dictionary_name: (bound[name][0].dictionary, bound[name][0].probability, eligible[name])
            for dictionary_name, name in argument_for.items()
        }
    )
    values: dict[str, graphql.ValueNode] = {}
    for dictionary_name, entry_index in draw_paired_entries(context.random, pairs).items():
        name = argument_for[dictionary_name]
        binding, scalar_name = bound[name]
        entry_value = eligible[name][entry_index]
        _record(context, binding.dictionary, entry_index, entry_value, (name,), (*field_path, name))
        values[name] = scalar_value_node(scalar_name, entry_value)
    return values


def _record(
    context: _Context,
    dictionary: DictionaryDefinition,
    entry_index: int,
    entry_value: EntryValue,
    argument_path: tuple[str, ...],
    body_path: tuple[str, ...],
) -> None:
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
            body_path=format_body_path(body_path),
        )
    )


def _find_argument_binding(
    context: _Context, field: tuple[str, str], argument_path: tuple[str, ...]
) -> _ArgumentBinding | None:
    type_name, field_name = field
    for binding in context.bindings.arguments:
        path = binding.path
        if (
            path.type_name in (WILDCARD, type_name)
            and path.field_name in (WILDCARD, field_name)
            and path.argument == argument_path
        ):
            return binding
    return None


def _find_binding(
    context: _Context, field: tuple[str, str], argument_path: tuple[str, ...], type_key: str
) -> tuple[DictionaryDefinition, float] | None:
    binding = _find_argument_binding(context, field, argument_path)
    if binding is not None:
        return binding.dictionary, binding.probability
    return context.bindings.type_wide.get(type_key)


def _eligible_entries(
    dictionary: DictionaryDefinition, scalar_name: str, type_key: str
) -> list[tuple[int, EntryValue]]:
    return _fitting(dictionary.entries, scalar_name, type_key)


def _fitting(entries: tuple[DictionaryEntry, ...], scalar_name: str, type_key: str) -> list[tuple[int, EntryValue]]:
    coerced = coerce_entries_for_type(entries, type_key)
    if scalar_name == "Int":
        return [entry for entry in coerced if fits_graphql_int(entry[1])]
    if scalar_name == "Float":
        return [entry for entry in coerced if fits_graphql_float(entry[1])]
    return list(coerced)
