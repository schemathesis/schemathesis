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
from schemathesis.core.jsonschema import FANCY_REGEX_OPTIONS, BundleError, Bundler, make_validator
from schemathesis.core.jsonschema.bundler import BUNDLE_STORAGE_KEY, BundleCache
from schemathesis.core.jsonschema.resolver import Resolver
from schemathesis.core.jsonschema.types import JsonSchema, JsonSchemaObject, JsonValue
from schemathesis.core.media_types import FORM_MEDIA_TYPES
from schemathesis.core.parameters import HEADER_LOCATIONS, ParameterLocation
from schemathesis.core.transforms import deepclone
from schemathesis.core.validation import check_header_name
from schemathesis.generation.modes import GenerationMode
from schemathesis.resources import ExtraDataSource
from schemathesis.schemas import APIOperation, ParameterSet
from schemathesis.specs.openapi.adapter.protocol import SpecificationAdapter
from schemathesis.specs.openapi.adapter.references import maybe_resolve_with_resolver
from schemathesis.specs.openapi.converter import to_json_schema
from schemathesis.specs.openapi.formats import HEADER_FORMAT, STRING_FORMATS
from schemathesis.specs.openapi.headers import KNOWN_HEADER_FORMATS
from schemathesis.transport.serialization import quote_all

if TYPE_CHECKING:
    from hypothesis import strategies as st

    from schemathesis.core.error_feedback import ErrorFeedbackStore
    from schemathesis.specs.openapi.extra_data_source import CapturedVariant, VariantUsageTracker
    from schemathesis.specs.openapi.negative.mutations import MutationTargetDescriptor


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


def _variant_key(variant: dict[str, Any]) -> str:
    """Create a stable string key for a variant dict."""
    return jsonschema_rs.canonical.json.to_string(variant)


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

    from schemathesis.specs.openapi.negative import GeneratedValue

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
        return GeneratedValue(value=base, meta=None, pool_draws=chosen.draws)

    return hybrid()


def _deep_merge_overlay(target: dict[str, Any], overlay: dict[str, Any]) -> None:
    """Apply `overlay` onto `target` in place, recursing into nested dicts so leaf overlays don't drop generated siblings."""
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            _deep_merge_overlay(target[key], value)
        else:
            target[key] = value


def _schema_has_integer_properties(schema: JsonSchemaObject) -> bool:
    """Check if the schema has any integer-type properties."""
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        return False
    for prop_schema in properties.values():
        if isinstance(prop_schema, dict) and prop_schema.get("type") == "integer":
            return True
    return False


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


def _bias_path_integers_to_positive(params: dict[str, Any], random: Random) -> dict[str, Any]:
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
            result[key] = max(1, abs(value))
        else:
            result[key] = value
    return result


def build_positive_biased_path_strategy(strategy: st.SearchStrategy) -> st.SearchStrategy:
    """Wrap a path parameter strategy to bias integers toward positive values."""
    from hypothesis import strategies as st

    @st.composite  # type: ignore[untyped-decorator]
    def biased(draw: st.DrawFn) -> dict[str, Any] | None:
        params = draw(strategy)
        if params is None:
            return params
        random = draw(st.randoms())
        return _bias_path_integers_to_positive(params, random)

    return biased()


