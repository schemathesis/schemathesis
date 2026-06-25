from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

import jsonschema_rs
from hypothesis import event, note, reject
from hypothesis import strategies as st
from hypothesis_jsonschema import from_schema
from requests.structures import CaseInsensitiveDict

from schemathesis import auths
from schemathesis.config import GenerationConfig
from schemathesis.core import NOT_SET, media_types
from schemathesis.core.cache import MISSING
from schemathesis.core.control import SkipTest
from schemathesis.core.error_feedback import ErrorFeedbackStore, ObservationKind
from schemathesis.core.errors import (
    SERIALIZERS_SUGGESTION_MESSAGE,
    InvalidSchema,
    MalformedMediaType,
    SerializationNotPossible,
)
from schemathesis.core.jsonschema.numeric import (
    bounds_are_unsatisfiable,
    is_numeric_bound,
    next_float32,
    resolve_inclusive_bounds,
)
from schemathesis.core.jsonschema.types import JsonSchema
from schemathesis.core.media_types import FORM_MEDIA_TYPES, find_media_type_strategy
from schemathesis.core.parameters import ParameterLocation
from schemathesis.core.timing import Instant
from schemathesis.core.transforms import deepclone
from schemathesis.core.transport import prepare_urlencoded
from schemathesis.generation import GenerationMode
from schemathesis.generation.hypothesis import custom_formats_cache
from schemathesis.generation.meta import (
    CaseMetadata,
    ComponentInfo,
    ExamplesPhaseData,
    FuzzingPhaseData,
    GenerationInfo,
    PhaseInfo,
    StatefulPhaseData,
    TestPhase,
)
from schemathesis.generation.value import GeneratedValue
from schemathesis.hooks import HookContext, HookDispatcher, apply_to_all_dispatchers
from schemathesis.openapi.generation.filters import is_valid_urlencoded
from schemathesis.resources import ExtraDataSource, PoolDraw, SemanticDraw
from schemathesis.schemas import APIOperation
from schemathesis.specs.openapi.adapter.parameters import OpenApiBody, OpenApiParameterSet
from schemathesis.specs.openapi.formats import (
    DEFAULT_HEADER_EXCLUDE_CHARACTERS,
    HEADER_FORMAT,
    INVALID_HEADER_CHARS,
    STRING_FORMATS,
    get_default_format_strategies,
    header_values,
)
from schemathesis.specs.openapi.headers import KNOWN_HEADER_FORMATS, get_header_format_strategies
from schemathesis.specs.openapi.negative import (
    negative_schema,
    wrap_filter_hook_for_generated_value,
    wrap_flatmap_hook_for_generated_value,
    wrap_map_hook_for_generated_value,
)
from schemathesis.specs.openapi.negative.mutations import MutationMetadata
from schemathesis.specs.openapi.negative.utils import can_negate, is_binary_format
from schemathesis.transport.serialization import quote_all

if TYPE_CHECKING:
    from schemathesis.generation.dictionaries import DictionaryDraw
    from schemathesis.specs.openapi.schemas import OpenApiOperation

SLASH = "/"
# Probability of generating valid headers in negative mode
VALID_HEADER_PROBABILITY = 0.95
_PLAIN_HEADER_FORMATS = {HEADER_FORMAT} | set(KNOWN_HEADER_FORMATS.values())
# Strategies that take no varying input are deterministic and reusable; allocating
# them once at import avoids ~300–600ns of fresh `LazyStrategy` construction per call.
_NONE_STRATEGY: st.SearchStrategy = st.none()
_JUST_NOT_SET: st.SearchStrategy = st.just(NOT_SET)
_LOCATION_NAME_TO_ENUM: dict[str, ParameterLocation] = {
    "query": ParameterLocation.QUERY,
    "path": ParameterLocation.PATH,
    "header": ParameterLocation.HEADER,
    "cookie": ParameterLocation.COOKIE,
    "body": ParameterLocation.BODY,
}
StrategyFactory = Callable[
    [JsonSchema, str, ParameterLocation, str | None, GenerationConfig, type[jsonschema_rs.Validator]],
    st.SearchStrategy,
]


def _draw(draw: st.DrawFn, strategy: st.SearchStrategy, operation: APIOperation) -> Any:
    try:
        return draw(strategy)
    except jsonschema_rs.ValidationError as exc:
        raise InvalidSchema.from_jsonschema_error(
            exc,
            path=operation.path,
            method=operation.method,
            config=operation.schema.config.output,
        ) from None


