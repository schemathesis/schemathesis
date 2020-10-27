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
        enabled_mutations = draw(st.shared(FeatureStrategy(), "enabled_mutations"))

        for name, property_schema in properties:
            if name != mutated_property_name and enabled_properties.is_enabled(name):
                for mutation in MUTATIONS:
                    if enabled_mutations.is_enabled(mutation.__name__):
                        mutation(draw, new_schema, property_schema, name)

        return new_schema

    return inner().flatmap(from_schema)


def mutate_required(draw: Draw, new_schema: Schema, property_schema: Schema, name: str) -> None:
    required = new_schema.get("required")
    if required and name in required:
        required.remove(name)
    # An optional property still can be generated
    # To avoid it we need to remove it completely
    properties = new_schema["properties"]
    del properties[name]


def mutate_type(draw: Draw, new_schema: Schema, property_schema: Schema, name: str) -> None:
    property_type = property_schema["type"]
    to_exclude = set(property_type) if isinstance(property_type, list) else {property_type}
    available_types = {"string", "integer", "number", "object", "array", "boolean", "null"} - to_exclude
    if available_types:
        property_schema["type"] = draw(st.sampled_from(sorted(available_types)))


MUTATIONS = [mutate_required, mutate_type]
