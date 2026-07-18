from __future__ import annotations

import re
from abc import ABC, abstractmethod
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from itertools import chain
from random import Random
from typing import TYPE_CHECKING, Any, cast
from urllib.parse import unquote

import jsonschema_rs

from schemathesis.config import GenerationConfig
from schemathesis.core import NOT_SET, NotSet
from schemathesis.core.adapter import OperationParameter
from schemathesis.core.errors import InvalidSchema
from schemathesis.core.jsonschema import (
    FANCY_REGEX_OPTIONS,
    VALIDATED_FORMATS_BY_DRAFT,
    BundleError,
    Bundler,
    make_validator,
    maybe_resolve_bundled,
    schema_with_bundle,
)
from schemathesis.core.jsonschema.bundler import BUNDLE_STORAGE_KEY, BundleCache
from schemathesis.core.jsonschema.resolver import Resolver
from schemathesis.core.jsonschema.types import JsonSchema, JsonSchemaObject, JsonValue, get_type
from schemathesis.core.media_types import FORM_MEDIA_TYPES
from schemathesis.core.parameters import HEADER_LOCATIONS, ParameterLocation
from schemathesis.core.transforms import deepclone
from schemathesis.core.validation import check_header_name
from schemathesis.generation.modes import GenerationMode
from schemathesis.generation.value import GeneratedValue
from schemathesis.python._constants.pool import ConstantDraw, ConstantsPool, ConstantType, ConstantValue, Origin
from schemathesis.resources import ExtraDataSource, SemanticDraw
from schemathesis.schemas import APIOperation, ParameterSet
from schemathesis.specs.openapi.adapter.protocol import SpecificationAdapter
from schemathesis.specs.openapi.adapter.references import maybe_resolve_with_resolver
from schemathesis.specs.openapi.converter import to_json_schema
from schemathesis.specs.openapi.formats import HEADER_FORMAT, STRING_FORMATS
from schemathesis.specs.openapi.headers import KNOWN_HEADER_FORMATS
from schemathesis.transport.serialization import Binary, quote_all

if TYPE_CHECKING:
    from hypothesis import strategies as st

    from schemathesis.core.error_feedback import ErrorFeedbackStore
    from schemathesis.specs.openapi.extra_data_source import CapturedVariant, VariantUsageTracker
    from schemathesis.specs.openapi.negative.mutations import MutationTargetDescriptor
    from schemathesis.specs.openapi.schemas import OpenApiOperation
    from schemathesis.specs.openapi.semantic_pool import LeafDescriptor, SemanticValueIndex


MISSING_SCHEMA_OR_CONTENT_MESSAGE = (
    "Can not generate data for {location} parameter `{name}`! "
    "It should have either `schema` or `content` keywords defined"
)

INVALID_SCHEMA_MESSAGE = (
    "Can not generate data for {location} parameter `{name}`! Its schema should be an object or boolean, got {schema}"
)

# `parameter["in"]` value -> `ParameterLocation`. `querystring` is a known alias for
# `query` that some specs use; everything else falls back to UNKNOWN at the call site.
_IN_TO_LOCATION: dict[str | None, ParameterLocation] = {
    "query": ParameterLocation.QUERY,
    "querystring": ParameterLocation.QUERY,
    "header": ParameterLocation.HEADER,
    "path": ParameterLocation.PATH,
    "cookie": ParameterLocation.COOKIE,
    "body": ParameterLocation.BODY,
    None: ParameterLocation.UNKNOWN,
}

# Reused for the common case where no parameters are excluded — avoids
# allocating a fresh empty frozenset on every cache lookup.
_EMPTY_EXCLUDE_KEY: frozenset[str] = frozenset()

# Probability of using captured resource values vs generated values in hybrid strategy.
CAPTURED_VALUES_PROBABILITY = 0.8

# Probability of using negative strategy when captured values are available.
# We want to mostly use captured values to test deeper application logic.
NEGATIVE_STRATEGY_PROBABILITY = 0.03

# Probability of biasing path parameter integers toward positive values.
# Most REST APIs use positive integers for resource IDs, so this improves
# the chance of hitting existing resources while still allowing edge cases.
PATH_INTEGER_POSITIVE_BIAS = 0.8

# Probability of using schema examples instead of generated values.
# 20% example usage provides good coverage of domain-specific values
# while still allowing hypothesis-generated exploration.
EXAMPLE_USAGE_PROBABILITY = 0.20

# Low so injected constants seed exploration without crowding out hypothesis-generated values.
CONSTANTS_OVERLAY_PROBABILITY = 0.15


def _variant_key(variant: dict[str, Any]) -> str:
    """Create a stable string key for a variant dict."""
    return jsonschema_rs.canonical.json.to_string(variant)


def build_semantic_overlay(
    inner_strategy: st.SearchStrategy,
    leaf_descriptors: list[LeafDescriptor],
    semantic_index: SemanticValueIndex,
    validator_cls: type[jsonschema_rs.Validator],
    container_schema: JsonSchema | None = None,
) -> st.SearchStrategy:
    """Replace generated leaf values with semantic-pool draws that pass leaf and container validation.

    ``container_schema`` is the consumer object schema (body or parameter set). Container-level
    constraints (``not``, ``if`` / ``then``, ``dependentSchemas``, top-level ``oneOf``) can fail
    after a leaf is substituted even when the leaf itself validates; the overlay re-checks the
    full container after every substitution and reverts when the result is invalid.
    """
    from hypothesis import strategies as st

    from schemathesis.specs.openapi.examples import _example_is_valid
    from schemathesis.specs.openapi.semantic_pool import SEMANTIC_OVERLAY_PROBABILITY

    paired: list[tuple[LeafDescriptor, jsonschema_rs.Validator | None]] = []
    for descriptor in leaf_descriptors:
        try:
            validator = make_validator(descriptor.schema, validator_cls)
        except jsonschema_rs.ValidationError:
            # Malformed leaf schema (e.g. invalid regex); fall through with no leaf gate.
            validator = None
        paired.append((descriptor, validator))

    container_validator: jsonschema_rs.Validator | None = None
    if container_schema is not None:
        try:
            container_validator = make_validator(container_schema, validator_cls)
        except jsonschema_rs.ValidationError:
            # Malformed container schema; substitutions skip the container-level revalidation.
            container_validator = None

    @st.composite  # type: ignore[untyped-decorator]
    def overlaid(draw: st.DrawFn) -> JsonValue | GeneratedValue:
        base = draw(inner_strategy)
        # Inner may already be a GeneratedValue carrying pool_draws / meta; unwrap once and re-wrap
        # at the end so we propagate that provenance alongside any new semantic draws.
        if isinstance(base, GeneratedValue):
            inner_meta = base.meta
            inner_pool_draws = base.pool_draws
            inner_semantic_draws = base.semantic_draws
            inner_dictionary_draws = base.dictionary_draws
            inner_constants_draws = base.constants_draws
            body = base.value
        else:
            inner_meta = None
            inner_pool_draws = ()
            inner_semantic_draws = ()
            inner_dictionary_draws = ()
            inner_constants_draws = ()
            body = base
        if not isinstance(body, dict):
            return base
        # `st.floats` shrinks toward 0, biasing substitution well above the configured probability.
        random = draw(st.randoms())
        # `inner_strategy` may be `build_example_aware_strategy`, which returns a shared
        # example dict by reference. Defer the deepclone until a substitution actually fires
        # so non-substituting draws keep the zero-copy fast path.
        copied = False
        new_draws: list[SemanticDraw] | None = None
        for descriptor, validator in paired:
            if random.random() >= SEMANTIC_OVERLAY_PROBABILITY:
                continue
            candidates = semantic_index.lookup(
                type_token=descriptor.type,
                format_token=descriptor.format,
                pattern_hash=descriptor.pattern_hash,
                normalized_name=descriptor.normalized_name,
            )
            if not candidates:
                continue
            candidate = draw(st.sampled_from(candidates))
            value = candidate.value
            if validator is not None and not _example_is_valid(value, validator):
                continue
            if not copied:
                body = deepclone(body)
                copied = True
            original = _get_at_path(body, descriptor.path)
            if original is _MISSING:
                continue
            _set_at_path(body, descriptor.path, value)
            if container_validator is not None and not _example_is_valid(body, container_validator):
                _set_at_path(body, descriptor.path, original)
                continue
            semantic_index.record_draw(
                type_token=descriptor.type,
                format_token=descriptor.format,
                pattern_hash=descriptor.pattern_hash,
                normalized_name=descriptor.normalized_name,
                value=value,
            )
            if new_draws is None:
                new_draws = []
            new_draws.append(
                SemanticDraw(
                    path=descriptor.path,
                    type_token=descriptor.type,
                    format_token=descriptor.format,
                    pattern_hash=descriptor.pattern_hash,
                    normalized_name=descriptor.normalized_name,
                    value=value,
                    source_operation=candidate.source_operation,
                )
            )
        if new_draws is None and not isinstance(base, GeneratedValue):
            return body
        combined_semantic = inner_semantic_draws + tuple(new_draws) if new_draws else inner_semantic_draws
        return GeneratedValue(
            body,
            inner_meta,
            inner_pool_draws,
            combined_semantic,
            inner_dictionary_draws,
            _prune_overwritten_constants(inner_constants_draws, body),
        )

    return overlaid()


def _without_security_parameters(
    schema_properties: dict, operation: OpenApiOperation, location: ParameterLocation
) -> dict:
    """Drop auth-carrying properties: a harvested credential would make generated auth valid."""
    from schemathesis.specs.openapi._auth_retry import get_security_parameters

    names = {
        parameter["name"] for parameter in get_security_parameters(operation) if parameter.get("in") == location.value
    }
    if not names:
        return schema_properties
    return {name: schema for name, schema in schema_properties.items() if name not in names}


def _is_generatable_constant(value: ConstantValue, generation_config: GenerationConfig, *, header_like: bool) -> bool:
    """Whether a harvested literal is a value generation was allowed to produce in the first place."""
    if not isinstance(value, str):
        return True
    if not generation_config.allow_x00 and "\x00" in value:
        return False
    excluded = generation_config.exclude_header_characters
    if header_like and excluded and any(char in value for char in excluded):
        return False
    codec = generation_config.codec
    if codec is not None:
        try:
            value.encode(codec)
        except UnicodeEncodeError:
            return False
    return True