@st.composite  # type: ignore[untyped-decorator]
def openapi_cases(
    draw: st.DrawFn,
    *,
    operation: APIOperation,
    hooks: HookDispatcher | None = None,
    auth_storage: auths.AuthStorage | None = None,
    generation_mode: GenerationMode = GenerationMode.POSITIVE,
    path_parameters: dict[str, Any] | None = None,
    headers: dict[str, Any] | None = None,
    cookies: dict[str, Any] | None = None,
    query: dict[str, Any] | None = None,
    body: Any = NOT_SET,
    media_type: str | None = None,
    phase: TestPhase = TestPhase.FUZZING,
    extra_data_source: ExtraDataSource | None = None,
    error_feedback: ErrorFeedbackStore | None = None,
) -> Any:
    """A strategy that creates `Case` instances.

    Explicit `path_parameters`, `headers`, `cookies`, `query`, `body` arguments will be used in the resulting `Case`
    object.

    If such explicit parameters are composite (not `body`) and don't provide the whole set of parameters for that
    location, then we generate what is missing and merge these two parts. Note that if parameters are optional, then
    they may remain absent.

    The primary purpose of this behavior is to prevent sending incomplete explicit examples by generating missing parts
    as it works with `body`.
    """
    started_at = Instant()

    generation_config = operation.schema.config.generation_for(operation=operation, phase=phase.value)

    ctx = HookContext(operation=operation)

    # Don't mix in schema examples during EXAMPLES phase - they're handled separately there
    mix_examples = phase != TestPhase.EXAMPLES

    path_parameters_ = generate_parameter(
        ParameterLocation.PATH,
        path_parameters,
        operation,
        draw,
        ctx,
        hooks,
        generation_mode,
        generation_config,
        extra_data_source=extra_data_source,
        error_feedback=error_feedback,
        mix_examples=mix_examples,
    )
    headers_ = generate_parameter(
        ParameterLocation.HEADER,
        headers,
        operation,
        draw,
        ctx,
        hooks,
        generation_mode,
        generation_config,
        extra_data_source=extra_data_source,
        error_feedback=error_feedback,
        mix_examples=mix_examples,
    )
    cookies_ = generate_parameter(
        ParameterLocation.COOKIE,
        cookies,
        operation,
        draw,
        ctx,
        hooks,
        generation_mode,
        generation_config,
        extra_data_source=extra_data_source,
        error_feedback=error_feedback,
        mix_examples=mix_examples,
    )
    query_ = generate_parameter(
        ParameterLocation.QUERY,
        query,
        operation,
        draw,
        ctx,
        hooks,
        generation_mode,
        generation_config,
        extra_data_source=extra_data_source,
        error_feedback=error_feedback,
        mix_examples=mix_examples,
    )

    if body is NOT_SET:
        if operation.body:
            body_generator = generation_mode
            if generation_mode.is_negative:
                # Consider only schemas that are possible to negate
                candidates = [item for item in operation.body.items if item.is_negatable]
                # Not possible to negate body, fallback to positive data generation
                if not candidates:
                    candidates = operation.body.items
                    body_generator = GenerationMode.POSITIVE
            else:
                candidates = operation.body.items
            parameter = draw(st.sampled_from(candidates))
            strategy = _get_body_strategy(
                parameter,
                operation,
                generation_config,
                draw,
                body_generator,
                extra_data_source=extra_data_source,
                error_feedback=error_feedback,
                mix_examples=mix_examples,
            )
            strategy = apply_hooks(operation, ctx, hooks, strategy, ParameterLocation.BODY)
            # Parameter may have a wildcard media type. In this case, choose any supported one
            try:
                possible_media_types = sorted(
                    operation.schema.transport.get_matching_media_types(parameter.media_type), key=lambda x: x[0]
                )
            except MalformedMediaType as exc:
                raise InvalidSchema.from_malformed_media_type(
                    exc, parameter.media_type, path=operation.path, method=operation.method
                ) from exc
            if not possible_media_types:
                all_media_types = operation.get_request_payload_content_types()
                if all(
                    operation.schema.transport.get_first_matching_media_type(media_type) is None
                    for media_type in all_media_types
                ):
                    # None of media types defined for this operation are not supported
                    raise SerializationNotPossible.from_media_types(*all_media_types) from None
                # Other media types are possible - avoid choosing this media type in the future
                event_text = f"Can't serialize data to `{parameter.media_type}`."
                note(f"{event_text} {SERIALIZERS_SUGGESTION_MESSAGE}")
                event(event_text)
                reject()
            media_type, _ = draw(st.sampled_from(possible_media_types))
            if media_types.is_form_urlencoded(media_type):
                # Helper to transform FormBodyWithContentTypes while preserving it
                def prepare_urlencoded_form(x: Any) -> Any:
                    if isinstance(x, FormBodyWithContentTypes):
                        return FormBodyWithContentTypes(body=prepare_urlencoded(x.body), content_types=x.content_types)
                    return prepare_urlencoded(x)

                def is_valid_urlencoded_form(x: Any) -> bool:
                    if isinstance(x, FormBodyWithContentTypes):
                        return is_valid_urlencoded(x.body)
                    return is_valid_urlencoded(x)

                # The hybrid strategy wraps in `GeneratedValue` when it picks a captured pool
                # variant, so both positive and negative paths must unwrap before
                # transforming/filtering and rewrap to keep `pool_draws` flowing.
                strategy = strategy.map(
                    lambda x: (
                        GeneratedValue(
                            prepare_urlencoded_form(x.value),
                            x.meta,
                            x.pool_draws,
                            x.semantic_draws,
                            x.dictionary_draws,
                        )
                        if isinstance(x, GeneratedValue)
                        else prepare_urlencoded_form(x)
                    )
                ).filter(lambda x: is_valid_urlencoded_form(x.value if isinstance(x, GeneratedValue) else x))
            body_result = _draw(draw, strategy, operation)
            body_metadata = None
            body_pool_draws: tuple[PoolDraw, ...] = ()
            body_semantic_draws: tuple[SemanticDraw, ...] = ()
            body_dictionary_draws: tuple[DictionaryDraw, ...] = ()
            # Negative strategy returns GeneratedValue, positive returns just value
            if isinstance(body_result, GeneratedValue):
                body_metadata = body_result.meta
                body_pool_draws = body_result.pool_draws
                body_semantic_draws = body_result.semantic_draws
                body_dictionary_draws = body_result.dictionary_draws
                body_result = body_result.value
            body_ = ValueContainer(
                value=body_result,
                location="body",
                generator=body_generator,
                meta=body_metadata,
                pool_draws=body_pool_draws,
                semantic_draws=body_semantic_draws,
                dictionary_draws=body_dictionary_draws,
            )
        else:
            body_ = ValueContainer(value=body, location="body", generator=None, meta=None)
    else:
        # This explicit body payload comes for a media type that has a custom strategy registered
        # Such strategies only support binary payloads, otherwise they can't be serialized
        if not isinstance(body, bytes) and media_type and find_media_type_strategy(media_type) is not None:
            all_media_types = operation.get_request_payload_content_types()
            raise SerializationNotPossible.from_media_types(*all_media_types)
        body_ = ValueContainer(value=body, location="body", generator=None, meta=None)

    # If we need to generate negative cases but no generated values were negated, then skip the whole test
    if generation_mode.is_negative and not any_negated_values([query_, cookies_, headers_, path_parameters_, body_]):
        if generation_config.modes == [GenerationMode.NEGATIVE]:
            raise SkipTest("Impossible to generate negative test cases")
        else:
            reject()

    # A schema-invalid dictionary draw carries negative content even when no mutator
    # produced surviving metadata (the overlay strips mutations for overwritten parameters).
    first_invalid_dictionary_draw: DictionaryDraw | None = next(
        (
            draw
            for container in (query_, cookies_, headers_, path_parameters_, body_)
            for draw in container.dictionary_draws
            if not draw.matches_schema
        ),
        None,
    )

    effective_generation_mode = generation_mode
    if (
        generation_mode.is_negative
        and not any(
            container.generator == GenerationMode.NEGATIVE and container.meta is not None
            for container in [query_, cookies_, headers_, path_parameters_, body_]
            if container.is_generated
        )
        and first_invalid_dictionary_draw is None
    ):
        effective_generation_mode = GenerationMode.POSITIVE

    # Extract mutation metadata from negated values and create phase-appropriate data
    if effective_generation_mode.is_negative:
        negated_container = None
        for container in [query_, cookies_, headers_, path_parameters_, body_]:
            if container.generator == GenerationMode.NEGATIVE and container.meta is not None:
                negated_container = container
                break

        if negated_container and negated_container.meta:
            metadata = negated_container.meta
            parameter_location = _LOCATION_NAME_TO_ENUM.get(negated_container.location)
            _phase_data = {
                TestPhase.EXAMPLES: ExamplesPhaseData(
                    description=metadata.description,
                    parameter=metadata.parameter,
                    parameter_location=parameter_location,
                    location=metadata.location,
                    mutations=metadata.mutations,
                ),
                TestPhase.FUZZING: FuzzingPhaseData(
                    description=metadata.description,
                    parameter=metadata.parameter,
                    parameter_location=parameter_location,
                    location=metadata.location,
                    mutations=metadata.mutations,
                ),
                TestPhase.STATEFUL: StatefulPhaseData(
                    description=metadata.description,
                    parameter=metadata.parameter,
                    parameter_location=parameter_location,
                    location=metadata.location,
                    mutations=metadata.mutations,
                ),
            }[phase]
            phase_data = cast(ExamplesPhaseData | FuzzingPhaseData | StatefulPhaseData, _phase_data)
        elif first_invalid_dictionary_draw is not None:
            draw = first_invalid_dictionary_draw
            description = f"Dictionary `{draw.dictionary}` entry violates the schema for `{draw.parameter_name}`"
            parameter_location = _LOCATION_NAME_TO_ENUM.get(draw.parameter_location)
            _phase_data = {
                TestPhase.EXAMPLES: ExamplesPhaseData(
                    description=description,
                    parameter=draw.parameter_name,
                    parameter_location=parameter_location,
                    location=None,
                ),
                TestPhase.FUZZING: FuzzingPhaseData(
                    description=description,
                    parameter=draw.parameter_name,
                    parameter_location=parameter_location,
                    location=None,
                ),
                TestPhase.STATEFUL: StatefulPhaseData(
                    description=description,
                    parameter=draw.parameter_name,
                    parameter_location=parameter_location,
                    location=None,
                ),
            }[phase]
            phase_data = cast(ExamplesPhaseData | FuzzingPhaseData | StatefulPhaseData, _phase_data)
        else:
            _phase_data = {
                TestPhase.EXAMPLES: ExamplesPhaseData(
                    description="Schema mutated",
                    parameter=None,
                    parameter_location=None,
                    location=None,
                ),
                TestPhase.FUZZING: FuzzingPhaseData(
                    description="Schema mutated",
                    parameter=None,
                    parameter_location=None,
                    location=None,
                ),
                TestPhase.STATEFUL: StatefulPhaseData(
                    description="Schema mutated",
                    parameter=None,
                    parameter_location=None,
                    location=None,
                ),
            }[phase]
            phase_data = cast(ExamplesPhaseData | FuzzingPhaseData | StatefulPhaseData, _phase_data)
    else:
        _phase_data = {
            TestPhase.EXAMPLES: ExamplesPhaseData(
                description="Positive test case",
                parameter=None,
                parameter_location=None,
                location=None,
            ),
            TestPhase.FUZZING: FuzzingPhaseData(
                description="Positive test case",
                parameter=None,
                parameter_location=None,
                location=None,
            ),
            TestPhase.STATEFUL: StatefulPhaseData(
                description="Positive test case",
                parameter=None,
                parameter_location=None,
                location=None,
            ),
        }[phase]
        phase_data = cast(ExamplesPhaseData | FuzzingPhaseData | StatefulPhaseData, _phase_data)

    # Extract body and content types if using form encoding
    body_value = body_.value
    multipart_content_types = None
    if isinstance(body_value, FormBodyWithContentTypes):
        multipart_content_types = body_value.content_types
        body_value = body_value.body

    pool_draws = tuple(
        draw for container in (query_, path_parameters_, headers_, cookies_, body_) for draw in container.pool_draws
    )
    semantic_draws = tuple(
        draw for container in (query_, path_parameters_, headers_, cookies_, body_) for draw in container.semantic_draws
    )
    dictionary_draws = tuple(
        draw
        for container in (query_, path_parameters_, headers_, cookies_, body_)
        for draw in container.dictionary_draws
    )
    instance = operation.Case(
        media_type=media_type,
        path_parameters=path_parameters_.value or {},
        headers=headers_.value or CaseInsensitiveDict(),
        cookies=cookies_.value or {},
        query=query_.value or {},
        body=body_value,
        multipart_content_types=multipart_content_types,
        _meta=CaseMetadata(
            generation=GenerationInfo(
                time=started_at.elapsed,
                mode=effective_generation_mode,
            ),
            phase=PhaseInfo(name=phase, data=phase_data),
            components={
                kind: ComponentInfo(mode=value.generator)
                for kind, value in [
                    (ParameterLocation.QUERY, query_),
                    (ParameterLocation.PATH, path_parameters_),
                    (ParameterLocation.HEADER, headers_),
                    (ParameterLocation.COOKIE, cookies_),
                    (ParameterLocation.BODY, body_),
                ]
                if value.generator is not None
            },
            pool_draws=pool_draws,
            semantic_draws=semantic_draws,
            dictionary_draws=dictionary_draws,
        ),
    )
    auth_context = auths.AuthContext(
        operation=operation,
        app=operation.app,
    )
    auths.set_on_case(instance, auth_context, auth_storage)
    return instance


