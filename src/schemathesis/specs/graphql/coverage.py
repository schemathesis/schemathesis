"""Deterministic coverage cases for GraphQL operations."""

from __future__ import annotations

from collections.abc import Iterator
from typing import TYPE_CHECKING, cast

import graphql

from schemathesis.core.parameters import ParameterLocation
from schemathesis.core.timing import Instant
from schemathesis.generation import GenerationMode
from schemathesis.generation.meta import CoverageScenario, PhaseInfo
from schemathesis.specs.graphql.scalars import UnknownScalar, scalar_strategies

if TYPE_CHECKING:
    from schemathesis.generation.case import Case
    from schemathesis.schemas import APIOperation
    from schemathesis.specs.graphql.schemas import GraphQLOperationDefinition

BUILTIN_SCALAR_VALUES: dict[str, object] = {"Int": 0, "Float": 0.0, "String": "", "ID": "1", "Boolean": True}

InputPath = tuple[graphql.GraphQLInputObjectType, ...]


class ScalarLiterals:
    """One literal per scalar for the whole operation, so every case agrees on it."""

    __slots__ = ("cache",)

    def __init__(self) -> None:
        self.cache: dict[str, graphql.ValueNode] = {}

    def get(self, type_: graphql.GraphQLScalarType) -> graphql.ValueNode:
        name = type_.name
        node: graphql.ValueNode | None
        if name in self.cache:
            return self.cache[name]
        if name in BUILTIN_SCALAR_VALUES:
            node = graphql.ast_from_value(BUILTIN_SCALAR_VALUES[name], type_)
        else:
            strategy = scalar_strategies().get(name)
            if strategy is None:
                raise UnknownScalar(name)
            from schemathesis.generation.hypothesis.examples import generate_one

            node = generate_one(strategy)
        assert node is not None
        self.cache[name] = node
        return node


def iter_coverage_cases(
    operation: APIOperation, schema: graphql.GraphQLSchema, modes: list[GenerationMode]
) -> Iterator[Case]:
    if GenerationMode.POSITIVE not in modes:
        return
    definition = cast("GraphQLOperationDefinition", operation.definition)
    field = definition.raw
    scalars = ScalarLiterals()
    instant = Instant()
    arguments = default_arguments(field, scalars)
    if arguments is None:
        return
    yield _make_case(
        operation,
        body=render(definition, arguments, default_selection(field.type)),
        mode=GenerationMode.POSITIVE,
        scenario=CoverageScenario.DEFAULT_POSITIVE_TEST,
        description="Default positive test case",
        location=root_coordinate(definition, schema),
        parameter=None,
        generation_time=instant.elapsed,
    )


def default_arguments(field: graphql.GraphQLField, scalars: ScalarLiterals) -> list[graphql.ArgumentNode] | None:
    """Required arguments filled with fixed values, or `None` when one of them has no value."""
    arguments = []
    for name, argument in field.args.items():
        if not graphql.is_required_argument(argument):
            continue
        value = default_value(argument.type, (), scalars)
        if value is None:
            return None
        arguments.append(graphql.ArgumentNode(name=graphql.NameNode(value=name), value=value))
    return arguments


def default_value(
    type_: graphql.GraphQLInputType, path: InputPath, scalars: ScalarLiterals
) -> graphql.ValueNode | None:
    """A fixed valid value, or `None` when the type can only be filled by recursing forever."""
    if isinstance(type_, graphql.GraphQLNonNull):
        return default_value(type_.of_type, path, scalars)
    if isinstance(type_, graphql.GraphQLList):
        item_type = type_.of_type
        if graphql.get_named_type(item_type) in path:
            return graphql.ListValueNode(values=())
        item = default_value(item_type, path, scalars)
        return graphql.ListValueNode(values=() if item is None else (item,))
    if isinstance(type_, graphql.GraphQLScalarType):
        return scalars.get(type_)
    if isinstance(type_, graphql.GraphQLEnumType):
        return graphql.EnumValueNode(value=next(iter(type_.values)))
    assert isinstance(type_, graphql.GraphQLInputObjectType)
    inner_path = (*path, type_)
    fields = []
    for name, input_field in type_.fields.items():
        if not graphql.is_required_input_field(input_field):
            continue
        field_type = input_field.type
        assert isinstance(field_type, graphql.GraphQLNonNull)
        if not isinstance(field_type.of_type, graphql.GraphQLList) and field_type.of_type in inner_path:
            # A cycle of required non-list fields has no finite value
            return None
        value = default_value(field_type, inner_path, scalars)
        if value is None:
            return None
        fields.append((name, value))
    return _object_value(fields)


def _object_value(fields: list[tuple[str, graphql.ValueNode]]) -> graphql.ObjectValueNode:
    return graphql.ObjectValueNode(
        fields=tuple(graphql.ObjectFieldNode(name=graphql.NameNode(value=name), value=value) for name, value in fields)
    )


def default_selection(type_: graphql.GraphQLOutputType) -> graphql.SelectionSetNode | None:
    """Scalar and enum fields without required arguments, or `__typename` when there are none."""
    named = graphql.get_named_type(type_)
    if graphql.is_leaf_type(named):
        return None
    names = []
    if isinstance(named, (graphql.GraphQLObjectType, graphql.GraphQLInterfaceType)):
        names = [
            name
            for name, field in named.fields.items()
            if graphql.is_leaf_type(graphql.get_named_type(field.type))
            and not any(graphql.is_required_argument(argument) for argument in field.args.values())
        ]
    return graphql.SelectionSetNode(selections=tuple(_field_node(name) for name in names or ["__typename"]))


def _field_node(
    name: str,
    arguments: list[graphql.ArgumentNode] | None = None,
    selection: graphql.SelectionSetNode | None = None,
) -> graphql.FieldNode:
    return graphql.FieldNode(
        name=graphql.NameNode(value=name),
        arguments=tuple(arguments or ()),
        directives=(),
        selection_set=selection,
    )


def render(
    definition: GraphQLOperationDefinition,
    arguments: list[graphql.ArgumentNode],
    selection: graphql.SelectionSetNode | None,
) -> str:
    field = _field_node(definition.field_name, arguments, selection)
    kind = graphql.OperationType.MUTATION if definition.is_mutation else graphql.OperationType.QUERY
    document = graphql.DocumentNode(
        definitions=(
            graphql.OperationDefinitionNode(
                operation=kind,
                variable_definitions=(),
                directives=(),
                selection_set=graphql.SelectionSetNode(selections=(field,)),
            ),
        )
    )
    return graphql.print_ast(document)


def root_coordinate(definition: GraphQLOperationDefinition, schema: graphql.GraphQLSchema) -> str:
    root = schema.mutation_type if definition.is_mutation else schema.query_type
    assert root is not None
    return f"{root.name}.{definition.field_name}"


def _make_case(
    operation: APIOperation,
    *,
    body: str,
    mode: GenerationMode,
    scenario: CoverageScenario,
    description: str,
    location: str,
    parameter: str | None,
    generation_time: float,
) -> Case:
    from schemathesis.specs.graphql.schemas import build_case

    return build_case(
        operation,
        body=body,
        mode=mode,
        phase=PhaseInfo.coverage(scenario, description, location, parameter, parameter_location=ParameterLocation.BODY),
        generation_time=generation_time,
    )