def build_constants_overlay_strategy(
    inner: st.SearchStrategy,
    *,
    source: ConstantsPool,
    schema_properties: dict,
    validator_cls: type,
    location: str,
    generation_config: GenerationConfig,
    container_schema: dict | None = None,
    probability: float = CONSTANTS_OVERLAY_PROBABILITY,
) -> st.SearchStrategy:
    """Substitute object properties with schema-valid constants from the SUT at `probability`.

    Skips properties with no schema-valid pool value; discards a substitution that breaks
    `container_schema`'s cross-field constraints.
    """
    from hypothesis import strategies as st

    from schemathesis.openapi.generation.filters import is_valid_header
    from schemathesis.specs.openapi.examples import _example_is_valid

    # Header and cookie values funnel through `is_valid_header`; a constant it would reject makes the
    # location filter discard the whole case, so screen those out here instead of paying that cost.
    header_like = location in ("header", "cookie")

    # Resolve once: property path -> tuple of (schema-valid pool value, its origin) for that property.
    candidates: dict[tuple[str, ...], list[tuple[ConstantValue, Origin | None]]] = {}
    root_schema = container_schema or {"type": "object", "properties": schema_properties}
    leaves = (
        _iter_constant_leaves(root_schema, root_schema=root_schema, validator_cls=validator_cls)
        if location == "body"
        else (((name,), schema) for name, schema in schema_properties.items() if isinstance(schema, dict))
    )
    for path, schema in leaves:
        keys = _constant_types_for(schema, validator_cls)
        if location != "body":
            # `bytes`/`binary` constants only belong in a request body; other locations serialize
            # scalars and cannot carry a `Binary` object.
            keys = tuple(key for key in keys if key != "bytes")
        pool_values: list[tuple[ConstantValue, Origin | None]] = []
        for key in keys:
            if source.has_values_for(key):
                for entry in source.entries_for(key):
                    value = Binary(entry.value) if isinstance(entry.value, bytes) else entry.value
                    if not _is_generatable_constant(value, generation_config, header_like=header_like):
                        continue
                    pool_values.append((value, entry.origins[0] if entry.origins else None))
        if not pool_values:
            continue
        # Property schemas keep bundled `$ref`s pointing at the container's `x-bundled` store;
        # splice it back in so the leaf validator can resolve them instead of erroring out.
        leaf_schema = schema_with_bundle(schema, container_schema) if isinstance(container_schema, dict) else schema
        try:
            validator = make_validator(leaf_schema, validator_cls)
        except Exception:
            continue
        valid = tuple(
            (value, origin)
            for value, origin in pool_values
            # `Binary` renders as an empty `str`, so `jsonschema_rs` rejects its type and
            # `_example_is_valid` swallows that error into `True`; length-check the raw bytes instead.
            if (
                _binary_length_fits(value.data, schema)
                if isinstance(value, Binary)
                else _example_is_valid(value, validator)
            )
        )
        if header_like:
            valid = tuple(
                (value, origin)
                for value, origin in valid
                if not isinstance(value, str) or is_valid_header({path[-1]: value})
            )
        if valid:
            candidates.setdefault(path, []).extend(valid)

    if not candidates:
        return inner

    container_validator = None
    if isinstance(container_schema, dict):
        try:
            container_validator = make_validator(container_schema, validator_cls)
        except Exception:
            container_validator = None

    @st.composite  # type: ignore[untyped-decorator,unused-ignore]
    def overlay(draw: st.DrawFn) -> Any:
        produced = draw(inner)
        random = draw(st.randoms())
        value = produced.value if isinstance(produced, GeneratedValue) else produced
        if not isinstance(value, dict):
            return produced

        # Defer the copy until a substitution actually fires so non-substituting draws
        # (the common case at this probability) keep the zero-copy fast path.
        new_value: dict[str, Any] | None = None
        new_draws: list[ConstantDraw] = []
        for path, valid_values in candidates.items():
            if _get_at_path(value, path) is _MISSING:
                continue
            if random.random() >= probability:
                continue
            chosen, origin = random.choice(valid_values)
            if new_value is None:
                new_value = deepclone(value)
            _set_at_path(new_value, path, chosen)
            new_draws.append(
                ConstantDraw(
                    location=location,
                    parameter_name=path[-1],
                    value=chosen.data if isinstance(chosen, Binary) else chosen,
                    origin=origin,
                    body_path="/" + "/".join(path) if location == "body" else None,
                )
            )

        if new_value is None:
            return produced

        # Guard against cross-field constraints that leaf-level validation can't catch.
        if not _example_is_valid(new_value, container_validator):
            return produced

        if isinstance(produced, GeneratedValue):
            return GeneratedValue(
                value=new_value,
                meta=produced.meta,
                pool_draws=produced.pool_draws,
                semantic_draws=produced.semantic_draws,
                dictionary_draws=produced.dictionary_draws,
                constants_draws=(*produced.constants_draws, *new_draws),
            )
        return GeneratedValue(value=new_value, meta=None, constants_draws=tuple(new_draws))

    return overlay()


def _iter_constant_leaves(
    schema: JsonSchemaObject,
    *,
    root_schema: JsonSchemaObject,
    validator_cls: type,
    path: tuple[str, ...] = (),
    depth: int = 0,
) -> Iterator[tuple[tuple[str, ...], JsonSchemaObject]]:
    from schemathesis.specs.openapi.semantic_pool import DEFAULT_MAX_DEPTH

    if depth > DEFAULT_MAX_DEPTH:
        return
    schema = maybe_resolve_bundled(cast(JsonSchemaObject, schema_with_bundle(schema, root_schema)))
    if path and _constant_types_for(schema, validator_cls):
        yield path, schema
    properties = schema.get("properties")
    if isinstance(properties, dict):
        for name, subschema in properties.items():
            if isinstance(name, str) and isinstance(subschema, dict):
                yield from _iter_constant_leaves(
                    subschema,
                    root_schema=root_schema,
                    validator_cls=validator_cls,
                    path=(*path, name),
                    depth=depth + 1,
                )
    for keyword in ("allOf", "oneOf", "anyOf"):
        branches = schema.get(keyword)
        if isinstance(branches, list):
            for branch in branches:
                if isinstance(branch, dict):
                    yield from _iter_constant_leaves(
                        branch,
                        root_schema=root_schema,
                        validator_cls=validator_cls,
                        path=path,
                        depth=depth + 1,
                    )


def _binary_length_fits(data: bytes, schema: JsonSchemaObject) -> bool:
    length = len(data)
    minimum = schema.get("minLength")
    if isinstance(minimum, int) and length < minimum:
        return False
    maximum = schema.get("maxLength")
    if isinstance(maximum, int) and length > maximum:
        return False
    return True


def _constant_types_for(schema: JsonSchemaObject, validator_cls: type) -> tuple[ConstantType, ...]:
    # Object/array-structured schemas are never scalar substitution targets, even without an
    # explicit `type` - a typeless schema's `get_type` otherwise reports every type.
    if schema.keys() & {"properties", "items", "additionalProperties"}:
        return ()
    fmt = schema.get("format")
    if fmt == "binary" and schema.keys() & {"enum", "const", "pattern"}:
        return ()
    # A `format` the validator does not enforce is annotation-only: it cannot filter a type-valid
    # literal that violates the format, so a harvested constant would clobber the format-aware value
    # with data the SUT rejects (the `phone`/`int32` cases). `binary` is length-checked separately.
    if fmt is not None and fmt != "binary" and fmt not in VALIDATED_FORMATS_BY_DRAFT.get(validator_cls, frozenset()):
        return ()
    result: list[ConstantType] = []

    def add(key: ConstantType) -> None:
        if key not in result:
            result.append(key)

    for json_type in get_type(schema):
        if json_type == "string":
            add("bytes" if fmt == "binary" else "string")
        elif json_type == "integer":
            add("integer")
        elif json_type == "number":
            # `number` accepts both integers and floats per JSON Schema.
            add("float")
            add("integer")
    return tuple(result)


def _captured_variants_active(extra_data_source: ExtraDataSource | None, operation: APIOperation) -> bool:
    """True when the captured-variant overlay would bind stale resource values for this operation.

    Resource-bound variants are baked into the strategy at build time, so caching freezes them
    against later updates. Semantic-only data sources read the live index per draw, so caching
    them is safe.
    """
    if extra_data_source is None:
        return False
    from schemathesis.specs.openapi.extra_data_source import OpenApiExtraDataSource

    if not isinstance(extra_data_source, OpenApiExtraDataSource):
        return True
    return operation.label in extra_data_source.consumer_labels


def _semantic_cache_key(extra_data_source: ExtraDataSource | None) -> int | None:
    """Identity of the semantic index that the overlay would close over, or None when no overlay applies.

    The overlay binds at build time, so a strategy cached for one source must not be reused for
    a different one (or for a call where the overlay is absent entirely).
    """
    if extra_data_source is None:
        return None
    from schemathesis.specs.openapi.extra_data_source import OpenApiExtraDataSource

    if not isinstance(extra_data_source, OpenApiExtraDataSource):
        return None
    if extra_data_source.semantic_index is None:
        return None
    return id(extra_data_source.semantic_index)


_MISSING: object = object()


def _get_at_path(target: object, path: tuple[str, ...]) -> object:
    """Read the value at path. Returns the ``_MISSING`` sentinel when any segment is absent."""
    cursor: object = target
    for segment in path:
        if not isinstance(cursor, dict) or segment not in cursor:
            return _MISSING
        cursor = cursor[segment]
    return cursor


def _set_at_path(target: dict[str, Any], path: tuple[str, ...], value: object) -> bool:
    """Set value at path in target. Returns False when the path is missing from target."""
    if not path:
        return False
    cursor: object = target
    for segment in path[:-1]:
        if not isinstance(cursor, dict) or segment not in cursor:
            return False
        cursor = cursor[segment]
    if not isinstance(cursor, dict) or path[-1] not in cursor:
        return False
    cursor[path[-1]] = value
    return True


def _prune_overwritten_constants(
    constants_draws: tuple[ConstantDraw, ...], value: JsonValue
) -> tuple[ConstantDraw, ...]:
    """Drop provenance for constant leaves a later overlay overwrote, so draws match the emitted value."""
    if not constants_draws:
        return constants_draws
    return tuple(draw for draw in constants_draws if _constant_value_at_draw(value, draw) == draw.value)


def _prune_overwritten_body_constants(
    constants_draws: tuple[ConstantDraw, ...], body: JsonValue
) -> tuple[ConstantDraw, ...]:
    """Prune only body-location draws against `body`; other locations aren't part of the body."""
    if not constants_draws:
        return constants_draws
    return tuple(
        draw for draw in constants_draws if draw.location != "body" or _constant_value_at_draw(body, draw) == draw.value
    )


def _constant_value_at_draw(value: object, draw: ConstantDraw) -> object:
    path = (
        tuple(segment for segment in draw.body_path.split("/") if segment) if draw.body_path else (draw.parameter_name,)
    )
    current = _get_at_path(value, path)
    return current.data if isinstance(current, Binary) else current


def _constant_values_at_draws(constants_draws: tuple[ConstantDraw, ...], value: object) -> tuple[object, ...]:
    return tuple(_constant_value_at_draw(value, draw) for draw in constants_draws)


def _prune_modified_constants(
    constants_draws: tuple[ConstantDraw, ...], previous_values: tuple[object, ...], value: object
) -> tuple[ConstantDraw, ...]:
    return tuple(
        draw
        for draw, previous in zip(constants_draws, previous_values, strict=True)
        if _constant_value_at_draw(value, draw) == previous
    )