OPTIONAL_BODY_RATE = 0.05


@dataclass(slots=True)
class FormBodyWithContentTypes:
    """Form body data with selected content types for properties."""

    body: dict[str, Any]
    content_types: dict[str, str]  # property_name -> selected content type


def _body_required_per_feedback(operation: APIOperation, error_feedback: ErrorFeedbackStore | None) -> bool:
    """Return True when the server reported the body itself as missing for this operation."""
    if error_feedback is None:
        return False
    # Any body-location `must not be blank` — body-level or field-level — implies the body
    # itself is required, since omitting it would have produced a different error.
    for observation in error_feedback.observations(operation_label=operation.label, location=ParameterLocation.BODY):
        if observation.kind == ObservationKind.MUST_NOT_BE_BLANK:
            return True
    return False


def _maybe_set_optional_body(
    strategy: st.SearchStrategy,
    parameter: OpenApiBody,
    operation: APIOperation,
    draw: st.DrawFn,
    error_feedback: ErrorFeedbackStore | None,
) -> st.SearchStrategy:
    """Add NOT_SET option to strategy for optional body parameters."""
    if _body_required_per_feedback(operation, error_feedback):
        return strategy
    if (
        not parameter.is_required
        and draw(st.floats(min_value=0.0, max_value=1.0, allow_infinity=False, allow_nan=False, allow_subnormal=False))
        < OPTIONAL_BODY_RATE
    ):
        strategy |= _JUST_NOT_SET
    return strategy


