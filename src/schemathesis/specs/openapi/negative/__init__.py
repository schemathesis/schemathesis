from functools import lru_cache
from typing import Any, Dict, Optional, Tuple
from urllib.parse import urlencode

import attr
import jsonschema
from hypothesis import strategies as st
from hypothesis_jsonschema import from_schema

from .mutations import MutationContext
from .types import Draw, Schema


@attr.s(slots=True, hash=False)
class CacheKey:
    """A cache key for API Operation / location.

    Carries the schema around but don't use it for hashing to simplify LRU cache usage.
    """

    operation_name: str = attr.ib()
    location: str = attr.ib()
    schema: Schema = attr.ib()

    def __hash__(self) -> int:
        return hash((self.operation_name, self.location))


@lru_cache()
def get_validator(cache_key: CacheKey) -> jsonschema.Draft4Validator:
    """Get JSON Schema validator for the given schema."""
    # Each operation / location combo has only a single schema, therefore could be cached
    return jsonschema.Draft4Validator(cache_key.schema)


ALL_KEYWORDS = {
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
}


@lru_cache()
def split_schema(cache_key: CacheKey) -> Tuple[Schema, Schema]:
    """Split the schema in two parts.

    The first one contains only validation JSON Schema keywords, the second one everything else.
    """
    keywords, non_keywords = {}, {}
    for keyword, value in cache_key.schema.items():
        if keyword in ALL_KEYWORDS:
            keywords[keyword] = value
        else:
            non_keywords[keyword] = value
    return keywords, non_keywords


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
    cache_key = CacheKey(operation_name, location, schema)
    validator = get_validator(cache_key)
    keywords, non_keywords = split_schema(cache_key)

    if location == "query":

        def filter_values(value: Dict[str, Any]) -> bool:
            return is_non_empty_query(value) and not validator.is_valid(value)

    else:

        def filter_values(value: Dict[str, Any]) -> bool:
            return not validator.is_valid(value)

    return mutated(keywords, non_keywords, location, media_type).flatmap(
        lambda s: from_schema(s, custom_formats=custom_formats).filter(filter_values)
    )


def is_non_empty_query(query: Dict[str, Any]) -> bool:
    # Whether this query parameters will be encoded to a non-empty query string
    result = []
    for key, values in query.items():
        if isinstance(values, str) or not hasattr(values, "__iter__"):
            values = [values]
        for value in values:
            if value is not None:
                result.append(
                    (
                        key.encode("utf-8") if isinstance(key, str) else key,
                        value.encode("utf-8") if isinstance(value, str) else value,
                    )
                )
    return urlencode(result, doseq=True) != ""


@st.composite  # type: ignore
def mutated(draw: Draw, keywords: Schema, non_keywords: Schema, location: str, media_type: Optional[str]) -> Any:
    return MutationContext(
        keywords=keywords, non_keywords=non_keywords, location=location, media_type=media_type
    ).mutate(draw)