def build_hybrid_strategy(
    original_strategy: st.SearchStrategy,
    captured_variants: list[CapturedVariant],
    usage_tracker: VariantUsageTracker,
) -> st.SearchStrategy:
    """Combine original strategy with captured variants using weighted sampling.

    Weights selection to prefer variants that haven't been drawn recently,
    reducing wasted test budget from repeated operations on the same resources.

    Captured variants may be partial (only containing parameters with resource
    requirements). We merge them with generated values to ensure all required
    parameters are present. When a variant is selected, the strategy returns a
    `GeneratedValue` carrying the pool-draw provenance; otherwise it returns the
    raw generated value (a dict or scalar).
    """
    from hypothesis import strategies as st

    # Pre-compute keys for all variants
    variant_keys = [_variant_key(v.overlay) for v in captured_variants]
    n_variants = len(captured_variants)

    @st.composite  # type: ignore[untyped-decorator]
    def hybrid(draw: st.DrawFn) -> Any:
        random = draw(st.randoms())

        # Decide: use captured variant or generate fresh?
        if random.random() >= CAPTURED_VALUES_PROBABILITY:
            return draw(original_strategy)

        # Always generate base values first, then overlay captured values.
        # This ensures parameters without resource requirements (like `file_name`)
        # still get generated values while resource-linked params use captured data.
        base = draw(original_strategy)

        # An upstream overlay (e.g. the constants overlay) may wrap the dict in
        # `GeneratedValue`. Unwrap so we can deep-merge captured values into the body,
        # and combine provenance below.
        base_meta = None
        base_pool_draws: tuple = ()
        base_semantic_draws: tuple = ()
        base_dictionary_draws: tuple = ()
        base_constants_draws: tuple = ()
        if isinstance(base, GeneratedValue):
            base_meta = base.meta
            base_pool_draws = base.pool_draws
            base_semantic_draws = base.semantic_draws
            base_dictionary_draws = base.dictionary_draws
            base_constants_draws = base.constants_draws
            base = base.value

        # Captured variants are partial dict overrides; meaningful only when the base is a dict.
        # Schemas without `type: object` can produce scalars/lists — leave those untouched.
        if not isinstance(base, dict):
            return base

        # Single variant: no selection needed
        if n_variants == 1:
            usage_tracker.record_draw(variant_keys[0])
            chosen = captured_variants[0]
        else:
            # Shuffle indices before weighted selection to avoid Hypothesis's bias
            # toward early indices when using cumulative probability selection.
            idx = usage_tracker.weighted_select(variant_keys, random)
            usage_tracker.record_draw(variant_keys[idx])
            chosen = captured_variants[idx]

        _deep_merge_overlay(base, chosen.overlay)
        return GeneratedValue(
            value=base,
            meta=base_meta,
            pool_draws=base_pool_draws + chosen.draws,
            semantic_draws=base_semantic_draws,
            dictionary_draws=base_dictionary_draws,
            constants_draws=_prune_overwritten_constants(base_constants_draws, base),
        )

    return hybrid()


def _deep_merge_overlay(target: dict[str, Any], overlay: dict[str, Any]) -> None:
    """Apply `overlay` onto `target` in place, recursing into nested dicts so leaf overlays don't drop generated siblings."""
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            _deep_merge_overlay(target[key], value)
        else:
            target[key] = value


def _resolve_inclusive_bound(schema: JsonSchemaObject, inclusive_key: str, exclusive_key: str, step: int) -> int | None:
    # `bool` is a subclass of `int`; a boolean bound is an invalid schema, so ignore it.
    value = schema.get(inclusive_key)
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    exclusive = schema.get(exclusive_key)
    if isinstance(exclusive, int) and not isinstance(exclusive, bool):
        return exclusive + step
    return None


def _integer_property_bounds(schema: JsonSchemaObject) -> dict[str, tuple[int | None, int | None]]:
    """Per-property inclusive integer bounds, used to keep the positive-ID bias within range."""
    bounds: dict[str, tuple[int | None, int | None]] = {}
    for name, prop_schema in schema.get("properties", {}).items():
        if isinstance(prop_schema, dict) and prop_schema.get("type") == "integer":
            minimum = _resolve_inclusive_bound(prop_schema, "minimum", "exclusiveMinimum", 1)
            maximum = _resolve_inclusive_bound(prop_schema, "maximum", "exclusiveMaximum", -1)
            bounds[name] = (minimum, maximum)
    return bounds


def _has_explicit_slash_example(examples: Sequence[object]) -> bool:
    for example in examples:
        if isinstance(example, str) and "/" in unquote(example):
            return True
    return False


def _get_explicit_intent_path_names(*, parameters: Sequence[OpenApiParameter]) -> frozenset[str]:
    """Collect path parameter names where encoded slash is explicitly allowed."""
    explicit: set[str] = set()
    for parameter in parameters:
        if _has_explicit_slash_example(parameter.examples):
            explicit.add(parameter.name)
        schema = parameter.optimized_schema
        if isinstance(schema, dict) and schema.get("format") in STRING_FORMATS:
            explicit.add(parameter.name)
    return frozenset(explicit)


def _bias_path_integers_to_positive(
    params: dict[str, Any], random: Random, bounds: dict[str, tuple[int | None, int | None]]
) -> dict[str, Any]:
    """Bias integer path parameters toward positive values.

    Most REST APIs use positive integers for resource IDs (1, 2, 3, ...),
    so biasing toward positive values increases the chance of hitting
    existing resources while still occasionally testing edge cases like 0
    and negative numbers.
    """
    result = {}
    for key, value in params.items():
        # `bool` is a subclass of `int`; without excluding it `False` would be rewritten to `1`.
        if (
            isinstance(value, int)
            and not isinstance(value, bool)
            and value <= 0
            and random.random() < PATH_INTEGER_POSITIVE_BIAS
        ):
            # Convert to positive: 0 -> 1, negative -> abs(value) or 1
            candidate = max(1, abs(value))
            minimum, maximum = bounds.get(key, (None, None))
            # `abs` can overshoot the declared range (e.g. `abs(int32 min) = int32 max + 1`);
            # keep the already-valid original value rather than emit out-of-range data.
            if (maximum is not None and candidate > maximum) or (minimum is not None and candidate < minimum):
                result[key] = value
            else:
                result[key] = candidate
        else:
            result[key] = value
    return result


def build_positive_biased_path_strategy(
    strategy: st.SearchStrategy, bounds: dict[str, tuple[int | None, int | None]]
) -> st.SearchStrategy:
    """Wrap a path parameter strategy to bias integers toward positive values."""
    from hypothesis import strategies as st

    @st.composite  # type: ignore[untyped-decorator]
    def biased(draw: st.DrawFn) -> Any:
        params = draw(strategy)
        if params is None:
            return params
        random = draw(st.randoms())
        # An upstream overlay (e.g. the constants overlay) may have wrapped the dict in
        # `GeneratedValue`. Unwrap, bias, and re-wrap preserving provenance — otherwise
        # `params.items()` would explode for integer path parameters.
        if isinstance(params, GeneratedValue):
            biased_value = _bias_path_integers_to_positive(params.value, random, bounds)
            return GeneratedValue(
                value=biased_value,
                meta=params.meta,
                pool_draws=params.pool_draws,
                semantic_draws=params.semantic_draws,
                dictionary_draws=params.dictionary_draws,
                constants_draws=params.constants_draws,
            )
        return _bias_path_integers_to_positive(params, random, bounds)

    return biased()


def filter_schema_valid_examples(examples: list[JsonValue], schema: JsonSchema, validator_cls: type) -> list[JsonValue]:
    """Drop examples that don't conform to the given schema; real-world specs often disagree."""
    if not examples:
        return examples
    from schemathesis.specs.openapi._hypothesis import snapped_float32_clone
    from schemathesis.specs.openapi.examples import _example_is_valid

    # Snap `format: float` bounds so examples that collapse to an out-of-range value once narrowed are evicted.
    schema = snapped_float32_clone(schema)
    try:
        validator = make_validator(schema, validator_cls)
    except Exception:
        return examples
    return [ex for ex in examples if _example_is_valid(ex, validator)]


def build_example_aware_strategy(
    original_strategy: st.SearchStrategy,
    examples: list[JsonValue],
) -> st.SearchStrategy:
    """Combine original strategy with schema examples.

    Uses examples approximately 20% of the time to provide coverage of domain-specific
    values while still allowing hypothesis-generated exploration (~80%).

    Uses true randomness (not Hypothesis's reproducible random) to ensure the
    probability distribution is uniform and not affected by shrinking behavior.
    """
    from hypothesis import strategies as st

    @st.composite  # type: ignore[untyped-decorator]
    def with_examples(draw: st.DrawFn) -> Any:
        # Use true random for uniform distribution (like stateful phase)
        random = draw(st.randoms(use_true_random=True))

        # 20% use example, 80% generate fresh
        if random.random() >= EXAMPLE_USAGE_PROBABILITY:
            return draw(original_strategy)

        return random.choice(examples)

    return with_examples()


def build_parameter_example_aware_strategy(
    original_strategy: st.SearchStrategy,
    parameter_examples: dict[str, list[JsonValue]],
) -> st.SearchStrategy:
    """Combine original parameter strategy with per-parameter schema examples.

    For each parameter with examples, approximately 20% chance to replace its
    generated value with one of the examples. Parameters without examples keep
    their generated values.

    Uses true randomness for uniform probability distribution.
    """
    from hypothesis import strategies as st

    @st.composite  # type: ignore[untyped-decorator]
    def with_parameter_examples(draw: st.DrawFn) -> dict[str, Any] | None:
        result = draw(original_strategy)
        if result is None:
            return result

        # Use true random for uniform distribution
        random = draw(st.randoms(use_true_random=True))

        # For each parameter with examples, potentially replace with example
        for param_name, examples in parameter_examples.items():
            if not examples:
                continue
            # 20% chance to use example for this parameter
            if random.random() < EXAMPLE_USAGE_PROBABILITY:
                result[param_name] = random.choice(examples)

        return result

    return with_parameter_examples()