def _build_form_strategy_with_encoding(
    parameter: OpenApiBody,
    operation: OpenApiOperation,
    generation_config: GenerationConfig,
    generation_mode: GenerationMode,
) -> st.SearchStrategy | None:
    """Build a strategy for form bodies that have custom encoding contentType.

    Supports wildcard media type matching (e.g., "image/*" matches "image/png").

    Returns `None` if no custom encoding with registered strategies or comma-separated content types is found.
    """
    schema = parameter.optimized_schema
    if not isinstance(schema, dict) or schema.get("type") != "object":
        return None

    properties = schema.get("properties", {})
    if not properties:
        return None

    # Maps property_name to strategy returning (content_type, data) tuple
    property_with_content_type_strategies: dict[str, st.SearchStrategy] = {}
    # Maps property_name to list of content types (for comma-separated without custom strategy)
    property_content_type_selections: dict[str, list[str]] = {}

    for property_name in properties:
        raw_content_type = parameter.get_property_content_type(property_name)

        # contentType can be a string (or comma-separated list) or an array of strings per the spec
        content_types: list[str] = []
        if isinstance(raw_content_type, str):
            content_types = [ct.strip() for ct in raw_content_type.split(",")]
        elif isinstance(raw_content_type, list):
            content_types = [ct.strip() for ct in raw_content_type if isinstance(ct, str)]

        if content_types:
            strategies_for_types = []
            for ct in content_types:
                strategy = find_media_type_strategy(ct)
                if strategy is not None:
                    # Pair strategy with its content type so we know which was selected
                    strategies_for_types.append(st.tuples(st.just(ct), strategy))

            if strategies_for_types:
                # In negative mode with binary format, custom strategies always produce valid data
                # Skip them to allow structural mutations instead
                if generation_mode.is_negative:
                    prop_schema = properties.get(property_name, {})
                    if is_binary_format(prop_schema):
                        # Skip custom strategy but still select content type if multiple
                        if len(content_types) > 1:
                            property_content_type_selections[property_name] = content_types
                        continue
                # Store strategy that returns (content_type, data) tuple
                property_with_content_type_strategies[property_name] = st.one_of(*strategies_for_types)
            elif len(content_types) > 1:
                # No custom strategy found, but multiple content types specified
                # Store them for random selection
                property_content_type_selections[property_name] = content_types

    if not property_with_content_type_strategies and not property_content_type_selections:
        return None

    # Build strategies for properties
    property_strategies = {}
    for property_name, subschema in properties.items():
        if property_name in property_with_content_type_strategies:
            # This property has custom content type - will be handled separately
            continue
        else:
            strategy_factory = GENERATOR_MODE_TO_STRATEGY_FACTORY[generation_mode]
            property_strategies[property_name] = strategy_factory(
                subschema,
                operation.label,
                ParameterLocation.BODY,
                parameter.media_type,
                generation_config,
                operation.schema.adapter.jsonschema_validator_cls,
            )

    # Build fixed dictionary strategy with optional properties
    required = set(schema.get("required", []))
    required_strategies = {k: v for k, v in property_strategies.items() if k in required}
    optional_strategies = {k: _JUST_NOT_SET | v for k, v in property_strategies.items() if k not in required}

    def _unwrap(value: Any) -> Any:
        return value.value if isinstance(value, GeneratedValue) else value

    @st.composite  # type: ignore[untyped-decorator]
    def build_body(draw: st.DrawFn) -> FormBodyWithContentTypes:
        body: dict[str, Any] = {}
        selected_content_types: dict[str, str] = {}

        # Generate required properties
        for key, strategy in required_strategies.items():
            body[key] = _unwrap(draw(strategy))
        # Generate optional properties, filtering out NOT_SET
        for key, strategy in optional_strategies.items():
            value = _unwrap(draw(strategy))
            if value is not NOT_SET:
                body[key] = value

        # Generate properties with content type strategies (respecting optional)
        for property_name, ct_strategy in property_with_content_type_strategies.items():
            if property_name in required:
                # Required - always generate
                content_type, data = draw(ct_strategy)
                body[property_name] = data
                selected_content_types[property_name] = content_type
            else:
                # Optional - may omit
                should_include = draw(st.booleans())
                if should_include:
                    content_type, data = draw(ct_strategy)
                    body[property_name] = data
                    selected_content_types[property_name] = content_type

        # For properties with comma-separated content types (but no custom strategy),
        # randomly select one of the content types
        for property_name, content_type_list in property_content_type_selections.items():
            selected_content_types[property_name] = draw(st.sampled_from(content_type_list))

        return FormBodyWithContentTypes(body=body, content_types=selected_content_types)

    return build_body()


