from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import TYPE_CHECKING, Any
from urllib.parse import urlencode

import jsonschema
from hypothesis import strategies as st
from hypothesis_jsonschema import from_schema

from schemathesis.config import GenerationConfig
from schemathesis.core.jsonschema import ALL_KEYWORDS
from schemathesis.core.jsonschema.types import JsonSchema
from schemathesis.core.parameters import ParameterLocation

from .mutations import MutationContext, MutationMetadata

if TYPE_CHECKING:
    from .types import Draw, Schema


@dataclass
class GeneratedValue:
    """Wrapper for generated values with optional mutation metadata.

    This allows us to pass both the value and metadata through the generation pipeline
    without using tuples, making the code cleaner and type-safe.
    """

    value: Any
    meta: MutationMetadata | None

    __slots__ = ("value", "meta")


@dataclass
class CacheKey:
    """A cache key for API Operation / location.

    Carries the schema around but don't use it for hashing to simplify LRU cache usage.
    """

    operation_name: str
    location: str
    schema: JsonSchema
    validator_cls: type[jsonschema.Validator]
    custom_format_names: frozenset[str]

    __slots__ = ("operation_name", "location", "schema", "validator_cls", "custom_format_names")

    def __hash__(self) -> int:
        return hash((self.operation_name, self.location, self.custom_format_names))


def _always_invalid(value: Any) -> bool:
    """A format check that always fails."""
    return False


@lru_cache
def _build_format_checker(custom_format_names: frozenset[str]) -> jsonschema.FormatChecker:
    """Build a format checker that handles both standard and custom formats.

    For custom formats not in the standard checker, we add a check that always fails.
    This is because arbitrary strings are almost certainly not valid for custom formats
    (e.g., uuid4, phone numbers, etc.).
    """
    checker = jsonschema.FormatChecker()
    standard = jsonschema.Draft202012Validator.FORMAT_CHECKER

    # Copy all standard checks
    for name in standard.checkers:
        func, raises = standard.checkers[name]
        checker.checkers[name] = (func, raises)

    # For custom formats not in standard checker, add "always invalid" checks
    for name in custom_format_names:
        if name not in checker.checkers:
            checker.checkers[name] = (_always_invalid, ())

    return checker


@lru_cache
def get_validator(cache_key: CacheKey) -> jsonschema.Validator:
    """Get JSON Schema validator for the given schema."""
    # Each operation / location combo has only a single schema, therefore could be cached
    format_checker = _build_format_checker(cache_key.custom_format_names)
    return cache_key.validator_cls(
        cache_key.schema,
        format_checker=format_checker,
    )


@lru_cache
def split_schema(cache_key: CacheKey) -> tuple[Schema, Schema]:
    """Split the schema in two parts.

    The first one contains only validation JSON Schema keywords, the second one everything else.
    """
    keywords, non_keywords = {}, {}
    schema = {} if isinstance(cache_key.schema, bool) else cache_key.schema
    for keyword, value in schema.items():
        if keyword in ALL_KEYWORDS:
            keywords[keyword] = value
        else:
            non_keywords[keyword] = value
    return keywords, non_keywords


def negative_schema(
    schema: JsonSchema,
    operation_name: str,
    location: ParameterLocation,
    media_type: str | None,
    generation_config: GenerationConfig,
    *,
    custom_formats: dict[str, st.SearchStrategy[str]],
    validator_cls: type[jsonschema.Validator],
) -> st.SearchStrategy:
    """A strategy for instances that DO NOT match the input schema.

    It is used to cover the input space that is not possible to cover with the "positive" strategy.

    Returns a strategy that produces GeneratedValue instances with mutation metadata.
    """
    # The mutated schema is passed to `from_schema` and guarded against producing instances valid against
    # the original schema.
    cache_key = CacheKey(operation_name, location, schema, validator_cls, frozenset(custom_formats))
    validator = get_validator(cache_key)
    keywords, non_keywords = split_schema(cache_key)

    if location == ParameterLocation.QUERY:

        def filter_values(value: dict[str, Any]) -> bool:
            return is_non_empty_query(value) and not validator.is_valid(value)

    else:

        def filter_values(value: dict[str, Any]) -> bool:
            return not validator.is_valid(value)

    def generate_value_with_metadata(value: tuple[dict, MutationMetadata]) -> st.SearchStrategy:
        schema, metadata = value
        return (
            from_schema(
                schema,
                custom_formats=custom_formats,
                allow_x00=generation_config.allow_x00,
                codec=generation_config.codec,
            )
            .filter(filter_values)
            .map(lambda value: GeneratedValue(value, metadata))
        )

    return mutated(
        keywords=keywords,
        non_keywords=non_keywords,
        location=location,
        media_type=media_type,
        allow_extra_parameters=generation_config.allow_extra_parameters,
    ).flatmap(generate_value_with_metadata)


def is_non_empty_query(query: dict[str, Any]) -> bool:
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


@st.composite  # type: ignore[untyped-decorator]
def mutated(
    draw: Draw,
    *,
    keywords: Schema,
    non_keywords: Schema,
    location: ParameterLocation,
    media_type: str | None,
    allow_extra_parameters: bool,
) -> Any:
    return MutationContext(
        keywords=keywords,
        non_keywords=non_keywords,
        location=location,
        media_type=media_type,
        allow_extra_parameters=allow_extra_parameters,
    ).mutate(draw)