@dataclass
class OpenApiComponent(ABC):
    definition: JsonSchemaObject
    is_required: bool
    name_to_uri: dict[str, str]
    adapter: SpecificationAdapter

    __slots__ = (
        "definition",
        "is_required",
        "name_to_uri",
        "adapter",
        "_optimized_schema",
        "_unoptimized_schema",
        "_raw_schema",
        "_validation_schema",
        "_examples",
        "_mutation_targets",
    )

    def __post_init__(self) -> None:
        self._optimized_schema: JsonSchema | NotSet = NOT_SET
        self._unoptimized_schema: JsonSchema | NotSet = NOT_SET
        self._raw_schema: JsonSchema | NotSet = NOT_SET
        self._validation_schema: JsonSchema | NotSet = NOT_SET
        self._examples: list | NotSet = NOT_SET
        self._mutation_targets: tuple | NotSet = NOT_SET

    @property
    def optimized_schema(self) -> JsonSchema:
        """JSON schema optimized for data generation."""
        if self._optimized_schema is NOT_SET:
            self._optimized_schema = self._build_schema(optimize=True)
        assert not isinstance(self._optimized_schema, NotSet)
        return self._optimized_schema

    @property
    def unoptimized_schema(self) -> JsonSchema:
        """JSON schema preserving original constraint structure."""
        if self._unoptimized_schema is NOT_SET:
            self._unoptimized_schema = self._build_schema(optimize=False)
        assert not isinstance(self._unoptimized_schema, NotSet)
        return self._unoptimized_schema

    @property
    def raw_schema(self) -> JsonSchema:
        """Raw schema extracted from definition before JSON Schema conversion."""
        if self._raw_schema is NOT_SET:
            self._raw_schema = self._get_raw_schema()
        assert not isinstance(self._raw_schema, NotSet)
        return self._raw_schema

    @property
    def validation_schema(self) -> JsonSchema:
        """JSON schema for conformance validation — resolved but without generation-specific type injection.

        Keeps `prefixItems` intact so `Draft202012Validator` accepts the schema during construction.
        """
        if self._validation_schema is NOT_SET:
            self._validation_schema = to_json_schema(
                self.raw_schema,
                nullable_keyword=self.adapter.nullable_keyword,
                update_quantifiers=False,
                upgrade_legacy_exclusive_bounds=(
                    self.adapter.jsonschema_validator_cls is jsonschema_rs.Draft202012Validator
                ),
                convert_prefix_items=False,
                convert_if_then_else=False,
                name_to_uri=self.name_to_uri,
                merge_ref_siblings=self.adapter.ref_siblings,
            )
        assert not isinstance(self._validation_schema, NotSet)
        return self._validation_schema

    @abstractmethod
    def _get_raw_schema(self) -> JsonSchema:
        """Get the raw schema for this component."""
        raise NotImplementedError

    @abstractmethod
    def _get_default_type(self) -> str | None:
        """Get default type for this parameter."""
        raise NotImplementedError

    def _build_schema(self, *, optimize: bool) -> JsonSchema:
        """Build JSON schema with optional optimizations for data generation."""
        schema = to_json_schema(
            self.raw_schema,
            nullable_keyword=self.adapter.nullable_keyword,
            update_quantifiers=optimize,
            upgrade_legacy_exclusive_bounds=(
                self.adapter.jsonschema_validator_cls is jsonschema_rs.Draft202012Validator
            ),
            name_to_uri=self.name_to_uri,
            merge_ref_siblings=self.adapter.ref_siblings,
        )

        # Missing the `type` keyword may significantly slowdown data generation, ensure it is set
        default_type = self._get_default_type()
        if isinstance(schema, dict):
            if default_type is not None:
                schema.setdefault("type", default_type)
        elif schema is True and default_type is not None:
            # Restrict such cases too
            schema = {"type": default_type}

        return schema

    @property
    def examples(self) -> list:
        """All examples extracted from definition.

        Combines both single 'example' and 'examples' container values.
        """
        if self._examples is NOT_SET:
            self._examples = self._extract_examples()
        assert not isinstance(self._examples, NotSet)
        return self._examples

    @property
    def mutation_targets(self) -> tuple[MutationTargetDescriptor, ...]:
        """Pre-computed walk recipes for every mutation target reachable from `optimized_schema`.

        Cached for the component's lifetime so strategy rebuilds against the unmodified
        `optimized_schema` skip the walk. Callers must NOT pass these descriptors when
        the schema reaching the strategy has been transformed (e.g. by error-feedback
        adjustments) — those calls fall through to a fresh `compute_mutation_targets` against
        the transformed schema so newly-synthesized targets are picked up.
        """
        from schemathesis.specs.openapi.negative.mutations import compute_mutation_targets

        if self._mutation_targets is NOT_SET:
            self._mutation_targets = compute_mutation_targets(self.optimized_schema)
        assert not isinstance(self._mutation_targets, NotSet)
        return self._mutation_targets

    def _extract_examples(self) -> list[object]:
        """Extract examples from definition and schema.

        Looks for examples in:
        - Top-level 'example' and 'examples' keywords in the definition
        - 'example' and 'examples' keywords in the nested schema (for parameters with schema)
        """
        examples: list[object] = []

        # Extract from top-level definition
        container = self.definition.get(self.adapter.examples_container_keyword)
        if isinstance(container, dict):
            examples.extend(ex["value"] for ex in container.values() if isinstance(ex, dict) and "value" in ex)
        elif isinstance(container, list):
            examples.extend(container)

        example = self.definition.get(self.adapter.example_keyword, NOT_SET)
        if example is not NOT_SET:
            examples.append(example)

        # Also extract from the schema if present (e.g., parameter.schema.example)
        raw_schema = self.raw_schema
        if isinstance(raw_schema, dict):
            schema_example = raw_schema.get(self.adapter.example_keyword, NOT_SET)
            if schema_example is not NOT_SET:
                examples.append(schema_example)

            # JSON Schema supports 'examples' as an array
            schema_examples = raw_schema.get("examples")
            if isinstance(schema_examples, list):
                examples.extend(schema_examples)

        return examples

    def _get_strategy_examples(self, operation: OpenApiOperation) -> list[JsonValue]:
        """Extract examples using proper OAS3 Example Object unpacking for the definition container.

        Unlike `_extract_examples`, uses `extract_inner_examples` which correctly handles
        both dict and list containers — extracting inner `value`/`externalValue` fields
        and resolving `$ref`s via the operation schema.
        """
        from schemathesis.specs.openapi.examples import extract_inner_examples

        examples: list[JsonValue] = []

        container = self.definition.get(self.adapter.examples_container_keyword)
        if container is not None:
            examples.extend(extract_inner_examples(container, operation.schema))

        example = self.definition.get(self.adapter.example_keyword, NOT_SET)
        if example is not NOT_SET:
            examples.append(example)

        raw_schema = self.raw_schema
        if isinstance(raw_schema, dict):
            schema_example = raw_schema.get(self.adapter.example_keyword, NOT_SET)
            if schema_example is not NOT_SET:
                examples.append(schema_example)

            schema_examples = raw_schema.get("examples")
            if isinstance(schema_examples, list):
                examples.extend(schema_examples)

        return examples


@dataclass
class OpenApiParameter(OpenApiComponent):
    """OpenAPI operation parameter."""

    @classmethod
    def from_definition(
        cls, *, definition: JsonSchemaObject, name_to_uri: dict[str, str], adapter: SpecificationAdapter
    ) -> OpenApiParameter:
        is_required = definition.get("required", False)
        return cls(definition=definition, is_required=is_required, name_to_uri=name_to_uri, adapter=adapter)

    @property
    def media_type(self) -> None:
        """Non-body parameters have no media type."""
        return None

    @property
    def name(self) -> str:
        """Parameter name."""
        return self.definition["name"]

    @property
    def location(self) -> ParameterLocation:
        """Where this parameter is located."""
        # Direct dict lookup beats `ParameterLocation(value)` — the enum dispatch
        # (`EnumType.__call__` → `Enum.__new__`) is the slow path here.
        return _IN_TO_LOCATION.get(self.definition.get("in"), ParameterLocation.UNKNOWN)

    def _build_schema(self, *, optimize: bool) -> JsonSchema:
        schema = super()._build_schema(optimize=optimize)
        # A required parameter with an empty array value serializes to nothing (form/simple styles
        # drop empty arrays), leaving the parameter absent from the request and violating `required`.
        if (
            self.is_required
            and isinstance(schema, dict)
            and schema.get("type") == "array"
            and schema.get("minItems", 0) < 1
            and schema.get("maxItems", 1) >= 1
        ):
            schema = {**schema, "minItems": 1}
        return schema

    def _get_raw_schema(self) -> JsonSchema:
        """Get raw parameter schema."""
        return self.adapter.extract_parameter_schema(self.definition)

    def _get_default_type(self) -> str | None:
        """Return default type if parameter is in string-type location."""
        # Content-encoded parameters (`content:` instead of `schema:`) carry a
        # pre-serialization schema (e.g. object) — not the wire type.  Don't
        # inject `type: string` for them; their schema already describes the value.
        if "schema" not in self.definition:
            return None
        return "string" if self.location.is_in_header else None