def _get_body_strategy(
    parameter: OpenApiBody,
    operation: APIOperation,
    generation_config: GenerationConfig,
    draw: st.DrawFn,
    generation_mode: GenerationMode,
    extra_data_source: ExtraDataSource | None = None,
    mix_examples: bool = True,
    error_feedback: ErrorFeedbackStore | None = None,
) -> st.SearchStrategy:
    # Check for custom encoding in form bodies (multipart/form-data or application/x-www-form-urlencoded)
    if parameter.media_type in FORM_MEDIA_TYPES:
        custom_strategy = _build_form_strategy_with_encoding(parameter, operation, generation_config, generation_mode)
        if custom_strategy is not None:
            return custom_strategy

    # Check for custom media type strategy
    custom_strategy = find_media_type_strategy(parameter.media_type)
    if custom_strategy is not None:
        # Always use custom strategies for raw bodies - they produce transmittable bytes.
        # In negative mode, bypassing them would generate non-bytes values (e.g., integers)
        # that can't be sent over HTTP for raw binary media types like application/x-tar.
        return custom_strategy

    # Use the cached strategy from the parameter
    strategy = parameter.get_strategy(
        operation,
        generation_config,
        generation_mode,
        extra_data_source=extra_data_source,
        error_feedback=error_feedback,
        mix_examples=mix_examples,
    )
    return _maybe_set_optional_body(strategy, parameter, operation, draw, error_feedback)