def filter_schema_valid_examples(examples: list[JsonValue], schema: JsonSchema, validator_cls: type) -> list[JsonValue]:
    """Drop examples that don't conform to the given schema; real-world specs often disagree."""
    if not examples:
        return examples
    from schemathesis.specs.openapi.examples import _example_is_valid

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
    definition: Mapping[str, Any]
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

    def _get_strategy_examples(self, operation: APIOperation) -> list[JsonValue]:
        """Extract examples using proper OAS3 Example Object unpacking for the definition container.

        Unlike `_extract_examples`, uses `extract_inner_examples` which correctly handles
        both dict and list containers — extracting inner `value`/`externalValue` fields
        and resolving `$ref`s via the operation schema.
        """
        from schemathesis.specs.openapi.examples import extract_inner_examples
        from schemathesis.specs.openapi.schemas import OpenApiSchema

        assert isinstance(operation.schema, OpenApiSchema)
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
        cls, *, definition: Mapping[str, Any], name_to_uri: dict[str, str], adapter: SpecificationAdapter
    ) -> OpenApiParameter:
        is_required = definition.get("required", False)
        return cls(definition=definition, is_required=is_required, name_to_uri=name_to_uri, adapter=adapter)

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
        definition: Mapping[str, Any],
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
        definition: Mapping[str, Any],
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
        self._positive_strategy_cache: tuple[st.SearchStrategy, int | None] | NotSet = NOT_SET
        self._negative_strategy_cache: tuple[st.SearchStrategy, int | None] | NotSet = NOT_SET
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
        operation: APIOperation,
        generation_config: GenerationConfig,
        generation_mode: GenerationMode,
        extra_data_source: ExtraDataSource | None = None,
        mix_examples: bool = True,
        error_feedback: ErrorFeedbackStore | None = None,
    ) -> st.SearchStrategy:
        """Get a Hypothesis strategy for this body parameter."""
        # Don't cache when mix_examples is False since we need different strategies
        # for EXAMPLES phase vs fuzzing/stateful phases
        use_cache = extra_data_source is None and mix_examples
        feedback_generation = error_feedback.generation if error_feedback is not None else None

        # Check cache based on generation mode (only when extra data sources are not used)
        if use_cache:
            if generation_mode == GenerationMode.POSITIVE:
                cached = self._positive_strategy_cache
                if cached is not NOT_SET and not isinstance(cached, NotSet):
                    cached_strategy, cached_generation = cached
                    if cached_generation == feedback_generation:
                        return cached_strategy
            else:
                cached = self._negative_strategy_cache
                if cached is not NOT_SET and not isinstance(cached, NotSet):
                    cached_strategy, cached_generation = cached
                    if cached_generation == feedback_generation:
                        return cached_strategy

        # Import here to avoid circular dependency
        from schemathesis.specs.openapi._hypothesis import GENERATOR_MODE_TO_STRATEGY_FACTORY
        from schemathesis.specs.openapi.schemas import OpenApiSchema

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
        assert isinstance(operation.schema, OpenApiSchema)
        # Reuse the precomputed target walk recipes when the strategy is generating against
        # `optimized_schema` directly (no error-feedback adjustment fired).
        target_descriptors = (
            self.mutation_targets if generation_mode.is_negative and schema is self.optimized_schema else None
        )
        strategy = strategy_factory(
            schema,
            operation.label,
            ParameterLocation.BODY,
            self.media_type,
            generation_config,
            operation.schema.adapter.jsonschema_validator_cls,
            self.name_to_uri,
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

        # Apply hybrid approach when captured variants are available
        if captured_variants and usage_tracker is not None:
            if generation_mode.is_negative:
                strategy = self._build_negative_aware_strategy(
                    operation, generation_config, captured_variants, usage_tracker
                )
            else:
                strategy = build_hybrid_strategy(strategy, captured_variants, usage_tracker)

        # Cache the strategy keyed by feedback generation
        if use_cache:
            slot = (strategy, feedback_generation)
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
    ) -> st.SearchStrategy:
        """Build strategy for negative mode when captured values are available."""
        from hypothesis import strategies as st

        from schemathesis.specs.openapi.negative import GeneratedValue

        positive_strategy = self.get_strategy(
            operation, generation_config, GenerationMode.POSITIVE, extra_data_source=None
        )
        positive_strategy = build_hybrid_strategy(positive_strategy, captured_variants, usage_tracker)
        # The hybrid strategy already wraps in `GeneratedValue` when it picks a captured pool
        # variant (so pool-draw provenance survives). Wrap only the un-wrapped values here.
        positive_strategy = positive_strategy.map(
            lambda x: x if isinstance(x, GeneratedValue) else GeneratedValue(x, None)
        )

        negative_strategy = self.get_strategy(
            operation, generation_config, GenerationMode.NEGATIVE, extra_data_source=None
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
    # `type: array` + `enum: [item-strings]` + `items` is a contradictory schema (likely a codegen artifact).
    # Drop the top-level enum only when enum values are scalars, not arrays themselves.
    if (
        schema.get("type") == "array"
        and "enum" in schema
        and "items" in schema
        and all(not isinstance(v, list) for v in schema["enum"])
    ):
        del schema["enum"]
    return schema


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

    raw_name = reference.rsplit("/", maxsplit=1)[1]
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

    __slots__ = ("items", "location", "adapter", "_schema", "_schema_cache", "_strategy_cache", "_strict_validator")

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
        self._schema_cache: dict[frozenset[str], dict[str, Any]] = {}
        self._strategy_cache: dict[tuple[frozenset[str], GenerationMode, int | None], st.SearchStrategy] = {}
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
        operation: APIOperation,
        generation_config: GenerationConfig,
        generation_mode: GenerationMode,
        exclude: Iterable[str] = (),
        extra_data_source: ExtraDataSource | None = None,
        mix_examples: bool = True,
        error_feedback: ErrorFeedbackStore | None = None,
    ) -> st.SearchStrategy:
        """Get a Hypothesis strategy for this parameter set with specified exclusions."""
        exclude_key = _EMPTY_EXCLUDE_KEY if not exclude else frozenset(exclude)
        feedback_generation = error_feedback.generation if error_feedback is not None else None
        cache_key = (exclude_key, generation_mode, feedback_generation)

        use_cache = extra_data_source is None and mix_examples

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
        from schemathesis.specs.openapi.negative import GeneratedValue
        from schemathesis.specs.openapi.schemas import OpenApiSchema

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
            assert isinstance(operation.schema, OpenApiSchema)
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

            # Bias path parameter integers toward positive values in positive mode
            if (
                self.location == ParameterLocation.PATH
                and not is_negative
                and _schema_has_integer_properties(schema_obj)
            ):
                strategy = build_positive_biased_path_strategy(strategy)

            explicit_intent_path_names: frozenset[str] = frozenset()
            if self.location == ParameterLocation.PATH:
                explicit_intent_path_names = _get_explicit_intent_path_names(parameters=self.items)

            serialize = operation.get_parameter_serializer(self.location)
            if serialize is not None:
                if is_negative:
                    # Apply serialize only to the value part of GeneratedValue
                    strategy = strategy.map(lambda x: GeneratedValue(serialize(x.value), x.meta, x.pool_draws))
                else:
                    strategy = strategy.map(serialize)

            # Path & query parameters will be cast to string anyway, but having their JSON equivalents for
            # `True` / `False` / `None` improves chances of them passing validation in apps
            # that expect boolean / null types
            # and not aware of Python-specific representation of those types
            if self.location == ParameterLocation.PATH:
                if is_negative:
                    strategy = strategy.map(
                        lambda x: GeneratedValue(
                            _quote_all_safe(jsonify_python_specific_types(x.value)), x.meta, x.pool_draws
                        )
                    )
                    # Keep strict anti-misrouting defaults for negative generation.
                    # Explicit %2F allowances apply only to positive data.
                    strategy = strategy.filter(lambda x: is_valid_path(x.value))
                else:
                    strategy = strategy.map(_quote_all_safe).map(jsonify_python_specific_types)
                    strategy = strategy.filter(
                        lambda x, allow=explicit_intent_path_names: is_valid_path(x, allow_encoded_slash_for=allow)
                    )
            elif self.location == ParameterLocation.QUERY:
                query_filter = is_valid_query
                if is_negative:
                    strategy = strategy.filter(lambda x: query_filter(x.value))
                else:
                    strategy = strategy.filter(query_filter)
                if is_negative:
                    strategy = strategy.map(
                        lambda x: GeneratedValue(jsonify_python_specific_types(x.value), x.meta, x.pool_draws)
                    )
                else:
                    strategy = strategy.map(jsonify_python_specific_types)
            else:
                header_filter = is_valid_header
                # Headers with special format do not need filtration
                if not (self.location.is_in_header and _can_skip_header_filter(schema)):
                    if is_negative:
                        strategy = strategy.filter(lambda x: header_filter(x.value))
                    else:
                        strategy = strategy.filter(header_filter)

        # Apply hybrid approach when captured variants are available
        if captured_variants and usage_tracker is not None:
            if generation_mode.is_negative:
                # In negative mode with captured values, mostly use positive strategy
                # to leverage valuable captured IDs for testing deeper application logic
                strategy = self._build_negative_aware_strategy(
                    operation, generation_config, exclude, captured_variants, usage_tracker
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
    ) -> st.SearchStrategy:
        """Build strategy for negative mode when captured values are available.

        Mostly uses positive strategy with captured values (97%) to test deeper
        application logic, with occasional negative tests (3%).
        """
        from hypothesis import strategies as st

        from schemathesis.specs.openapi.negative import GeneratedValue

        # Get positive strategy with hybrid approach
        positive_strategy = self.get_strategy(
            operation, generation_config, GenerationMode.POSITIVE, exclude, extra_data_source=None
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
            operation, generation_config, GenerationMode.NEGATIVE, exclude, extra_data_source=None
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