@dataclass
class OpenApiBody(OpenApiComponent):
    """OpenAPI request body."""

    media_type: str
    resource_name: str | None
    name_to_uri: dict[str, str]

    __slots__ = (
        "definition",
        "is_required",
        "media_type",
        "resource_name",
        "name_to_uri",
        "adapter",
        "_optimized_schema",
        "_unoptimized_schema",
        "_raw_schema",
        "_validation_schema",
        "_examples",
        "_mutation_targets",
        "_positive_strategy_cache",
        "_negative_strategy_cache",
        "_is_negatable",
    )

    @classmethod
    def from_definition(
        cls,
        *,
        definition: JsonSchemaObject,
        is_required: bool,
        media_type: str,
        resource_name: str | None,
        name_to_uri: dict[str, str],
        adapter: SpecificationAdapter,
    ) -> OpenApiBody:
        return cls(
            definition=definition,
            is_required=is_required,
            media_type=media_type,
            resource_name=resource_name,
            name_to_uri=name_to_uri,
            adapter=adapter,
        )

    @classmethod
    def from_form_parameters(
        cls,
        *,
        definition: JsonSchemaObject,
        media_type: str,
        name_to_uri: dict[str, str],
        adapter: SpecificationAdapter,
    ) -> OpenApiBody:
        return cls(
            definition=definition,
            is_required=True,
            media_type=media_type,
            resource_name=None,
            name_to_uri=name_to_uri,
            adapter=adapter,
        )

    def __post_init__(self) -> None:
        super().__post_init__()
        self._positive_strategy_cache: tuple[st.SearchStrategy, int | None, int | None, int | None] | NotSet = NOT_SET
        self._negative_strategy_cache: tuple[st.SearchStrategy, int | None, int | None, int | None] | NotSet = NOT_SET
        self._is_negatable: bool | NotSet = NOT_SET

    @property
    def is_negatable(self) -> bool:
        """Whether this body schema can be negated for negative test generation."""
        if self._is_negatable is NOT_SET:
            from schemathesis.specs.openapi.negative.utils import can_negate

            schema = self.optimized_schema
            self._is_negatable = isinstance(schema, dict) and can_negate(schema)
        assert not isinstance(self._is_negatable, NotSet)
        return self._is_negatable

    @property
    def location(self) -> ParameterLocation:
        return ParameterLocation.BODY

    @property
    def name(self) -> str:
        # The name doesn't matter but is here for the interface completeness.
        return "body"

    def _get_raw_schema(self) -> JsonSchema:
        """Get raw body schema."""
        return self.definition.get("schema", {})

    def _get_default_type(self) -> str | None:
        """Return default type if body is a form type."""
        return "object" if self.media_type in FORM_MEDIA_TYPES else None

    def get_property_content_type(self, property_name: str) -> str | list[str] | None:
        """Get custom contentType for a form property from `encoding` definition."""
        encoding = self.definition.get("encoding", {})
        property_encoding = encoding.get(property_name, {})
        return property_encoding.get("contentType")

    def get_property_filename(self, property_name: str) -> str | None:
        """Get filename from encoding.headers.Content-Disposition for a form property."""
        encoding = self.definition.get("encoding", {})
        headers = encoding.get(property_name, {}).get("headers", {})
        cd = headers.get("Content-Disposition", {})
        value = cd.get("example") or (cd.get("schema") or {}).get("example")
        if not value:
            return None
        match = re.search(r'filename="([^"]*)"', value) or re.search(r"filename=(\S+)", value)
        return match.group(1) if match else None

    def get_strategy(
        self,
        operation: OpenApiOperation,
        generation_config: GenerationConfig,
        generation_mode: GenerationMode,
        extra_data_source: ExtraDataSource | None = None,
        mix_examples: bool = True,
        error_feedback: ErrorFeedbackStore | None = None,
        constants_value_source: ConstantsPool | None = None,
    ) -> st.SearchStrategy:
        """Get a Hypothesis strategy for this body parameter."""
        # The captured-variant overlay binds resource values at build time, so caching it
        # would freeze stale variants. The semantic overlay closes over the live index and
        # remains correct under caching, so semantic-only data sources stay cache-eligible.
        use_cache = mix_examples and not _captured_variants_active(extra_data_source, operation)
        feedback_generation = error_feedback.generation if error_feedback is not None else None
        semantic_id = _semantic_cache_key(extra_data_source)
        constants_id = id(constants_value_source) if constants_value_source is not None else None

        # Check cache based on generation mode (only when extra data sources are not used)
        if use_cache:
            if generation_mode == GenerationMode.POSITIVE:
                cached = self._positive_strategy_cache
                if cached is not NOT_SET and not isinstance(cached, NotSet):
                    cached_strategy, cached_generation, cached_semantic, cached_constants = cached
                    if (
                        cached_generation == feedback_generation
                        and cached_semantic == semantic_id
                        and cached_constants == constants_id
                    ):
                        return cached_strategy
            else:
                cached = self._negative_strategy_cache
                if cached is not NOT_SET and not isinstance(cached, NotSet):
                    cached_strategy, cached_generation, cached_semantic, cached_constants = cached
                    if (
                        cached_generation == feedback_generation
                        and cached_semantic == semantic_id
                        and cached_constants == constants_id
                    ):
                        return cached_strategy

        # Import here to avoid circular dependency
        from schemathesis.specs.openapi._hypothesis import GENERATOR_MODE_TO_STRATEGY_FACTORY

        # Check for captured variants for hybrid approach
        captured_variants: list[CapturedVariant] | None = None
        usage_tracker = None
        if extra_data_source is not None:
            from schemathesis.specs.openapi.extra_data_source import OpenApiExtraDataSource

            if isinstance(extra_data_source, OpenApiExtraDataSource):
                captured_variants = extra_data_source.get_captured_variants(
                    operation=operation, location=ParameterLocation.BODY, schema=self.optimized_schema
                )
                usage_tracker = extra_data_source.usage_tracker

        # Build the strategy
        strategy_factory = GENERATOR_MODE_TO_STRATEGY_FACTORY[generation_mode]
        schema = self.optimized_schema
        if error_feedback is not None:
            from schemathesis.specs.openapi.error_feedback import apply_adjustments

            schema = apply_adjustments(
                operation=operation,
                location=ParameterLocation.BODY,
                schema=schema,
                store=error_feedback,
            )
        # Reuse the precomputed target walk recipes when the strategy is generating against
        # `optimized_schema` directly (no error-feedback adjustment fired).
        target_descriptors = (
            self.mutation_targets if generation_mode.is_negative and schema is self.optimized_schema else None
        )
        # Negative filter needs `prefixItems` intact so `Draft202012Validator` can be constructed.
        validation_schema = self.validation_schema if generation_mode.is_negative else None
        strategy = strategy_factory(
            schema,
            operation.label,
            ParameterLocation.BODY,
            self.media_type,
            generation_config,
            operation.schema.adapter.jsonschema_validator_cls,
            self.name_to_uri,
            validation_schema=validation_schema,
            target_descriptors=target_descriptors,
        )

        # Mix in schema examples for positive mode (20% example, 80% generated)
        # Skip during EXAMPLES phase since examples are handled separately there
        if mix_examples and generation_mode == GenerationMode.POSITIVE:
            # Filter against the adjustment-applied schema so spec examples that the API
            # has demonstrated to be invalid (e.g. `"dd-MM-yyyy"` after format inference)
            # don't leak into the mixer.
            validation_schema = self.validation_schema
            if error_feedback is not None:
                from schemathesis.specs.openapi.error_feedback import apply_adjustments

                validation_schema = apply_adjustments(
                    operation=operation,
                    location=ParameterLocation.BODY,
                    schema=validation_schema,
                    store=error_feedback,
                )
            strategy_examples = filter_schema_valid_examples(
                self._get_strategy_examples(operation),
                validation_schema,
                self.adapter.jsonschema_validator_cls,
            )
            if strategy_examples:
                strategy = build_example_aware_strategy(strategy, strategy_examples)

        # Apply the constants overlay BEFORE the semantic and captured-variant overlays so
        # live, response-derived values get priority: a semantic substitution or a captured
        # productId must not be overwritten by a random pool literal while later attribution
        # claims those sources were used. `build_semantic_overlay` and `build_hybrid_strategy`
        # both unwrap an upstream `GeneratedValue` and re-wrap with combined provenance, so
        # constants substitutions that survive remain attributed correctly.
        body_schema_properties = schema.get("properties", {}) if isinstance(schema, dict) else {}
        if constants_value_source is not None and generation_mode == GenerationMode.POSITIVE:
            strategy = build_constants_overlay_strategy(
                strategy,
                source=constants_value_source,
                schema_properties=body_schema_properties,
                validator_cls=operation.schema.adapter.jsonschema_validator_cls,
                location="body",
                generation_config=generation_config,
                container_schema=schema if isinstance(schema, dict) else None,
            )

        if (
            extra_data_source is not None
            and generation_mode == GenerationMode.POSITIVE
            and isinstance(extra_data_source, OpenApiExtraDataSource)
            and extra_data_source.semantic_index is not None
            and isinstance(schema, dict)
        ):
            from schemathesis.specs.openapi.semantic_pool import iter_consumer_leaves

            leaf_descriptors = iter_consumer_leaves(schema)
            if leaf_descriptors:
                strategy = build_semantic_overlay(
                    strategy,
                    leaf_descriptors,
                    extra_data_source.semantic_index,
                    operation.schema.adapter.jsonschema_validator_cls,
                    container_schema=schema,
                )

        # Apply hybrid approach when captured variants are available
        if captured_variants and usage_tracker is not None:
            if generation_mode.is_negative:
                strategy = self._build_negative_aware_strategy(
                    operation,
                    generation_config,
                    captured_variants,
                    usage_tracker,
                    mix_examples=mix_examples,
                    error_feedback=error_feedback,
                    constants_value_source=constants_value_source,
                )
            else:
                strategy = build_hybrid_strategy(strategy, captured_variants, usage_tracker)

        from schemathesis.generation.body_overrides import (
            build_body_override_overlay_strategy,
            resolve_body_overrides,
        )
        from schemathesis.generation.dictionaries import (
            build_body_dictionary_overlay_strategy,
            resolve_body_bindings,
        )

        body_bindings = resolve_body_bindings(
            operation=operation,
            body_schema=schema,
            generation_config=generation_config,
        )
        if body_bindings:
            strategy = build_body_dictionary_overlay_strategy(
                strategy,
                bindings=body_bindings,
                operation_label=operation.label,
                validator_cls=operation.schema.adapter.jsonschema_validator_cls,
                generation_mode=generation_mode,
            )

        body_overrides = resolve_body_overrides(operation=operation, body_schema=schema)
        if body_overrides:
            strategy = build_body_override_overlay_strategy(strategy, overrides=body_overrides)

        # Cache the strategy keyed by feedback generation, semantic-index identity, and constants-source identity
        if use_cache:
            slot = (strategy, feedback_generation, semantic_id, constants_id)
            if generation_mode == GenerationMode.POSITIVE:
                self._positive_strategy_cache = slot
            else:
                self._negative_strategy_cache = slot

        return strategy

    def _build_negative_aware_strategy(
        self,
        operation: APIOperation,
        generation_config: GenerationConfig,
        captured_variants: list[CapturedVariant],
        usage_tracker: VariantUsageTracker,
        *,
        mix_examples: bool = True,
        error_feedback: ErrorFeedbackStore | None = None,
        constants_value_source: ConstantsPool | None = None,
    ) -> st.SearchStrategy:
        """Build strategy for negative mode when captured values are available."""
        from hypothesis import strategies as st

        positive_strategy = self.get_strategy(
            operation,
            generation_config,
            GenerationMode.POSITIVE,
            extra_data_source=None,
            mix_examples=mix_examples,
            error_feedback=error_feedback,
            constants_value_source=constants_value_source,
        )
        positive_strategy = build_hybrid_strategy(positive_strategy, captured_variants, usage_tracker)
        # The hybrid strategy already wraps in `GeneratedValue` when it picks a captured pool
        # variant (so pool-draw provenance survives). Wrap only the un-wrapped values here.
        positive_strategy = positive_strategy.map(
            lambda x: x if isinstance(x, GeneratedValue) else GeneratedValue(x, None)
        )

        negative_strategy = self.get_strategy(
            operation,
            generation_config,
            GenerationMode.NEGATIVE,
            extra_data_source=None,
            mix_examples=mix_examples,
            error_feedback=error_feedback,
        )

        @st.composite  # type: ignore[untyped-decorator]
        def choose_strategy(draw: st.DrawFn) -> GeneratedValue:
            random = draw(st.randoms())
            if random.random() < NEGATIVE_STRATEGY_PROBABILITY:
                return draw(negative_strategy)
            return draw(positive_strategy)

        return choose_strategy()


OPENAPI_20_EXCLUDE_KEYS = frozenset(["required", "name", "in", "title", "description"])


def extract_parameter_schema_v2(parameter: Mapping[str, Any]) -> JsonSchemaObject:
    # In Open API 2.0, schema for non-body parameters lives directly in the parameter definition
    schema = {key: value for key, value in parameter.items() if key not in OPENAPI_20_EXCLUDE_KEYS}
    # Swagger 2.0 idiom: `type: array` + scalar `enum` constrains item values, not the whole array.
    # Move the enum onto `items` (intersecting with any existing `items.enum`).
    if (
        schema.get("type") == "array"
        and isinstance(schema.get("enum"), list)
        and isinstance(schema.get("items"), dict)
        and all(not isinstance(v, list) for v in schema["enum"])
    ):
        items = dict(schema["items"])
        existing = items.get("enum")
        if isinstance(existing, list):
            allowed = {_hashable(v) for v in existing}
            items["enum"] = [v for v in schema["enum"] if _hashable(v) in allowed]
        else:
            items["enum"] = list(schema["enum"])
        schema["items"] = items
        del schema["enum"]
    return schema