def get_parameters_value(
    value: dict[str, Any] | None,
    location: ParameterLocation,
    draw: st.DrawFn,
    operation: APIOperation,
    ctx: HookContext,
    hooks: HookDispatcher | None,
    generation_mode: GenerationMode,
    generation_config: GenerationConfig,
    extra_data_source: ExtraDataSource | None = None,
    mix_examples: bool = True,
    error_feedback: ErrorFeedbackStore | None = None,
) -> GeneratedValue:
    """Get the final value for the specified location.

    If the value is not set, then generate it from the relevant strategy. Otherwise, check what is missing in it and
    generate those parts.
    """
    if value is None:
        strategy = get_parameters_strategy(
            operation,
            generation_mode,
            location,
            generation_config,
            extra_data_source=extra_data_source,
            mix_examples=mix_examples,
            error_feedback=error_feedback,
        )
        strategy = apply_hooks(operation, ctx, hooks, strategy, location)
        result = _draw(draw, strategy, operation)
        # Negative strategy returns GeneratedValue, positive returns just value
        if isinstance(result, GeneratedValue):
            return result
        return GeneratedValue(value=result, meta=None)
    strategy = get_parameters_strategy(
        operation,
        generation_mode,
        location,
        generation_config,
        exclude=value.keys(),
        extra_data_source=extra_data_source,
        error_feedback=error_feedback,
        mix_examples=mix_examples,
    )
    strategy = apply_hooks(operation, ctx, hooks, strategy, location)
    new = _draw(draw, strategy, operation)
    if isinstance(new, GeneratedValue):
        meta = new.meta
        pool_draws = new.pool_draws
        semantic_draws = new.semantic_draws
        dictionary_draws = new.dictionary_draws
        new = new.value
    else:
        meta = None
        pool_draws = ()
        semantic_draws = ()
        dictionary_draws = ()
    if new is not None:
        copied = dict(value)
        copied.update(new)
        return GeneratedValue(
            value=copied,
            meta=meta,
            pool_draws=pool_draws,
            semantic_draws=semantic_draws,
            dictionary_draws=dictionary_draws,
        )
    return GeneratedValue(
        value=value,
        meta=meta,
        pool_draws=pool_draws,
        semantic_draws=semantic_draws,
        dictionary_draws=dictionary_draws,
    )


@dataclass(slots=True)
class ValueContainer:
    """Container for a value generated by a data generator or explicitly provided."""

    value: Any
    location: str
    generator: GenerationMode | None
    meta: MutationMetadata | None
    pool_draws: tuple[PoolDraw, ...] = ()
    semantic_draws: tuple[SemanticDraw, ...] = ()
    dictionary_draws: tuple[DictionaryDraw, ...] = ()

    @property
    def is_generated(self) -> bool:
        """If value was generated."""
        return self.generator is not None and (self.location == "body" or self.value is not None)


def any_negated_values(values: list[ValueContainer]) -> bool:
    """Check if any generated values are negated."""
    return any(value.generator == GenerationMode.NEGATIVE for value in values if value.is_generated)


# Percent-encoded backslash, control chars (0x00-0x1F), and DEL — what strict URL decoders
# (Tomcat, common WAFs) reject before routing. After `quote_all`, every occurrence in the
# encoded string represents a raw unsafe byte, so the replacement is safe regardless of source.
_UNSAFE_PATH_PERCENT = re.compile(r"%(?:5[Cc]|[01][0-9A-Fa-f]|7[Ff])")


def _strip_path_decoder_unsafe(value: dict[str, Any]) -> dict[str, Any]:
    for key, raw in value.items():
        if isinstance(raw, str):
            value[key] = _UNSAFE_PATH_PERCENT.sub("_", raw)
    return value


def generate_parameter(
    location: ParameterLocation,
    explicit: dict[str, Any] | None,
    operation: APIOperation,
    draw: st.DrawFn,
    ctx: HookContext,
    hooks: HookDispatcher | None,
    generator: GenerationMode,
    generation_config: GenerationConfig,
    extra_data_source: ExtraDataSource | None = None,
    mix_examples: bool = True,
    error_feedback: ErrorFeedbackStore | None = None,
) -> ValueContainer:
    """Generate a value for a parameter.

    Fallback to positive data generator if parameter can not be negated.
    """
    if generator.is_negative and (
        (location == ParameterLocation.PATH and not can_negate_path_parameters(operation))
        or (location.is_in_header and not can_negate_headers(operation, location))
    ):
        # If we can't negate any parameter, generate positive ones
        # If nothing else will be negated, then skip the test completely
        generator = GenerationMode.POSITIVE
    generated = get_parameters_value(
        explicit,
        location,
        draw,
        operation,
        ctx,
        hooks,
        generator,
        generation_config,
        extra_data_source=extra_data_source,
        error_feedback=error_feedback,
        mix_examples=mix_examples,
    )
    value = generated.value
    if value is not None and location == ParameterLocation.PATH:
        value = quote_all(value)
        if operation.schema._probe_state.path_decoder_strict:
            value = _strip_path_decoder_unsafe(value)

    used_generator: GenerationMode | None = generator
    if value == explicit:
        # When we pass `explicit`, then its parts are excluded from generation of the final value
        # If the final value is the same, then other parameters were generated at all
        used_generator = None
    return ValueContainer(
        value=value,
        location=location,
        generator=used_generator,
        meta=generated.meta,
        pool_draws=generated.pool_draws,
        semantic_draws=generated.semantic_draws,
        dictionary_draws=generated.dictionary_draws,
    )


def can_negate_path_parameters(operation: APIOperation) -> bool:
    """Check if any path parameter can be negated."""
    # No path parameters to negate
    parameters = cast(OpenApiParameterSet, operation.path_parameters).schema["properties"]
    if not parameters:
        return True
    return any(can_negate(parameter) for parameter in parameters.values())


def can_negate_headers(operation: APIOperation, location: ParameterLocation) -> bool:
    """Check if any header can be negated."""
    container = getattr(operation, location.container_name)
    # No headers to negate
    headers = container.schema["properties"]
    if not headers:
        return True
    plain = ({"type": "string"}, *({"type": "string", "format": f} for f in _PLAIN_HEADER_FORMATS))
    return any(header not in plain for header in headers.values())


