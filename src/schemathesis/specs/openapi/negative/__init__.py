from copy import deepcopy
from typing import Any, Dict, Tuple

from hypothesis import strategies as st
from hypothesis_jsonschema import from_schema

from .mutations import (
    Mutation,
    Mutator,
    change_properties,
    change_type,
    get_mutations,
    negate_constraints,
    ordered,
    remove_required_property,
)
from .types import Draw, Schema


def negative_schema(
    schema: Schema, location: str, *, custom_formats: Dict[str, st.SearchStrategy[str]]
) -> st.SearchStrategy:
    """A strategy for instances that DO NOT match the input schema.

    It is used to cover the input space that is not possible to cover with the "positive" strategy.
    """
    # The mutated schema is passed to `from_schema` and guarded against producing instances valid against
    # the original schema. The `allOf` schema implies that all constructed instances will:
    #  - Match the mutated schema
    #  - Not match the original one
    return mutated(schema, location).flatmap(
        lambda s: from_schema({"allOf": [s, {"not": schema}]}, custom_formats=custom_formats)
    )


@st.composite  # type: ignore
def mutated(draw: Draw, schema: Schema, location: str) -> Any:
    # On the top level, Schemathesis creates "object" schemas for all parameter "in" values except "body", which is
    # taken as-is. Therefore we can only apply mutations that won't change the Open API semantics of the schema.
    mutations: Tuple[Mutation, ...]
    if location in ("header", "cookie", "query"):
        # These objects follow this pattern:
        # {
        #     "properties": properties,
        #     "additionalProperties": False,
        #     "type": "object",
        #     "required": required
        # }
        # Open API semantics expect mapping; therefore, they should have the "object" type.
        # We can:
        #   - remove required parameters
        #   - negate constraints (only `additionalProperties` in this case)
        #   - mutate individual properties
        mutations = draw(ordered((remove_required_property, negate_constraints, change_properties)))
    elif location == "path":
        # The same as above, but we can only mutate individual properties as their names are predefined in the
        # path template, and all of them are required.
        mutations = (change_properties,)
    else:
        # Body can be of any type and does not have any specific type semantic.
        mutations = get_mutations(draw, schema)

    new_schema = deepcopy(schema)
    # TODO. apply swarm testing? If nothing succeeds, then call `reject()`
    mutator = Mutator()
    for mutation in mutations:
        if mutator.can_apply(mutation):
            mutator.apply(mutation, draw, new_schema, location)
    return new_schema