def _hashable(value: object) -> object:
    if isinstance(value, list):
        return tuple(_hashable(v) for v in value)
    if isinstance(value, dict):
        return tuple(sorted((k, _hashable(v)) for k, v in value.items()))
    return value


def extract_parameter_schema_v3(parameter: Mapping[str, Any]) -> JsonSchema:
    if "schema" in parameter:
        if not isinstance(parameter["schema"], dict | bool):
            raise InvalidSchema(
                INVALID_SCHEMA_MESSAGE.format(
                    location=parameter.get("in", ""),
                    name=parameter.get("name", "<UNKNOWN>"),
                    schema=parameter["schema"],
                ),
            )
        return parameter["schema"]
    # https://github.com/OAI/OpenAPI-Specification/blob/master/versions/3.0.3.md#fixed-fields-10
    # > The map MUST only contain one entry.
    try:
        content = parameter["content"]
    except KeyError as exc:
        raise InvalidSchema(
            MISSING_SCHEMA_OR_CONTENT_MESSAGE.format(
                location=parameter.get("in", ""), name=parameter.get("name", "<UNKNOWN>")
            ),
        ) from exc
    options = iter(content.values())
    media_type_object = next(options)
    return media_type_object.get("schema", {})


def _bundle_parameter(
    parameter: Mapping,
    resolver: Resolver,
    bundler: Bundler,
    bundle_cache: dict[int, tuple[dict[str, Any], dict[str, str]]],
) -> tuple[dict[str, Any], dict[str, str]]:
    """Bundle a parameter definition to make it self-contained."""
    param_id = id(parameter)
    if param_id in bundle_cache:
        cached_definition, cached_name_to_uri = bundle_cache[param_id]
        return deepclone(cached_definition), dict(cached_name_to_uri)

    parameter_resolver, definition = maybe_resolve_with_resolver(parameter, resolver)
    schema = definition.get("schema")
    name_to_uri = {}
    if schema is not None:
        definition = dict(definition)
        try:
            bundled = bundler.bundle_for_generation(
                schema,
                parameter_resolver,
            )
            definition["schema"] = bundled.schema
            name_to_uri.update(bundled.name_to_uri)
        except BundleError as exc:
            location = parameter.get("in", "")
            name = parameter.get("name", "<UNKNOWN>")
            raise InvalidSchema.from_bundle_error(exc, location, name) from exc
    elif "content" in definition:
        definition = dict(definition)
        try:
            updated_content: dict[str, Any] = {}
            for media_type, media_type_object in definition["content"].items():
                if not isinstance(media_type_object, Mapping):
                    updated_content[media_type] = media_type_object
                    continue
                media_type_object = dict(media_type_object)
                nested_schema = media_type_object.get("schema")
                if isinstance(nested_schema, dict):
                    bundled = bundler.bundle_for_generation(
                        nested_schema,
                        parameter_resolver,
                    )
                    media_type_object["schema"] = bundled.schema
                    name_to_uri.update(bundled.name_to_uri)
                updated_content[media_type] = media_type_object
            definition["content"] = updated_content
        except BundleError as exc:
            location = parameter.get("in", "")
            name = parameter.get("name", "<UNKNOWN>")
            raise InvalidSchema.from_bundle_error(exc, location, name) from exc

    definition_ = cast(dict, definition)
    result = definition_, name_to_uri
    bundle_cache[param_id] = (deepclone(definition_), dict(name_to_uri))
    return result


OPENAPI_20_DEFAULT_BODY_MEDIA_TYPE = "application/json"
OPENAPI_20_DEFAULT_FORM_MEDIA_TYPE = "multipart/form-data"


def _validated_parameters(definition: Mapping[str, Any]) -> Sequence[Mapping[str, Any]]:
    """Return the operation's `parameters` list, validating its shape."""
    parameters = definition.get("parameters", [])
    if not isinstance(parameters, list):
        raise InvalidSchema("'parameters' must be a list of parameter objects")
    for index, parameter in enumerate(parameters):
        if not isinstance(parameter, dict):
            raise InvalidSchema(f"'parameters[{index}]' must be a parameter object")
    return parameters


def iter_parameters_v2(
    definition: Mapping[str, Any],
    shared_parameters: Sequence[Mapping[str, Any]],
    default_media_types: list[str],
    resolver: Resolver,
    adapter: SpecificationAdapter,
    bundler: Bundler,
    bundle_cache: BundleCache,
) -> Iterator[OperationParameter]:
    media_types = definition.get("consumes", default_media_types)
    # Wildcard `*/*` is valid Swagger but no real client sends it as Content-Type. Drop it when concrete
    # entries exist; otherwise fall through to the JSON default so downstream dispatch can route bodies.
    if media_types and any(m == "*/*" for m in media_types):
        concrete = [m for m in media_types if m != "*/*"]
        media_types = concrete or []
    # For `in=body` parameters, we imply `application/json` as the default media type because it is the most common.
    body_media_types = media_types or (OPENAPI_20_DEFAULT_BODY_MEDIA_TYPE,)
    # If an API operation has parameters with `in=formData`, Schemathesis should know how to serialize it.
    # We can't be 100% sure what media type is expected by the server and chose `multipart/form-data` as
    # the default because it is broader since it allows us to upload files.
    form_data_media_types = media_types or (OPENAPI_20_DEFAULT_FORM_MEDIA_TYPE,)

    operation_parameters = _validated_parameters(definition)

    form_parameters = []
    form_name_to_uri = {}
    for parameter in chain(operation_parameters, shared_parameters):
        parameter, name_to_uri = _bundle_parameter(parameter, resolver, bundler, bundle_cache)
        location = parameter.get("in")
        if not isinstance(location, str):
            continue
        if location in HEADER_LOCATIONS:
            check_header_name(parameter["name"])

        if location == "formData":
            # We need to gather form parameters first before creating a composite parameter for them
            form_parameters.append(parameter)
            form_name_to_uri.update(name_to_uri)
        elif location == ParameterLocation.BODY:
            # Take the original definition & extract the resource_name from there
            resource_name = None
            for param in chain(operation_parameters, shared_parameters):
                _, param = maybe_resolve_with_resolver(param, resolver)
                if param.get("in") == ParameterLocation.BODY:
                    if "$ref" in param["schema"]:
                        resource_name = resource_name_from_ref(param["schema"]["$ref"])
            for media_type in body_media_types:
                yield OpenApiBody.from_definition(
                    definition=parameter,
                    is_required=parameter.get("required", False),
                    media_type=media_type,
                    name_to_uri=name_to_uri,
                    resource_name=resource_name,
                    adapter=adapter,
                )
        else:
            yield OpenApiParameter.from_definition(definition=parameter, name_to_uri=name_to_uri, adapter=adapter)

    if form_parameters:
        form_data = form_data_to_json_schema(form_parameters)
        # `in: formData` requires a form MIME in `consumes`; if none present, pick multipart when a file param exists, else urlencoded.
        form_media_types = [m for m in form_data_media_types if m in FORM_MEDIA_TYPES]
        if not form_media_types:
            has_file = any(parameter.get("type") == "file" for parameter in form_parameters)
            form_media_types = ["multipart/form-data" if has_file else "application/x-www-form-urlencoded"]
        for media_type in form_media_types:
            # Individual `formData` parameters are joined into a single "composite" one.
            yield OpenApiBody.from_form_parameters(
                definition=form_data, media_type=media_type, name_to_uri=form_name_to_uri, adapter=adapter
            )


def iter_parameters_v3(
    definition: Mapping[str, Any],
    shared_parameters: Sequence[Mapping[str, Any]],
    default_media_types: list[str],
    resolver: Resolver,
    adapter: SpecificationAdapter,
    bundler: Bundler,
    bundle_cache: BundleCache,
) -> Iterator[OperationParameter]:
    # Open API 3.0 has the `requestBody` keyword, which may contain multiple different payload variants.
    # TODO: Typing
    operation = definition

    seen_querystring = False
    seen_query = False

    operation_parameters = _validated_parameters(definition)

    for parameter in chain(operation_parameters, shared_parameters):
        parameter, name_to_uri = _bundle_parameter(parameter, resolver, bundler, bundle_cache)
        location = parameter.get("in")
        if not isinstance(location, str):
            continue
        if location == "querystring":
            if seen_querystring:
                raise InvalidSchema("OpenAPI 3.2 allows at most one `querystring` parameter per operation")
            if seen_query:
                raise InvalidSchema("OpenAPI 3.2 does not allow `query` and `querystring` parameters together")
            seen_querystring = True
        elif location == "query":
            if seen_querystring:
                raise InvalidSchema("OpenAPI 3.2 does not allow `query` and `querystring` parameters together")
            seen_query = True
        if location in HEADER_LOCATIONS:
            check_header_name(parameter["name"])

        yield OpenApiParameter.from_definition(definition=parameter, name_to_uri=name_to_uri, adapter=adapter)

    request_body_or_ref = operation.get("requestBody")
    if request_body_or_ref is not None:
        body_resolver, request_body_or_ref = maybe_resolve_with_resolver(request_body_or_ref, resolver)
        # It could be an object inside `requestBodies`, which could be a reference itself
        body_resolver, request_body = maybe_resolve_with_resolver(request_body_or_ref, body_resolver)

        required = request_body.get("required", False)
        for media_type, content in request_body["content"].items():
            resource_name = None
            schema = content.get("schema")
            name_to_uri = {}
            if isinstance(schema, dict):
                content = dict(content)
                if "$ref" in schema:
                    resource_name = resource_name_from_ref(schema["$ref"])
                else:
                    items = schema.get("items")
                    if isinstance(items, dict) and "$ref" in items:
                        resource_name = resource_name_from_ref(items["$ref"])
                try:
                    to_bundle = cast(dict[str, Any], schema)
                    bundled = bundler.bundle_for_generation(
                        to_bundle,
                        body_resolver,
                    )
                    content["schema"] = bundled.schema
                    name_to_uri = bundled.name_to_uri
                except BundleError as exc:
                    raise InvalidSchema.from_bundle_error(exc, "body") from exc
            yield OpenApiBody.from_definition(
                definition=content,
                is_required=required,
                media_type=media_type,
                resource_name=resource_name,
                name_to_uri=name_to_uri,
                adapter=adapter,
            )


def resource_name_from_ref(reference: str) -> str:
    """Extract and normalize resource name from a $ref."""
    from schemathesis.specs.openapi.stateful.dependencies.naming import normalize_schema_name

    raw_name = reference.rsplit("/", maxsplit=1)[-1]
    return normalize_schema_name(raw_name)


def build_path_parameter_v2(kwargs: Mapping[str, Any]) -> OpenApiParameter:
    from schemathesis.specs.openapi.adapter import v2

    return OpenApiParameter.from_definition(
        definition={"in": ParameterLocation.PATH.value, "required": True, "type": "string", "minLength": 1, **kwargs},
        name_to_uri={},
        adapter=v2,
    )


