from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from functools import lru_cache
from typing import TYPE_CHECKING, Any
from urllib.parse import urlencode

import jsonschema_rs
from hypothesis import strategies as st
from hypothesis.errors import InvalidArgument
from hypothesis_jsonschema import from_schema

from schemathesis.config import GenerationConfig
from schemathesis.core.jsonschema import ALL_KEYWORDS, DRAFT4_SUPPLEMENTAL_FORMATS, FANCY_REGEX_OPTIONS
from schemathesis.core.jsonschema.types import JsonSchema, JsonSchemaObject
from schemathesis.core.media_types import is_json
from schemathesis.core.mutations import OperatorKind
from schemathesis.core.parameters import ParameterLocation
from schemathesis.transport.serialization import Binary, contains_binary

from .mutations import (
    Mutation,
    MutationChannel,
    MutationContext,
    MutationMetadata,
    MutationTargetDescriptor,
    compute_mutation_targets,
    metadata_with_description_override,
)
from .value_channel import apply_value_channel, collect_value_targets

SYNTAX_FUZZING_PROBABILITY = 0.05
VALUE_CHANNEL_PROBABILITY = 0.15

if TYPE_CHECKING:
    from schemathesis.resources import PoolDraw

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


@dataclass(slots=True)
class GeneratedValue:
    """Wrapper for generated values with optional mutation metadata.

    This allows us to pass both the value and metadata through the generation pipeline
    without using tuples, making the code cleaner and type-safe.
    """

    value: Any
    meta: MutationMetadata | None
    pool_draws: tuple[PoolDraw, ...] = ()


def wrap_filter_hook_for_generated_value(hook: Callable) -> Callable:
    """Adapter so user-supplied filter hooks see plain values when negative-mode wraps them.

    The boolean result is returned directly so `strategy.filter()` can evaluate truthiness;
    re-wrapping in `GeneratedValue` would make every result truthy and break filtering.
    """

    def wrapper(value: Any) -> bool:
        if isinstance(value, GeneratedValue):
            return hook(value.value)
        return hook(value)

    return wrapper


def wrap_map_hook_for_generated_value(hook: Callable) -> Callable:
    """Adapter so user-supplied map hooks see plain values when negative-mode wraps them."""

    def wrapper(value: Any) -> Any:
        if isinstance(value, GeneratedValue):
            result = hook(value.value)
            return GeneratedValue(value=result, meta=value.meta, pool_draws=value.pool_draws)
        return hook(value)

    return wrapper


def wrap_flatmap_hook_for_generated_value(hook: Callable) -> Callable:
    """Adapter so user-supplied flatmap hooks see plain values when negative-mode wraps them.

    Unlike map hooks, flatmap hooks return a `SearchStrategy` — not a value. We unwrap
    `GeneratedValue` before invoking the hook, then re-wrap each drawn result so the
    `GeneratedValue` metadata is preserved through the flatmap.
    """

    def wrapper(value: Any) -> st.SearchStrategy:
        if isinstance(value, GeneratedValue):
            meta = value.meta
            pool_draws = value.pool_draws
            return hook(value.value).map(lambda v: GeneratedValue(value=v, meta=meta, pool_draws=pool_draws))
        return hook(value)

    return wrapper


@dataclass(slots=True)
class CacheKey:
    """A cache key for API Operation / location.

    Carries the schema around but don't use it for hashing to simplify LRU cache usage.
    """

    operation_name: str
    location: str
    schema: JsonSchema
    validator_cls: type[jsonschema_rs.Validator]
    custom_format_names: frozenset[str]

    def __hash__(self) -> int:
        return hash((self.operation_name, self.location, self.custom_format_names))


def _always_invalid(value: object) -> bool:
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


@lru_cache
def get_validator(cache_key: CacheKey) -> jsonschema_rs.Validator:
    """Hook custom formats to always-fail (enables format-violating fuzzing); skip `binary`/`byte` (runtime is permissive)."""
    formats: dict[str, Any] = {}
    if cache_key.validator_cls is jsonschema_rs.Draft4Validator:
        formats.update(DRAFT4_SUPPLEMENTAL_FORMATS)
    formats.update(dict.fromkeys(cache_key.custom_format_names - _ALWAYS_INVALID_FORMATS, _always_invalid))
    return cache_key.validator_cls(
        cache_key.schema,
        formats=formats,
        validate_formats=True,
        pattern_options=FANCY_REGEX_OPTIONS,
    )