def get_parameters_strategy(
    operation: APIOperation,
    generation_mode: GenerationMode,
    location: ParameterLocation,
    generation_config: GenerationConfig,
    exclude: Iterable[str] = (),
    extra_data_source: ExtraDataSource | None = None,
    mix_examples: bool = True,
    error_feedback: ErrorFeedbackStore | None = None,
) -> st.SearchStrategy:
    """Create a new strategy for the case's component from the API operation parameters."""
    container = getattr(operation, location.container_name)
    # Direct list bool check skips ParameterSet.__len__ method dispatch.
    if container.items:
        return container.get_strategy(
            operation,
            generation_config,
            generation_mode,
            exclude,
            extra_data_source=extra_data_source,
            mix_examples=mix_examples,
            error_feedback=error_feedback,
        )
    # No parameters defined for this location
    return _NONE_STRATEGY


def jsonify_python_specific_types(value: dict[str, Any]) -> dict[str, Any]:
    """Convert Python-specific values to their JSON equivalents."""
    stack: list = [value]
    while stack:
        item = stack.pop()
        if isinstance(item, dict):
            for key, sub_item in item.items():
                if isinstance(sub_item, bool):
                    item[key] = "true" if sub_item else "false"
                elif sub_item is None:
                    item[key] = "null"
                elif isinstance(sub_item, dict):
                    stack.append(sub_item)
                elif isinstance(sub_item, list):
                    stack.extend(item)
        elif isinstance(item, list):
            stack.extend(item)
    return value


def _build_custom_formats(generation_config: GenerationConfig, mode: GenerationMode) -> dict[str, st.SearchStrategy]:
    cache_key = (id(generation_config), mode)
    cached = custom_formats_cache.get(cache_key)
    if cached is not MISSING:
        return cached
    custom_formats = _build_custom_formats_uncached(generation_config, mode)
    custom_formats_cache[cache_key] = custom_formats
    return custom_formats


def _build_custom_formats_uncached(
    generation_config: GenerationConfig, mode: GenerationMode
) -> dict[str, st.SearchStrategy]:
    custom_formats = {**get_default_format_strategies(), **STRING_FORMATS}
    header_values_kwargs: dict[str, Any] = {}
    if generation_config.exclude_header_characters is not None:
        header_values_kwargs["exclude_characters"] = generation_config.exclude_header_characters
        if not generation_config.allow_x00:
            header_values_kwargs["exclude_characters"] += "\x00"
    elif not generation_config.allow_x00:
        header_values_kwargs["exclude_characters"] = DEFAULT_HEADER_EXCLUDE_CHARACTERS + "\x00"
    if generation_config.codec not in (None, "utf-8"):
        # User explicitly set a non-default codec - use it directly
        header_values_kwargs["codec"] = generation_config.codec
        custom_formats[HEADER_FORMAT] = header_values(**header_values_kwargs)
    else:
        base_exclude = header_values_kwargs.get("exclude_characters", "")
        valid_exclude = "".join(sorted(set(base_exclude + INVALID_HEADER_CHARS)))

        if mode.is_positive:
            # Positive mode: Always generate RFC-valid headers
            custom_formats[HEADER_FORMAT] = header_values(codec="ascii", exclude_characters=valid_exclude)
        else:
            # Negative mode: Occasionally allow invalid characters
            @st.composite  # type: ignore[untyped-decorator]
            def header_strategy(draw: st.DrawFn) -> str:
                random = draw(st.randoms())
                if random.random() < VALID_HEADER_PROBABILITY:
                    return draw(header_values(codec="ascii", exclude_characters=valid_exclude))
                return draw(header_values(**header_values_kwargs))

            custom_formats[HEADER_FORMAT] = header_strategy()
    custom_formats.update(get_header_format_strategies(mode))
    return custom_formats


# Don't descend: these hold literal data or negate, where snapping corrupts the value or weakens the negation.
_NO_SNAP_KEYWORDS = frozenset({"const", "default", "enum", "example", "examples", "if", "not"})
# Keywords whose value maps names to subschemas; descend into the values, never the keys.
_SCHEMA_MAP_KEYWORDS = frozenset(
    {"properties", "patternProperties", "dependentSchemas", "dependencies", "$defs", "definitions"}
)


def snap_float32_bounds(schema: object) -> None:
    """Pin exclusive `format: float` bounds throughout `schema` to float32-representable values, in place."""
    if not isinstance(schema, dict):
        return
    _snap_float32_node(schema)
    for key, value in schema.items():
        if key in _NO_SNAP_KEYWORDS:
            continue
        if key in _SCHEMA_MAP_KEYWORDS and isinstance(value, dict):
            for subschema in value.values():
                snap_float32_bounds(subschema)
        elif isinstance(value, list):
            for item in value:
                snap_float32_bounds(item)
        elif isinstance(value, dict):
            snap_float32_bounds(value)