def build_path_parameter_v3_0(kwargs: Mapping[str, Any]) -> OpenApiParameter:
    from schemathesis.specs.openapi.adapter import v3_0

    return OpenApiParameter.from_definition(
        definition={
            "in": ParameterLocation.PATH.value,
            "required": True,
            "schema": {"type": "string", "minLength": 1},
            **kwargs,
        },
        name_to_uri={},
        adapter=v3_0,
    )


def build_path_parameter_v3_1(kwargs: Mapping[str, Any]) -> OpenApiParameter:
    from schemathesis.specs.openapi.adapter import v3_1

    return OpenApiParameter.from_definition(
        definition={
            "in": ParameterLocation.PATH.value,
            "required": True,
            "schema": {"type": "string", "minLength": 1},
            **kwargs,
        },
        name_to_uri={},
        adapter=v3_1,
    )


@dataclass
class OpenApiParameterSet(ParameterSet):
    items: list[OpenApiParameter]
    location: ParameterLocation

    __slots__ = (
        "items",
        "location",
        "adapter",
        "_schema",
        "_validation_schema",
        "_schema_cache",
        "_strategy_cache",
        "_strict_validator",
    )

    def __init__(
        self,
        location: ParameterLocation,
        items: list[OpenApiParameter] | None = None,
        *,
        adapter: SpecificationAdapter,
    ) -> None:
        self.location = location
        self.adapter = adapter
        self.items = items or []
        self._schema: dict | NotSet = NOT_SET
        self._validation_schema: dict | NotSet = NOT_SET
        self._schema_cache: dict[frozenset[str], dict[str, Any]] = {}
        self._strategy_cache: dict[
            tuple[frozenset[str], GenerationMode, int | None, int | None, int | None], st.SearchStrategy
        ] = {}
        self._strict_validator: jsonschema_rs.Validator | NotSet = NOT_SET

    def get_strict_validator(self) -> jsonschema_rs.Validator:
        if isinstance(self._strict_validator, NotSet):
            self._strict_validator = self.adapter.jsonschema_validator_cls(
                self.schema, validate_formats=True, pattern_options=FANCY_REGEX_OPTIONS
            )
        return self._strict_validator

    @property
    def schema(self) -> dict[str, Any]:
        if self._schema is NOT_SET:
            self._schema = parameters_to_json_schema(self.items, self.location)
        assert not isinstance(self._schema, NotSet)
        return self._schema

    @property
    def validation_schema(self) -> dict[str, Any]:
        # Suitable for Draft 2020-12 validators — keeps `prefixItems` intact.
        if self._validation_schema is NOT_SET:
            self._validation_schema = parameters_to_validation_schema(self.items, self.location)
        assert not isinstance(self._validation_schema, NotSet)
        return self._validation_schema

    @property
    def name_to_uri(self) -> dict[str, str]:
        """Combine name_to_uri from all parameters in this set.

        Merging is safe because a single Bundler instance is used for all parameters,
        so bundled schema names are globally unique with no overlap between parameters.
        """
        result: dict[str, str] = {}
        for item in self.items:
            result.update(item.name_to_uri)
        return result

    def get_schema_with_exclusions(self, exclude: Iterable[str]) -> dict[str, Any]:
        """Get cached schema with specified parameters excluded."""
        exclude_key = _EMPTY_EXCLUDE_KEY if not exclude else frozenset(exclude)

        if exclude_key in self._schema_cache:
            return self._schema_cache[exclude_key]

        schema = self._apply_exclusions(self.schema, exclude_key)
        self._schema_cache[exclude_key] = schema
        return schema

    def _apply_exclusions(self, base: dict[str, Any], exclude_key: frozenset[str]) -> dict[str, Any]:
        if not exclude_key:
            return base
        # Need to exclude some parameters - create a shallow copy to avoid mutating cached schema
        schema = dict(base)
        if self.location == ParameterLocation.HEADER:
            # Remove excluded headers case-insensitively
            exclude_lower = {name.lower() for name in exclude_key}
            schema["properties"] = {
                key: value for key, value in schema["properties"].items() if key.lower() not in exclude_lower
            }
            if "required" in schema:
                kept = [key for key in schema["required"] if key.lower() not in exclude_lower]
                if kept:
                    schema["required"] = kept
                else:
                    # `required` must contain at least one item per JSON Schema; drop the key.
                    del schema["required"]
        else:
            # Non-header locations: remove by exact name
            schema["properties"] = {key: value for key, value in schema["properties"].items() if key not in exclude_key}
            if "required" in schema:
                kept = [key for key in schema["required"] if key not in exclude_key]
                if kept:
                    schema["required"] = kept
                else:
                    del schema["required"]
        return schema

    def get_strategy(
        self,
        operation: OpenApiOperation,
        generation_config: GenerationConfig,
        generation_mode: GenerationMode,
        exclude: Iterable[str] = (),
        extra_data_source: ExtraDataSource | None = None,
        mix_examples: bool = True,
        error_feedback: ErrorFeedbackStore | None = None,
        constants_value_source: ConstantsPool | None = None,
    ) -> st.SearchStrategy:
        """Get a Hypothesis strategy for this parameter set with specified exclusions."""
        exclude_key = _EMPTY_EXCLUDE_KEY if not exclude else frozenset(exclude)
        feedback_generation = error_feedback.generation if error_feedback is not None else None
        semantic_id = _semantic_cache_key(extra_data_source)
        constants_id = id(constants_value_source) if constants_value_source is not None else None
        cache_key = (exclude_key, generation_mode, feedback_generation, semantic_id, constants_id)

        use_cache = mix_examples and not _captured_variants_active(extra_data_source, operation)

        if use_cache and cache_key in self._strategy_cache:
            return self._strategy_cache[cache_key]

        # Import here to avoid circular dependency
        from hypothesis import strategies as st

        from schemathesis.openapi.generation.filters import is_valid_header, is_valid_path, is_valid_query
        from schemathesis.specs.openapi._hypothesis import (
            GENERATOR_MODE_TO_STRATEGY_FACTORY,
            _can_skip_header_filter,
            jsonify_python_specific_types,
            make_negative_strategy,
        )

        def _quote_all_safe(value: dict[str, Any]) -> dict[str, Any]:
            """Quote path parameter values, preserving invalid inputs for later filtering."""
            quoted = dict(value)
            try:
                return quote_all(quoted)
            except UnicodeEncodeError:
                return value

        # Get schema with exclusions
        schema: JsonSchema = self.get_schema_with_exclusions(exclude)
        if error_feedback is not None:
            from schemathesis.specs.openapi.error_feedback import apply_adjustments

            schema = apply_adjustments(
                operation=operation,
                location=self.location,
                schema=schema,
                store=error_feedback,
            )

        # Check for captured variants for hybrid approach
        captured_variants: list[CapturedVariant] | None = None
        usage_tracker = None
        if extra_data_source is not None:
            from schemathesis.specs.openapi.extra_data_source import OpenApiExtraDataSource

            if isinstance(extra_data_source, OpenApiExtraDataSource):
                captured_variants = extra_data_source.get_captured_variants(
                    operation=operation, location=self.location, schema=schema
                )
                usage_tracker = extra_data_source.usage_tracker

        # `JsonSchema` can be boolean (`True` / `False`), normalize to an object schema for downstream usage.
        if isinstance(schema, bool):
            schema = {} if schema else {"not": {}}
        assert isinstance(schema, dict)
        schema_obj: JsonSchemaObject = schema

        strategy_factory = GENERATOR_MODE_TO_STRATEGY_FACTORY[generation_mode]

        if not schema_obj.get("properties") and strategy_factory is make_negative_strategy:
            # Nothing to negate - all properties were excluded
            strategy = st.none()
        else:
            # Negative filter needs `prefixItems` intact so `Draft202012Validator` can be constructed.
            validation_schema_obj: JsonSchema | None = None
            if strategy_factory is make_negative_strategy:
                validation_schema_obj = self._apply_exclusions(
                    parameters_to_validation_schema(self.items, self.location), exclude_key
                )
            strategy = strategy_factory(
                schema_obj,
                operation.label,
                self.location,
                None,
                generation_config,
                operation.schema.adapter.jsonschema_validator_cls,
                self.name_to_uri,
                validation_schema=validation_schema_obj,
            )

            # For negative strategies, we need to handle GeneratedValue wrappers
            is_negative = strategy_factory is make_negative_strategy

            # Mix in schema examples for positive mode (20% example, 80% generated per parameter)
            # Must be applied BEFORE serialization so examples go through the same transformations
            # Skip during EXAMPLES phase since examples are handled separately there
            if mix_examples and not is_negative:
                validator_cls = operation.schema.adapter.jsonschema_validator_cls
                # Splice inferred constraints (format / min / max etc.) onto each parameter's
                # validation schema so examples the API has demonstrated to be invalid get evicted.
                adjusted_properties = schema_obj.get("properties") if isinstance(schema_obj, dict) else None
                parameter_examples: dict[str, list[Any]] = {}
                for param in self.items:
                    if param.name in exclude_key or not param.examples:
                        continue
                    validation_schema = param.validation_schema
                    if isinstance(adjusted_properties, dict) and isinstance(validation_schema, dict):
                        inferred = adjusted_properties.get(param.name)
                        if isinstance(inferred, dict):
                            validation_schema = {**validation_schema, **inferred}
                    valid = filter_schema_valid_examples(param.examples, validation_schema, validator_cls)
                    if valid:
                        parameter_examples[param.name] = valid
                if parameter_examples:
                    strategy = build_parameter_example_aware_strategy(strategy, parameter_examples)

            # Bias path parameter integers toward positive values BEFORE the constants overlay, so a
            # substituted literal (e.g. a negative sentinel id) is the final value and is never rewritten.
            if self.location == ParameterLocation.PATH and not is_negative:
                integer_bounds = _integer_property_bounds(schema_obj)
                if integer_bounds:
                    strategy = build_positive_biased_path_strategy(strategy, integer_bounds)

            # Apply the constants overlay BEFORE the semantic overlay so live, response-derived
            # values can overwrite a random pool literal for the same field. `build_semantic_overlay`
            # unwraps and re-wraps `GeneratedValue`, so any constant that survives keeps its provenance.
            schema_properties = schema_obj.get("properties", {}) if isinstance(schema_obj, dict) else {}
            if constants_value_source is not None and not is_negative:
                strategy = build_constants_overlay_strategy(
                    strategy,
                    source=constants_value_source,
                    schema_properties=_without_security_parameters(schema_properties, operation, self.location),
                    validator_cls=operation.schema.adapter.jsonschema_validator_cls,
                    location=self.location.value,
                    generation_config=generation_config,
                    container_schema=schema_obj if isinstance(schema_obj, dict) else None,
                )

            # Path parameters are always identity values; semantic substitution does not apply.
            # Runs before serialization and location-specific filters so substituted values pass through
            # the same `_quote_all_safe` / `is_valid_query` / `is_valid_header` paths as generated ones.
            if (
                extra_data_source is not None
                and self.location != ParameterLocation.PATH
                and not is_negative
                and isinstance(extra_data_source, OpenApiExtraDataSource)
                and extra_data_source.semantic_index is not None
            ):
                from schemathesis.specs.openapi.semantic_pool import iter_consumer_leaves

                leaf_descriptors = iter_consumer_leaves(schema_obj)
                if leaf_descriptors:
                    strategy = build_semantic_overlay(
                        strategy,
                        leaf_descriptors,
                        extra_data_source.semantic_index,
                        operation.schema.adapter.jsonschema_validator_cls,
                        container_schema=schema_obj,
                    )

            explicit_intent_path_names: frozenset[str] = frozenset()
            if self.location == ParameterLocation.PATH:
                explicit_intent_path_names = _get_explicit_intent_path_names(parameters=self.items)

            from schemathesis.generation.dictionaries import (
                build_dictionary_overlay_strategy,
                resolve_parameter_bindings,
            )

            bindings = resolve_parameter_bindings(
                operation=operation,
                location=self.location,
                properties=schema_properties,
                generation_config=generation_config,
            )
            if bindings:
                strategy = build_dictionary_overlay_strategy(
                    strategy,
                    bindings=bindings,
                    operation_label=operation.label,
                    parameter_location=self.location,
                    schema_properties=schema_properties,
                    validator_cls=operation.schema.adapter.jsonschema_validator_cls,
                    generation_mode=generation_mode,
                )

            serialize = operation.get_parameter_serializer(self.location)
            if serialize is not None:
                if is_negative:
                    # Apply serialize only to the value part of GeneratedValue
                    strategy = strategy.map(
                        lambda x: GeneratedValue(
                            serialize(x.value),
                            x.meta,
                            x.pool_draws,
                            x.semantic_draws,
                            x.dictionary_draws,
                            x.constants_draws,
                        )
                    )
                else:
                    # Semantic overlay can wrap the value in `GeneratedValue` on substitution;
                    # the wrapper preserves it (unwraps before `serialize`, re-wraps after).
                    from schemathesis.specs.openapi.negative import wrap_map_hook_for_generated_value

                    strategy = strategy.map(wrap_map_hook_for_generated_value(serialize, prune_constants=False))

            # Path & query parameters will be cast to string anyway, but having their JSON equivalents for
            # `True` / `False` / `None` improves chances of them passing validation in apps
            # that expect boolean / null types
            # and not aware of Python-specific representation of those types
            if self.location == ParameterLocation.PATH:
                if is_negative:
                    strategy = strategy.map(
                        lambda x: GeneratedValue(
                            _quote_all_safe(jsonify_python_specific_types(x.value)),
                            x.meta,
                            x.pool_draws,
                            x.semantic_draws,
                            x.dictionary_draws,
                            x.constants_draws,
                        )
                    )
                    # Keep strict anti-misrouting defaults for negative generation.
                    # Explicit %2F allowances apply only to positive data.
                    strategy = strategy.filter(lambda x: is_valid_path(x.value))
                else:
                    # Dictionary / semantic overlays can wrap the value in `GeneratedValue`
                    # under positive mode; route both helpers through the unwrap-rewrap
                    # adapters so substituted path values still serialize correctly.
                    from schemathesis.specs.openapi.negative import (
                        wrap_filter_hook_for_generated_value,
                        wrap_map_hook_for_generated_value,
                    )

                    strategy = strategy.map(
                        wrap_map_hook_for_generated_value(_quote_all_safe, prune_constants=False)
                    ).map(wrap_map_hook_for_generated_value(jsonify_python_specific_types, prune_constants=False))
                    strategy = strategy.filter(
                        wrap_filter_hook_for_generated_value(
                            lambda x, allow=explicit_intent_path_names: is_valid_path(x, allow_encoded_slash_for=allow)
                        )
                    )
            elif self.location == ParameterLocation.QUERY:
                query_filter = is_valid_query
                if is_negative:
                    strategy = strategy.filter(lambda x: query_filter(x.value))
                else:
                    from schemathesis.specs.openapi.negative import (
                        wrap_filter_hook_for_generated_value,
                        wrap_map_hook_for_generated_value,
                    )

                    strategy = strategy.filter(wrap_filter_hook_for_generated_value(query_filter))
                if is_negative:
                    strategy = strategy.map(
                        lambda x: GeneratedValue(
                            jsonify_python_specific_types(x.value),
                            x.meta,
                            x.pool_draws,
                            x.semantic_draws,
                            x.dictionary_draws,
                            x.constants_draws,
                        )
                    )
                else:
                    strategy = strategy.map(
                        wrap_map_hook_for_generated_value(jsonify_python_specific_types, prune_constants=False)
                    )
            else:
                header_filter = is_valid_header
                # Headers with special format do not need filtration
                if not (self.location.is_in_header and _can_skip_header_filter(schema)):
                    if is_negative:
                        strategy = strategy.filter(lambda x: header_filter(x.value))
                    else:
                        from schemathesis.specs.openapi.negative import wrap_filter_hook_for_generated_value

                        strategy = strategy.filter(wrap_filter_hook_for_generated_value(header_filter))

        # Apply hybrid approach when captured variants are available
        if captured_variants and usage_tracker is not None:
            if generation_mode.is_negative:
                # In negative mode with captured values, mostly use positive strategy
                # to leverage valuable captured IDs for testing deeper application logic
                strategy = self._build_negative_aware_strategy(
                    operation,
                    generation_config,
                    exclude,
                    captured_variants,
                    usage_tracker,
                    mix_examples=mix_examples,
                    error_feedback=error_feedback,
                    constants_value_source=constants_value_source,
                )
            else:
                strategy = build_hybrid_strategy(strategy, captured_variants, usage_tracker)

        if use_cache:
            self._strategy_cache[cache_key] = strategy
        return strategy

    def _build_negative_aware_strategy(
        self,
        operation: APIOperation,
        generation_config: GenerationConfig,
        exclude: Iterable[str],
        captured_variants: list[CapturedVariant],
        usage_tracker: VariantUsageTracker,
        *,
        mix_examples: bool = True,
        error_feedback: ErrorFeedbackStore | None = None,
        constants_value_source: ConstantsPool | None = None,
    ) -> st.SearchStrategy:
        """Build strategy for negative mode when captured values are available.

        Mostly uses positive strategy with captured values (97%) to test deeper
        application logic, with occasional negative tests (3%).
        """
        from hypothesis import strategies as st

        # Get positive strategy with hybrid approach
        positive_strategy = self.get_strategy(
            operation,
            generation_config,
            GenerationMode.POSITIVE,
            exclude,
            extra_data_source=None,
            mix_examples=mix_examples,
            error_feedback=error_feedback,
            constants_value_source=constants_value_source,
        )
        positive_strategy = build_hybrid_strategy(positive_strategy, captured_variants, usage_tracker)
        # Wrap in GeneratedValue for consistent return type with negative strategy
        # The hybrid strategy already wraps in `GeneratedValue` when it picks a captured pool
        # variant (so pool-draw provenance survives). Wrap only the un-wrapped values here.
        positive_strategy = positive_strategy.map(
            lambda x: x if isinstance(x, GeneratedValue) else GeneratedValue(x, None)
        )

        # Get negative strategy without extra_data_source to avoid recursion
        negative_strategy = self.get_strategy(
            operation,
            generation_config,
            GenerationMode.NEGATIVE,
            exclude,
            extra_data_source=None,
            mix_examples=mix_examples,
            error_feedback=error_feedback,
        )

        @st.composite  # type: ignore[untyped-decorator]
        def choose_strategy(draw: st.DrawFn) -> GeneratedValue:
            random = draw(st.randoms())
            if random.random() < NEGATIVE_STRATEGY_PROBABILITY:
                return draw(negative_strategy)
            return draw(positive_strategy)

        return choose_strategy()


