"""Deterministic coverage cases for GraphQL operations."""

from __future__ import annotations

import itertools
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
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
ValuePath = tuple[str | int, ...]
# Name, type and whether the argument or input field is required
FieldSpec = tuple[str, graphql.GraphQLInputType, bool]

# Literals that input coercion rejects for each built-in scalar; `1` for `Float` and `ID`, or `"a"` for `ID`, coerce
INCORRECT_LITERALS: dict[str, tuple[graphql.ValueNode, ...]] = {
    "Int": (
        graphql.StringValueNode(value="a"),
        graphql.BooleanValueNode(value=True),
        graphql.FloatValueNode(value="1.5"),
    ),
    "Float": (graphql.StringValueNode(value="a"), graphql.BooleanValueNode(value=True)),
    "String": (graphql.IntValueNode(value="1"), graphql.BooleanValueNode(value=True)),
    "ID": (graphql.BooleanValueNode(value=True), graphql.FloatValueNode(value="1.5")),
    "Boolean": (graphql.IntValueNode(value="1"), graphql.StringValueNode(value="a")),
}
INT_ABOVE_MAXIMUM = graphql.IntValueNode(value=str(2**31))
INT_BELOW_MINIMUM = graphql.IntValueNode(value=str(-(2**31) - 1))


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
    definition = cast("GraphQLOperationDefinition", operation.definition)
    field = definition.raw
    scalars = ScalarLiterals()
    instant = Instant()
    defaults = with_value(field, (), None, scalars)
    if defaults is None:
        return
    coordinate = root_coordinate(definition, schema)
    selection = default_selection(field.type)
    if GenerationMode.POSITIVE in modes:
        yield _make_case(
            operation,
            body=render(definition, defaults, selection),
            mode=GenerationMode.POSITIVE,
            scenario=CoverageScenario.DEFAULT_POSITIVE_TEST,
            description="Default positive test case",
            location=coordinate,
            parameter=None,
            generation_time=instant.elapsed,
        )
    if GenerationMode.NEGATIVE in modes:
        for element in iter_input_elements(field, coordinate):
            for scenario, description, path, value in negative_values(element, scalars):
                instant = Instant()
                try:
                    arguments = with_value(field, path, value, scalars)
                except UnknownScalar:
                    # Only required arguments of the operation field must have a known scalar
                    continue
                if arguments is None:
                    continue
                yield _make_case(
                    operation,
                    body=render(definition, arguments, selection),
                    mode=GenerationMode.NEGATIVE,
                    scenario=scenario,
                    description=description,
                    location=element.location,
                    parameter=element.parameter,
                    generation_time=instant.elapsed,
                )
        instant = Instant()
        name = unused_name(field.args)
        yield _make_case(
            operation,
            body=render(definition, [*defaults, _argument_node(name, graphql.IntValueNode(value="0"))], selection),
            mode=GenerationMode.NEGATIVE,
            scenario=CoverageScenario.UNKNOWN_ARGUMENT,
            description=f"Unknown argument `{name}`",
            location=coordinate,
            parameter=name,
            generation_time=instant.elapsed,
        )


@dataclass(slots=True)
class InputElement:
    """An argument, input field or list item that a case changes."""

    path: ValuePath
    location: str
    type_: graphql.GraphQLInputType
    required: bool
    one_of_parent: bool

    @property
    def parameter(self) -> str:
        return ".".join(map(str, self.path))


def iter_input_elements(field: graphql.GraphQLField, coordinate: str) -> Iterator[InputElement]:
    """Arguments, the input fields reached through them and list items, depth first."""
    for name, argument in field.args.items():
        yield from _walk(
            argument.type, (name,), f"{coordinate}({name}:)", graphql.is_required_argument(argument), False, ()
        )


