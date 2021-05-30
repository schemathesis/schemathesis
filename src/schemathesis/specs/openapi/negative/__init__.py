from copy import deepcopy
from typing import Any, Dict, Tuple

import jsonschema
from hypothesis import strategies as st
from hypothesis_jsonschema import from_schema

from ..utils import is_header_location, set_keyword_on_properties
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

ALL_KEYWORDS = (
    "additionalItems",
    "additionalProperties",
    "allOf",
    "anyOf",
    "const",
    "contains",
    "contentEncoding",
    "contentMediaType",
    "dependencies",
    "enum",
    "else",
    "exclusiveMaximum",
    "exclusiveMinimum",
    "format",
    "if",
    "items",
    "maxItems",
    "maxLength",
    "maxProperties",
    "maximum",
    "minItems",
    "minLength",
    "minProperties",
    "minimum",
    "multipleOf",
    "not",
    "oneOf",
    "pattern",
    "patternProperties",
    "properties",
    "propertyNames",
    "$ref",
    "required",
    "then",
    "type",
    "uniqueItems",
)

_VALIDATORS_CACHE = {}


def get_validator(schema: Schema, operation_name: str, location: str) -> jsonschema.Draft7Validator:
    """Get JSON Schema validator for the given schema."""
    # Each operation / location combo has only a single schema, therefore could be cached
    key = (operation_name, location)
    if key not in _VALIDATORS_CACHE:
        _VALIDATORS_CACHE[key] = jsonschema.Draft7Validator(schema)
    return _VALIDATORS_CACHE[key]


def split_schema(schema: Schema) -> Tuple[Schema, Schema]:
    """Split the schema into two parts.

    The first one contains only validation JSON Schema keywords, the second one everything else.
    """
    keywords, non_keywords = {}, {}
    for keyword, value in schema.items():
        if keyword in ALL_KEYWORDS:
            keywords[keyword] = value
        else:
            non_keywords[keyword] = value
    return keywords, non_keywords


def negative_schema(
    schema: Schema, operation_name: str, location: str, *, custom_formats: Dict[str, st.SearchStrategy[str]]
) -> st.SearchStrategy:
    """A strategy for instances that DO NOT match the input schema.

    It is used to cover the input space that is not possible to cover with the "positive" strategy.
    """
    # The mutated schema is passed to `from_schema` and guarded against producing instances valid against
    # the original schema.
    validator = get_validator(schema, operation_name, location)
    return mutated(schema, location).flatmap(
        lambda s: from_schema(s, custom_formats=custom_formats).filter(lambda v: not validator.is_valid(v))
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
    keywords, non_keywords = split_schema(schema)
    # Deep copy all keywords to avoid modifying the original schema
    new_schema = deepcopy(keywords)
    # TODO. apply swarm testing? If nothing succeeds, then call `reject()`
    mutator = Mutator()
    for mutation in mutations:
        if mutator.can_apply(mutation):
            mutator.apply(mutation, draw, new_schema, location)
    new_schema.update(non_keywords)
    if is_header_location(location):
        new_schema["propertyNames"] = {"format": "_header_name"}
        set_keyword_on_properties(new_schema, type="string", format="_header_value")
        # TODO. this one should be randomly applied
        new_schema["additionalProperties"] = {
            "propertyNames": {"format": "_header_name"},
            "type": "string",
            "format": "_header_value",
        }
    return new_schema
