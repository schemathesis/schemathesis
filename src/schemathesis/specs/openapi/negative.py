from copy import deepcopy
from typing import Any, Callable, Dict, List, Set, Tuple, TypeVar

import attr
from hypothesis import strategies as st
from hypothesis.strategies._internal.featureflags import FeatureStrategy
from hypothesis_jsonschema import from_schema

T = TypeVar("T")
Draw = Callable[[st.SearchStrategy[T]], T]
Schema = Dict[str, Any]


@attr.s(slots=True)
class Mutation:
    def mutate(self, draw: Draw, new_schema: Schema, property_schema: Schema, name: str) -> None:
        raise NotImplementedError


@attr.s(slots=True)
class MutateRequired(Mutation):
    def mutate(self, draw: Draw, new_schema: Schema, property_schema: Schema, name: str) -> None:
        """Make the schema never generate the given property."""
        required = new_schema.get("required")
        if required and name in required:
            required.remove(name)
        # An optional property still can be generated
        # To avoid it we need to remove it completely
        properties = new_schema["properties"]
        del properties[name]


@attr.s(slots=True)
class MutateType(Mutation):
    possible_types: Set[str] = attr.ib(
        factory=lambda: {"string", "integer", "number", "object", "array", "boolean", "null"}
    )

    def mutate(self, draw: Draw, new_schema: Schema, property_schema: Schema, name: str) -> None:
        """Change the property type if possible."""
        to_exclude = set(get_type(property_schema))
        available_types = self.possible_types - to_exclude
        if available_types:
            property_schema["type"] = draw(st.sampled_from(sorted(available_types)))


MUTATIONS = {
    "headers": (MutateRequired(), MutateType()),
    "cookies": (MutateRequired(), MutateType()),
    # We can't mutate `required` since it is required by the spec to be valid.
    # I.e. if any of path parameters is absent, then we can't reach the endpoint
    "path_parameters": (MutateType(),),
    "query": (MutateRequired(), MutateType()),
    "body": (MutateRequired(), MutateType()),
    "form_data": (MutateRequired(), MutateType()),
}


# Thoughts:
#   - Consider a request mutated when at least one parameter is mutated. I.e. one from "header", "query", etc.
#     Some parameters can't be mutated, but it should matter only when nothing at all can't be mutated.


def negative_schema(
    schema: Dict[str, Any], parameter: str, *, custom_formats: Dict[str, st.SearchStrategy[str]]
) -> st.SearchStrategy:
    """A strategy for instances, that DO NOT match the input schema."""
    mutations = MUTATIONS[parameter]

    # First we mutate the input schema, so any valid instance for it is invalid to the original schema

    @st.composite  # type: ignore
    def mutated(draw: Draw) -> Any:
        new_schema = deepcopy(schema)
        # Schemathesis creates "object" schemas for all parameter "in" values except "body" (which is taken as is).
        # In OAS 2 the "type" keyword is a list (in OAS 3 it is a string) therefore we need to convert "type" to a list
        if "object" in get_type(new_schema):
            return mutate_object_schema(draw, new_schema, mutations)
        return {"not": new_schema}

    return mutated().flatmap(lambda s: from_schema({"allOf": [{"not": schema}, s]}, custom_formats=custom_formats))


def get_type(schema: Schema) -> List[str]:
    type_ = schema.get("type", ["null", "boolean", "integer", "number", "string", "array", "object"])
    if isinstance(type_, str):
        return [type_]
    return type_


def mutate_object_schema(draw: Draw, schema: Schema, mutations: Tuple[Mutation, ...]) -> Schema:
    properties = sorted(schema.get("properties", {}).items())
    if not properties:
        return {"not": schema}
    # At least one mutation should be done to at least one property
    mutated_property_name, mutated_property_schema = draw(st.sampled_from(properties))
    mutation = draw(st.sampled_from(mutations))
    mutation.mutate(draw, schema, mutated_property_schema, mutated_property_name)

    # Other properties & mutations are chosen with feature flags
    features = draw(st.shared(FeatureStrategy(), key="features"))

    for name, property_schema in properties:
        if name != mutated_property_name and features.is_enabled(name):
            for mutation in mutations:
                if features.is_enabled(mutation.__class__.__name__):
                    mutation.mutate(draw, schema, property_schema, name)

    return schema