def _walk(
    type_: graphql.GraphQLInputType,
    path: ValuePath,
    location: str,
    required: bool,
    one_of_parent: bool,
    types: InputPath,
) -> Iterator[InputElement]:
    nullable = graphql.get_nullable_type(type_)
    if nullable in types:
        # Visited already on this path
        return
    yield InputElement(path=path, location=location, type_=type_, required=required, one_of_parent=one_of_parent)
    if isinstance(nullable, graphql.GraphQLList):
        yield from _walk(nullable.of_type, (*path, 0), location, False, False, types)
    elif isinstance(nullable, graphql.GraphQLInputObjectType):
        is_one_of = getattr(nullable, "is_one_of", False)
        for name, input_field in nullable.fields.items():
            yield from _walk(
                input_field.type,
                (*path, name),
                f"{nullable.name}.{name}",
                graphql.is_required_input_field(input_field),
                is_one_of,
                (*types, nullable),
            )


def negative_values(
    element: InputElement, scalars: ScalarLiterals
) -> Iterator[tuple[CoverageScenario, str, ValuePath, graphql.ValueNode | None]]:
    """Scenario, description, and the value to put at a path; `None` leaves the element at the path out."""
    path = element.path
    parameter = element.parameter
    incorrect = f"Incorrect type for `{parameter}`"
    missing_field = f"Missing required field `{parameter}`"
    if element.required:
        if len(path) == 1:
            yield CoverageScenario.MISSING_PARAMETER, f"Missing required argument `{parameter}`", path, None
        else:
            yield CoverageScenario.OBJECT_MISSING_REQUIRED_PROPERTY, missing_field, path, None
    type_ = element.type_
    if isinstance(type_, graphql.GraphQLNonNull) or element.one_of_parent:
        yield CoverageScenario.INCORRECT_TYPE, incorrect, path, graphql.NullValueNode()
    nullable = graphql.get_nullable_type(type_)
    if isinstance(nullable, graphql.GraphQLList):
        # List items get their own cases
        return
    if isinstance(nullable, graphql.GraphQLScalarType):
        # Custom scalars may accept any literal
        for literal in INCORRECT_LITERALS.get(nullable.name, ()):
            yield CoverageScenario.INCORRECT_TYPE, incorrect, path, literal
        if nullable.name == "Int":
            above, below = f"Value above maximum for `{parameter}`", f"Value below minimum for `{parameter}`"
            yield CoverageScenario.VALUE_ABOVE_MAXIMUM, above, path, INT_ABOVE_MAXIMUM
            yield CoverageScenario.VALUE_BELOW_MINIMUM, below, path, INT_BELOW_MINIMUM
    elif isinstance(nullable, graphql.GraphQLEnumType):
        yield CoverageScenario.INCORRECT_TYPE, incorrect, path, graphql.StringValueNode(value="a")
        invalid = graphql.EnumValueNode(value=unused_name(nullable.values))
        yield CoverageScenario.INVALID_ENUM_VALUE, f"Invalid enum value for `{parameter}`", path, invalid
    else:
        assert isinstance(nullable, graphql.GraphQLInputObjectType)
        unexpected = f"Unexpected field in `{parameter}`"
        yield CoverageScenario.INCORRECT_TYPE, incorrect, path, graphql.IntValueNode(value="1")
        is_one_of = getattr(nullable, "is_one_of", False)
        if is_one_of:
            yield CoverageScenario.OBJECT_MISSING_REQUIRED_PROPERTY, missing_field, path, _object_value([])
        unknown_field = (*path, unused_name(nullable.fields))
        yield CoverageScenario.OBJECT_UNEXPECTED_PROPERTIES, unexpected, unknown_field, graphql.IntValueNode(value="0")
        if is_one_of:
            pair = list(itertools.islice(_one_of_fields(nullable, (nullable,), scalars), 2))
            if len(pair) == 2:
                yield CoverageScenario.OBJECT_UNEXPECTED_PROPERTIES, unexpected, path, _object_value(pair)