def _snap_float32_node(schema: dict[str, Any]) -> None:
    # `format: float` is single precision; pin exclusive bounds so narrowed values can't collapse past them.
    if schema.get("format") != "float":
        return
    declared = schema.get("type")
    # Skip integer-only schemas; a number (or number union) still has a float branch to snap.
    if declared is not None and "number" not in (declared if isinstance(declared, list) else [declared]):
        return
    if "exclusiveMinimum" not in schema and "exclusiveMaximum" not in schema:
        return
    exclusive_minimum = schema.get("exclusiveMinimum")
    exclusive_maximum = schema.get("exclusiveMaximum")
    # A present exclusive bound that isn't bool/numeric is an invalid schema; leave it for the validator to reject.
    if "exclusiveMinimum" in schema and not _is_resolvable_bound(exclusive_minimum):
        return
    if "exclusiveMaximum" in schema and not _is_resolvable_bound(exclusive_maximum):
        return
    minimum, maximum = resolve_inclusive_bounds(
        schema, step=lambda value, going_up: next_float32(value, going_up=going_up)
    )
    if bounds_are_unsatisfiable(minimum, maximum):
        _drop_empty_float_branch(schema)
        return
    declared_minimum = schema.get("minimum")
    declared_maximum = schema.get("maximum")
    if _is_resolvable_bound(exclusive_minimum):
        if is_numeric_bound(minimum):
            # A separate inclusive `minimum` may be tighter than the stepped exclusive bound; keep the stricter one.
            schema["minimum"] = max(minimum, declared_minimum) if is_numeric_bound(declared_minimum) else minimum
        schema.pop("exclusiveMinimum", None)
    if _is_resolvable_bound(exclusive_maximum):
        if is_numeric_bound(maximum):
            schema["maximum"] = min(maximum, declared_maximum) if is_numeric_bound(declared_maximum) else maximum
        schema.pop("exclusiveMaximum", None)


def _is_resolvable_bound(value: object) -> bool:
    return isinstance(value, bool) or is_numeric_bound(value)


def _drop_empty_float_branch(schema: dict[str, Any]) -> None:
    # No finite float32 lies past the bound, so the `number` branch is empty; other type branches stay valid.
    declared = schema.get("type")
    if declared is None:
        # No declared type: pin the surviving non-numeric types so sibling constraints keep applying.
        schema["type"] = ["null", "boolean", "string", "array", "object"]
        schema.pop("format", None)
        return
    survivors = [kind for kind in (declared if isinstance(declared, list) else [declared]) if kind != "number"]
    if survivors:
        schema["type"] = survivors if len(survivors) > 1 else survivors[0]
        schema.pop("format", None)
    else:
        schema.clear()
        schema["not"] = {}


def _schema_has_float_format(node: object) -> bool:
    if isinstance(node, dict):
        return node.get("format") == "float" or any(_schema_has_float_format(value) for value in node.values())
    if isinstance(node, list):
        return any(_schema_has_float_format(item) for item in node)
    return False


def snapped_float32_clone(schema: JsonSchema) -> JsonSchema:
    """Return a float32-snapped deep clone of `schema`, or `schema` unchanged when it has no `format: float` to snap."""
    if not _schema_has_float_format(schema):
        return schema
    clone = deepclone(schema)
    snap_float32_bounds(clone)
    return clone


def make_positive_strategy(
    schema: JsonSchema,
    operation_name: str,
    location: ParameterLocation,
    media_type: str | None,
    generation_config: GenerationConfig,
    validator_cls: type[jsonschema_rs.Validator],
    name_to_uri: dict[str, str] | None = None,
    validation_schema: JsonSchema | None = None,
    target_descriptors: tuple | None = None,
) -> st.SearchStrategy:
    """Strategy for generating values that fit the schema."""
    custom_formats = _build_custom_formats(generation_config, GenerationMode.POSITIVE)
    schema = snapped_float32_clone(schema)
    return from_schema(
        schema,
        custom_formats=custom_formats,
        allow_x00=generation_config.allow_x00,
        codec=generation_config.codec,
    )


def _can_skip_header_filter(schema: dict[str, Any]) -> bool:
    # All headers should have a known format key in order to avoid the header filter
    return all(
        sub_schema.get("format") in _PLAIN_HEADER_FORMATS for sub_schema in schema.get("properties", {}).values()
    )


def make_negative_strategy(
    schema: JsonSchema,
    operation_name: str,
    location: ParameterLocation,
    media_type: str | None,
    generation_config: GenerationConfig,
    validator_cls: type[jsonschema_rs.Validator],
    name_to_uri: dict[str, str] | None = None,
    validation_schema: JsonSchema | None = None,
    target_descriptors: tuple | None = None,
) -> st.SearchStrategy:
    custom_formats = _build_custom_formats(generation_config, GenerationMode.NEGATIVE)
    return negative_schema(
        schema,
        operation_name=operation_name,
        location=location,
        media_type=media_type,
        custom_formats=custom_formats,
        generation_config=generation_config,
        validator_cls=validator_cls,
        validation_schema=validation_schema,
        name_to_uri=name_to_uri,
        target_descriptors=target_descriptors,
    )


GENERATOR_MODE_TO_STRATEGY_FACTORY = {
    GenerationMode.POSITIVE: make_positive_strategy,
    GenerationMode.NEGATIVE: make_negative_strategy,
}


def apply_hooks(
    operation: APIOperation,
    ctx: HookContext,
    hooks: HookDispatcher | None,
    strategy: st.SearchStrategy,
    location: ParameterLocation,
) -> st.SearchStrategy:
    """Apply all hooks related to the given location.

    Passes `GeneratedValue` (de)wrapping helpers so user hooks see plain values even
    when negative-mode strategies wrap them.
    """
    return apply_to_all_dispatchers(
        operation,
        ctx,
        hooks,
        strategy,
        location.container_name,
        filter_wrapper=wrap_filter_hook_for_generated_value,
        map_wrapper=wrap_map_hook_for_generated_value,
        flatmap_wrapper=wrap_flatmap_hook_for_generated_value,
    )
