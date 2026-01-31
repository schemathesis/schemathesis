from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from typing import TYPE_CHECKING, Any
from urllib.parse import urlencode

import jsonschema_rs
from hypothesis import strategies as st
from hypothesis_jsonschema import from_schema

from schemathesis.config import GenerationConfig
from schemathesis.core.jsonschema import ALL_KEYWORDS
from schemathesis.core.jsonschema.types import JsonSchema
from schemathesis.core.media_types import is_json
from schemathesis.core.parameters import ParameterLocation
from schemathesis.transport.serialization import Binary

from .mutations import MutationContext, MutationMetadata

SYNTAX_FUZZING_PROBABILITY = 0.05
# Use FancyRegexOptions to support lookahead/lookbehind assertions common in ECMA-262 patterns,
# with a large size limit to handle schemas with large quantifiers (e.g., {1,51200})
_PATTERN_OPTIONS = jsonschema_rs.FancyRegexOptions(size_limit=1_000_000_000)

if TYPE_CHECKING:
    from .types import Draw, Schema


def _is_not_valid_json(data: bytes) -> bool:
    """Check if bytes are NOT valid JSON."""
    try:
        json.loads(data)
        return False  # Valid JSON, reject
    except (json.JSONDecodeError, UnicodeDecodeError):
        return True  # Invalid, keep


def _random_non_json_bytes() -> st.SearchStrategy[bytes]:
    """Generate random bytes that are NOT valid JSON.

    Used for syntax-level fuzzing of JSON endpoints.
    """
    return st.binary(min_size=1, max_size=1024).filter(_is_not_valid_json)


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
    validator_cls: type[jsonschema_rs.Validator]
    custom_format_names: frozenset[str]

    __slots__ = ("operation_name", "location", "schema", "validator_cls", "custom_format_names")

    def __hash__(self) -> int:
        return hash((self.operation_name, self.location, self.custom_format_names))


def _always_invalid(value: Any) -> bool:
    """A format check that always fails."""
    return False


# Formats that should always be treated as invalid for negative testing.
# These are OpenAPI-specific formats that jsonschema-rs doesn't validate,
# so without this, any value would pass validation and get filtered out.
_ALWAYS_INVALID_FORMATS = frozenset({"binary", "byte"})


def _is_unconstrained_binary_schema(schema: JsonSchema) -> bool:
    """Check if schema is an unconstrained binary/byte format that accepts any value.

    A schema like {"format": "binary"} without a type constraint accepts any JSON value,
    since JSON Schema format validation only applies to strings. For such schemas, we can't
    meaningfully filter generated values because everything matches.
    """
    if not isinstance(schema, dict):
        return False
    # Has binary/byte format but no type constraint
    return schema.get("format") in _ALWAYS_INVALID_FORMATS and "type" not in schema


def _contains_binary(value: Any) -> bool:
    """Check if the value contains any Binary instances.

    Binary is a special wrapper type that jsonschema-rs cannot validate.
    """
    if isinstance(value, Binary):
        return True
    if isinstance(value, dict):
        return any(_contains_binary(v) for v in value.values())
    if isinstance(value, list):
        return any(_contains_binary(v) for v in value)
    return False


@lru_cache
def get_validator(cache_key: CacheKey) -> jsonschema_rs.Validator:
    """Get JSON Schema validator for the given schema."""
    return cache_key.validator_cls(
        cache_key.schema,
        formats=dict.fromkeys(cache_key.custom_format_names | _ALWAYS_INVALID_FORMATS, _always_invalid),
        validate_formats=True,
        pattern_options=_PATTERN_OPTIONS,
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
    validator_cls: type[jsonschema_rs.Validator],
    name_to_uri: dict[str, str] | None = None,
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

    # For unconstrained binary/byte schemas, skip the validation filter entirely.
    # Such schemas accept any value (no type constraint + format only applies to strings),
    # so we can't meaningfully filter generated values.
    skip_validation_filter = _is_unconstrained_binary_schema(schema)

    if location == ParameterLocation.QUERY:

        def filter_values(value: dict[str, Any]) -> bool:
            return is_non_empty_query(value) and (
                skip_validation_filter or _contains_binary(value) or not validator.is_valid(value)
            )

    else:

        def filter_values(value: dict[str, Any]) -> bool:
            return skip_validation_filter or _contains_binary(value) or not validator.is_valid(value)

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

    mutated_strategy = mutated(
        keywords=keywords,
        non_keywords=non_keywords,
        location=location,
        media_type=media_type,
        allow_extra_parameters=generation_config.allow_extra_parameters,
        name_to_uri=name_to_uri,
    ).flatmap(generate_value_with_metadata)

    # For JSON bodies, add syntax-level fuzzing with random bytes (~5% of cases)
    if location == ParameterLocation.BODY and media_type is not None and is_json(media_type):
        syntax_fuzzing_strategy = _random_non_json_bytes().map(
            lambda b: GeneratedValue(b, MutationMetadata(None, "Invalid syntax: random bytes", None))
        )

        @st.composite  # type: ignore[untyped-decorator]
        def with_syntax_fuzzing(draw: Any) -> GeneratedValue:
            random = draw(st.randoms(use_true_random=True))
            if random.random() < SYNTAX_FUZZING_PROBABILITY:
                return draw(syntax_fuzzing_strategy)
            return draw(mutated_strategy)

        return with_syntax_fuzzing()

    return mutated_strategy


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
    name_to_uri: dict[str, str] | None = None,
) -> Any:
    return MutationContext(
        keywords=keywords,
        non_keywords=non_keywords,
        location=location,
        media_type=media_type,
        allow_extra_parameters=allow_extra_parameters,
        name_to_uri=name_to_uri,
    ).mutate(draw)