def unused_name(declared: Iterable[str]) -> str:
    names = set(declared)
    candidates = itertools.chain(["invalid"], (f"invalid_{index}" for index in itertools.count(1)))
    return next(candidate for candidate in candidates if candidate not in names)


def with_value(
    field: graphql.GraphQLField, path: ValuePath, value: graphql.ValueNode | None, scalars: ScalarLiterals
) -> list[graphql.ArgumentNode] | None:
    """Required arguments with fixed values and `value` at `path`, or `None` when some value cannot be filled.

    An empty `path` gives the default arguments. A `None` value leaves the element at `path` out.
    """
    specs = [(name, argument.type, graphql.is_required_argument(argument)) for name, argument in field.args.items()]
    filled = _fill(specs, path, value, (), scalars)
    if filled is None:
        return None
    return [_argument_node(name, node) for name, node in filled]


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
    # graphql-core 3.2.7+ marks `@oneOf` inputs, which take exactly one non-null field
    if getattr(type_, "is_one_of", False):
        first = next(_one_of_fields(type_, inner_path, scalars), None)
        return None if first is None else _object_value([first])
    fields = _fill(_input_fields(type_), (), None, inner_path, scalars)
    return None if fields is None else _object_value(fields)


def _one_of_fields(
    type_: graphql.GraphQLInputObjectType, path: InputPath, scalars: ScalarLiterals
) -> Iterator[tuple[str, graphql.ValueNode]]:
    """`@oneOf` fields that have a fixed value, in declaration order."""
    for name, input_field in type_.fields.items():
        if not isinstance(input_field.type, graphql.GraphQLList) and input_field.type in path:
            continue
        try:
            value = default_value(input_field.type, path, scalars)
        except UnknownScalar:
            # The field is optional, so another one can be chosen
            continue
        if value is not None:
            yield name, value


def _input_fields(type_: graphql.GraphQLInputObjectType) -> list[FieldSpec]:
    return [(name, field.type, graphql.is_required_input_field(field)) for name, field in type_.fields.items()]


def _fill(
    specs: list[FieldSpec], path: ValuePath, value: graphql.ValueNode | None, types: InputPath, scalars: ScalarLiterals
) -> list[tuple[str, graphql.ValueNode]] | None:
    """Required fields with fixed values plus `value` placed at `path`, in declaration order."""
    target = path[0] if path else None
    fields = []
    for name, type_, required in specs:
        if name == target:
            target = None
            node = _replace(type_, path[1:], value, types, scalars)
            if node is None:
                if len(path) == 1:
                    continue
                return None
        elif required:
            named = graphql.get_nullable_type(type_)
            if not isinstance(named, graphql.GraphQLList) and named in types:
                # A cycle of required non-list fields has no finite value
                return None
            node = default_value(type_, types, scalars)
            if node is None:
                return None
        else:
            continue
        fields.append((name, node))
    if target is not None:
        # A name the input does not declare
        assert len(path) == 1 and value is not None
        fields.append((cast(str, target), value))
    return fields


def _replace(
    type_: graphql.GraphQLInputType,
    path: ValuePath,
    value: graphql.ValueNode | None,
    types: InputPath,
    scalars: ScalarLiterals,
) -> graphql.ValueNode | None:
    """A fixed value of `type_` with `value` at `path`."""
    if not path:
        return value
    nullable = graphql.get_nullable_type(type_)
    if isinstance(nullable, graphql.GraphQLList):
        item = _replace(nullable.of_type, path[1:], value, types, scalars)
        return None if item is None else graphql.ListValueNode(values=(item,))
    assert isinstance(nullable, graphql.GraphQLInputObjectType)
    # `@oneOf` fields are all optional, so the target is the only key
    fields = _fill(_input_fields(nullable), path, value, (*types, nullable), scalars)
    return None if fields is None else _object_value(fields)


def _argument_node(name: str, value: graphql.ValueNode) -> graphql.ArgumentNode:
    return graphql.ArgumentNode(name=graphql.NameNode(value=name), value=value)


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
