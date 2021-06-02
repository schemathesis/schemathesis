from typing import Any, Dict, Optional

import jsonschema
from hypothesis import strategies as st
from hypothesis_jsonschema import from_schema

from .mutations import MutationContext
from .types import Draw, Schema

_VALIDATORS_CACHE = {}


def get_validator(schema: Schema, operation_name: str, location: str) -> jsonschema.Draft4Validator:
    """Get JSON Schema validator for the given schema."""
    # Each operation / location combo has only a single schema, therefore could be cached
    key = (operation_name, location)
    if key not in _VALIDATORS_CACHE:
        _VALIDATORS_CACHE[key] = jsonschema.Draft4Validator(schema)
    return _VALIDATORS_CACHE[key]


def negative_schema(
    schema: Schema,
    operation_name: str,
    location: str,
    media_type: Optional[str],
    *,
    custom_formats: Dict[str, st.SearchStrategy[str]],
) -> st.SearchStrategy:
    """A strategy for instances that DO NOT match the input schema.

    It is used to cover the input space that is not possible to cover with the "positive" strategy.
    """
    # The mutated schema is passed to `from_schema` and guarded against producing instances valid against
    # the original schema.
    validator = get_validator(schema, operation_name, location)
    return mutated(schema, location, media_type).flatmap(
        lambda s: from_schema(s, custom_formats=custom_formats).filter(lambda v: not validator.is_valid(v))
    )


@st.composite  # type: ignore
def mutated(draw: Draw, schema: Schema, location: str, media_type: Optional[str]) -> Any:
    return MutationContext(schema=schema, location=location, media_type=media_type).mutate(draw)