@lru_cache
def get_real_validator(cache_key: CacheKey) -> jsonschema_rs.Validator:
    """A validator without the always-invalid format hook.

    The schema-channel `filter_values` validator artificially fails any custom
    format so format-violating mutations are recognized as invalid. The value
    channel applies known violators (e.g. `violate_email`) and only needs to
    confirm the resulting body is genuinely invalid against the original schema —
    so it must not treat a still-valid sibling format as a violation.
    """
    formats: dict[str, Any] = {}
    if cache_key.validator_cls is jsonschema_rs.Draft4Validator:
        formats.update(DRAFT4_SUPPLEMENTAL_FORMATS)
    return cache_key.validator_cls(
        cache_key.schema,
        formats=formats,
        validate_formats=True,
        pattern_options=FANCY_REGEX_OPTIONS,
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


def _strip_binary(value: Any) -> Any:
    # Replace Binary with "" so jsonschema_rs can validate structure; format:binary is annotation-only,
    # so "" passes the format check while required/additionalProperties/sibling constraints still fire.
    if isinstance(value, Binary):
        return ""
    if isinstance(value, dict):
        return {k: _strip_binary(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_strip_binary(v) for v in value]
    return value


def negative_schema(
    schema: JsonSchema,
    operation_name: str,
    location: ParameterLocation,
    media_type: str | None,
    generation_config: GenerationConfig,
    *,
    custom_formats: dict[str, st.SearchStrategy[str]],
    validator_cls: type[jsonschema_rs.Validator],
    validation_schema: JsonSchema | None = None,
    name_to_uri: dict[str, str] | None = None,
    target_descriptors: tuple[MutationTargetDescriptor, ...] | None = None,
) -> st.SearchStrategy:
    """A strategy for instances that DO NOT match the input schema.

    It is used to cover the input space that is not possible to cover with the "positive" strategy.

    Returns a strategy that produces GeneratedValue instances with mutation metadata.

    `validation_schema`, when provided, keeps `prefixItems` intact and is used only to build the
    runtime validator; falls back to `schema`.
    """
    # The mutated schema is passed to `from_schema` and guarded against producing instances valid against
    # the original schema.
    cache_key = CacheKey(operation_name, location, schema, validator_cls, frozenset(custom_formats))
    # Build the validator from the form with `prefixItems` intact so meta-validation accepts it.
    validator_cache_key = (
        cache_key
        if validation_schema is None or validation_schema is schema
        else CacheKey(operation_name, location, validation_schema, validator_cls, frozenset(custom_formats))
    )
    validator = get_validator(validator_cache_key)
    keywords, non_keywords = split_schema(cache_key)

    # For unconstrained binary/byte schemas, skip the validation filter entirely.
    # Such schemas accept any value (no type constraint + format only applies to strings),
    # so we can't meaningfully filter generated values.
    skip_validation_filter = _is_unconstrained_binary_schema(schema)

    if location == ParameterLocation.QUERY:

        def filter_values(value: Any) -> bool:
            return is_non_empty_query(value) and (
                skip_validation_filter or contains_binary(value) or not validator.is_valid(value)
            )

    else:

        def filter_values(value: Any) -> bool:
            return skip_validation_filter or contains_binary(value) or not validator.is_valid(value)

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

    if target_descriptors is None:
        target_descriptors = compute_mutation_targets(schema)

    mutated_strategy = mutated(
        keywords=keywords,
        non_keywords=non_keywords,
        location=location,
        media_type=media_type,
        allow_extra_parameters=generation_config.allow_extra_parameters,
        name_to_uri=name_to_uri,
        target_descriptors=target_descriptors,
    ).flatmap(generate_value_with_metadata)

    positive_strategy: st.SearchStrategy | None = None
    if location == ParameterLocation.BODY:
        _candidate = from_schema(
            schema,
            custom_formats=custom_formats,
            allow_x00=generation_config.allow_x00,
            codec=generation_config.codec,
        )
        try:
            _candidate.validate()
        except InvalidArgument:
            pass
        else:
            if not _candidate.is_empty:
                positive_strategy = _candidate
    if positive_strategy is not None:
        body_schema: JsonSchemaObject = schema if isinstance(schema, dict) else {}
        inner_mutated_strategy = mutated_strategy
        # Use the real-format validator here: `filter_values` artificially fails
        # custom formats, so a permissive sibling target (e.g. `minLength: 0`)
        # alongside a format-bearing field would let an unchanged-but-valid body
        # slip through as negative data.
        real_validator = get_real_validator(validator_cache_key)

        @st.composite  # type: ignore[untyped-decorator]
        def hybrid(draw: Any) -> GeneratedValue:
            random = draw(st.randoms())
            if random.random() < VALUE_CHANNEL_PROBABILITY:
                positive = draw(positive_strategy)
                targets = collect_value_targets(positive, body_schema)
                if not targets:
                    return draw(inner_mutated_strategy)
                target_path, schema_pointer, _value, keyword, schema_at_path = draw(st.sampled_from(targets))
                new_body, original_value, new_value = apply_value_channel(
                    positive, target_path, keyword, schema_at_path
                )
                # Violators are no-ops on permissive schemas; fall back to schema-channel to avoid
                # false-positive `negative_data_rejection`. Strip Binary to "" before validating —
                # jsonschema_rs rejects the wrapper but structure-level keywords still fire.
                body_for_validation = _strip_binary(new_body) if contains_binary(new_body) else new_body
                if real_validator.is_valid(body_for_validation):
                    return draw(inner_mutated_strategy)
                mutation = Mutation(
                    path=target_path,
                    schema_pointer=schema_pointer,
                    channel=MutationChannel.VALUE,
                    operator=OperatorKind.VALUE_VIOLATOR,
                    keywords=(keyword,),
                    parameter=str(target_path[-1]) if target_path else None,
                    original_value=original_value,
                    new_value=new_value,
                )
                return GeneratedValue(new_body, MutationMetadata(mutations=(mutation,)))
            return draw(inner_mutated_strategy)

        mutated_strategy = hybrid()

    # For JSON bodies, add syntax-level fuzzing with random bytes (~5% of cases)
    if location == ParameterLocation.BODY and media_type is not None and is_json(media_type):
        syntax_fuzzing_strategy = _random_non_json_bytes().map(
            lambda b: GeneratedValue(
                b,
                metadata_with_description_override(
                    operator=OperatorKind.SYNTAX_FUZZING,
                    parameter=None,
                    description="Invalid syntax: random bytes",
                    location=None,
                ),
            )
        )

        @st.composite  # type: ignore[untyped-decorator]
        def with_syntax_fuzzing(draw: Any) -> GeneratedValue:
            random = draw(st.randoms())
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
    target_descriptors: tuple[MutationTargetDescriptor, ...],
) -> Any:
    return MutationContext(
        keywords=keywords,
        non_keywords=non_keywords,
        location=location,
        media_type=media_type,
        allow_extra_parameters=allow_extra_parameters,
        name_to_uri=name_to_uri,
        target_descriptors=target_descriptors,
    ).mutate(draw)
