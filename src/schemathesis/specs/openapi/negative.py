from copy import deepcopy
from typing import Any, Callable, Dict, TypeVar

from hypothesis import strategies as st
from hypothesis.strategies._internal.featureflags import FeatureStrategy
from hypothesis_jsonschema import from_schema

T = TypeVar("T")
Draw = Callable[[st.SearchStrategy[T]], T]
Schema = Dict[str, Any]
# draw, new_schema, property schema, name
Mutation = Callable[[Draw, Schema, Schema, str], None]


def negative_schema(schema: Dict[str, Any]) -> st.SearchStrategy:
    """A strategy for objects, that DO NOT match the input schema."""

    # First we mutate the input schema, so any valid instance for it is invalid to the original schema

    @st.composite  # type: ignore
    def inner(draw: Draw) -> Any:
        new_schema = deepcopy(schema)
        properties = sorted(new_schema.get("properties", {}).items())
        # At least one mutation should be done to at least one property
        mutated_property_name, mutated_property_schema = draw(st.sampled_from(properties))
        mutation = draw(st.sampled_from(MUTATIONS))
        mutation(draw, new_schema, mutated_property_schema, mutated_property_name)

        # Other properties & mutations are chosen with feature flags
        enabled_properties = draw(FeatureStrategy())
        enabled_mutations = draw(FeatureStrategy())

        for name, property_schema in properties:
            if name != mutated_property_name and enabled_properties.is_enabled(name):
                for mutation in MUTATIONS:
                    if enabled_mutations.is_enabled(mutation.__name__):
                        mutation(draw, new_schema, property_schema, name)

        return new_schema

    return inner().flatmap(from_schema)


def mutate_required(draw: Draw, new_schema: Schema, property_schema: Schema, name: str) -> None:
    required = new_schema.get("required")
    if required:
        required.remove(name)
    # An optional property still can be generated
    # To avoid it we need to remove it completely
    properties = new_schema["properties"]
    del properties[name]


def mutate_type(draw: Draw, new_schema: Schema, property_schema: Schema, name: str) -> None:
    to_exclude = {property_schema["type"]}
    if property_schema["type"] == "integer":
        # Any valid integer is also a valid number
        # If we won't change it then it is possible that the result will still match the original schema
        # For example 0.0 is still a valid integer in JSON Schema Draft 7 (TODO. check)
        to_exclude.add("number")
    available_types = {"null", "string", "integer", "number", "array", "object"} - to_exclude
    property_schema["type"] = draw(st.sampled_from(sorted(available_types)))


MUTATIONS = [mutate_required, mutate_type]