COMBINED_FORM_DATA_MARKER = "x-schemathesis-form-parameter"


def form_data_to_json_schema(parameters: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Convert raw form parameter definitions to a JSON Schema."""
    parameter_data = (
        (param["name"], extract_parameter_schema_v2(param), param.get("required", False)) for param in parameters
    )

    merged = _merge_parameters_to_object_schema(parameter_data, ParameterLocation.BODY)

    return {"schema": merged, COMBINED_FORM_DATA_MARKER: True}


def parameters_to_json_schema(parameters: Iterable[OpenApiParameter], location: ParameterLocation) -> dict[str, Any]:
    """Convert multiple Open API parameters to a JSON Schema."""
    parameter_data = ((param.name, param.optimized_schema, param.is_required) for param in parameters)

    return _merge_parameters_to_object_schema(parameter_data, location)


def parameters_to_validation_schema(
    parameters: Iterable[OpenApiParameter], location: ParameterLocation
) -> dict[str, Any]:
    """Merge parameters' validation schemas — `prefixItems` intact, suitable for Draft 2020-12 validators."""
    parameter_data = ((param.name, param.validation_schema, param.is_required) for param in parameters)

    return _merge_parameters_to_object_schema(parameter_data, location)


def _merge_parameters_to_object_schema(
    parameters: Iterable[tuple[str, Any, bool]], location: ParameterLocation
) -> dict[str, Any]:
    """Merge parameter data into a JSON Schema object."""
    properties: dict[str, Any] = {}
    required: list[str] = []
    bundled: dict[str, Any] = {}
    # HTTP header names are case-insensitive — collapse duplicates onto the first-seen casing.
    canonical_by_lower: dict[str, str] = {}

    for name, subschema, is_required in parameters:
        # Extract bundled data if present
        if isinstance(subschema, dict) and BUNDLE_STORAGE_KEY in subschema:
            subschema = dict(subschema)
            subschema_bundle = subschema.pop(BUNDLE_STORAGE_KEY)
            # NOTE: Bundled schema names are not overlapping as they were bundled via the same `Bundler` that
            # ensures unique names
            bundled.update(subschema_bundle)

        # Apply location-specific adjustments to individual parameter schemas
        if isinstance(subschema, dict):
            # Headers: add format key for plain string types (structured for known headers)
            if location.is_in_header and list(subschema) == ["type"] and subschema["type"] == "string":
                format_key = KNOWN_HEADER_FORMATS.get(name.lower(), HEADER_FORMAT)
                subschema = {**subschema, "format": format_key}

            # Path parameters: ensure string types have minLength >= 1
            elif location == ParameterLocation.PATH and subschema.get("type") == "string":
                if "minLength" not in subschema:
                    subschema = {**subschema, "minLength": 1}

        if location.is_in_header:
            canonical = canonical_by_lower.setdefault(name.lower(), name)
            if canonical != name:
                # Same header under different case — first definition wins.
                if (location == ParameterLocation.PATH or is_required) and canonical not in required:
                    required.append(canonical)
                continue
            name = canonical

        properties[name] = subschema

        # Path parameters are always required
        if (location == ParameterLocation.PATH or is_required) and name not in required:
            required.append(name)

    merged = {
        "properties": properties,
        "additionalProperties": False,
        "type": "object",
    }
    if required:
        merged["required"] = required
    if bundled:
        merged[BUNDLE_STORAGE_KEY] = bundled

    return merged
