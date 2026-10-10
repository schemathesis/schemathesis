from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from itertools import islice
from typing import TYPE_CHECKING, Any, cast

import jsonschema_rs
from hypothesis import event, note, reject
from hypothesis import strategies as st
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
from schemathesis.core.jsonschema import CANONICALIZE_DRAFT_BY_VALIDATOR
from schemathesis.core.jsonschema.numeric import (
    bounds_are_unsatisfiable,
    is_numeric_bound,
    next_float32,
    resolve_inclusive_bounds,
)
from schemathesis.core.jsonschema.types import JsonSchema, JsonValue
from schemathesis.core.media_types import FORM_MEDIA_TYPES, find_media_type_strategy
from schemathesis.core.mutations import Mutation, MutationMetadata
from schemathesis.core.parameters import ParameterLocation
from schemathesis.core.timing import Instant
from schemathesis.core.transforms import deepclone, to_wire_string
from schemathesis.core.transport import prepare_urlencoded
from schemathesis.generation import GenerationMode
from schemathesis.generation.feedback import NO_FEEDBACK, FeedbackSources
from schemathesis.generation.hypothesis import custom_formats_cache
from schemathesis.generation.jsonschema.builder import EMPTY_STRATEGY, build
from schemathesis.generation.jsonschema.context import Alphabet
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
from schemathesis.specs.openapi.adapter.parameters import (
    CredentialRemoval,
    OpenApiBody,
    OpenApiParameter,
    OpenApiParameterSet,
    build_constants_overlay_strategy,
)
from schemathesis.specs.openapi.adapter.security import ORIGINAL_SECURITY_TYPE_KEY
from schemathesis.specs.openapi.coverage._schema import ANNOTATION_KEYWORDS
from schemathesis.specs.openapi.diagnostics import build_unsatisfiable_schema_error
from schemathesis.specs.openapi.formats import (
    DEFAULT_HEADER_EXCLUDE_CHARACTERS,
    HEADER_FORMAT,
    INVALID_HEADER_CHARS,
    STRING_FORMATS,
    cookie_alphabet,
    format_lengths_for,
    get_alphabet_format_strategies,
    get_default_format_strategies,
    header_alphabet,
    header_values,
)
from schemathesis.specs.openapi.headers import PLAIN_HEADER_FORMATS, get_header_format_strategies
from schemathesis.specs.openapi.negative import (
    negative_schema,
    wrap_filter_hook_for_generated_value,
    wrap_flatmap_hook_for_generated_value,
    wrap_map_hook_for_generated_value,
)
from schemathesis.specs.openapi.negative.mutations import has_negatable_target
from schemathesis.specs.openapi.negative.utils import is_binary_format
from schemathesis.transport.serialization import quote_all

if TYPE_CHECKING:
    from schemathesis.generation.dictionaries import DictionaryDraw
    from schemathesis.python._constants.pool import ConstantDraw, ConstantsPool
    from schemathesis.specs.openapi.schemas import OpenApiOperation

SLASH = "/"
# Probability of generating valid headers in negative mode
VALID_HEADER_PROBABILITY = 0.95
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
    if strategy is EMPTY_STRATEGY:
        # Drawing from an empty strategy only rejects the input, which reads as "nothing to generate here"
        # in phases that recover from rejection - stateful testing would report success without testing this.
        raise build_unsatisfiable_schema_error(operation)
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
    feedback: FeedbackSources = NO_FEEDBACK,
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

    negated = _draw_negated_locations(
        draw,
        operation,
        generation_mode,
        explicit={
            ParameterLocation.PATH: path_parameters,
            ParameterLocation.HEADER: headers,
            ParameterLocation.COOKIE: cookies,
            ParameterLocation.QUERY: query,
        },
        body_is_generated=body is NOT_SET,
    )

    credential_removal = None
    if generation_mode.is_negative and get_sole_credential(operation) is not None:
        if (
            feedback.answered_credential_removals is not None
            and operation.label in feedback.answered_credential_removals
        ):
            credential_removal = CredentialRemoval.ANSWERED
        else:
            credential_removal = CredentialRemoval.PENDING

    # Drawn in this order: path, headers, cookies, query.
    path_parameters_, headers_, cookies_, query_ = (
        generate_parameter(
            location,
            explicit,
            operation,
            draw,
            ctx,
            hooks,
            _mode_for(location, generation_mode, negated),
            generation_config,
            extra_data_source=feedback.extra_data_source,
            error_feedback=feedback.error_feedback,
            mix_examples=mix_examples,
            constants_value_source=feedback.constants_value_source,
            credential_removal=credential_removal if location is ParameterLocation.HEADER else None,
        )
        for location, explicit in (
            (ParameterLocation.PATH, path_parameters),
            (ParameterLocation.HEADER, headers),
            (ParameterLocation.COOKIE, cookies),
            (ParameterLocation.QUERY, query),
        )
    )

    if body is not NOT_SET:
        _ensure_explicit_body_is_serializable(operation, body, media_type)
        body_ = ValueContainer(value=body, location="body", generator=None, meta=None)
    elif operation.body:
        body_, media_type = _generate_body(
            draw,
            operation,
            ctx,
            hooks,
            _mode_for(ParameterLocation.BODY, generation_mode, negated),
            generation_config,
            extra_data_source=feedback.extra_data_source,
            error_feedback=feedback.error_feedback,
            mix_examples=mix_examples,
            constants_value_source=feedback.constants_value_source,
        )
    else:
        body_ = ValueContainer(value=body, location="body", generator=None, meta=None)

    # If we need to generate negative cases but no generated values were negated, then skip the whole test.
    # Skipping or rejecting a stateful step discards the whole scenario, so the step is sent as a positive one instead.
    if (
        generation_mode.is_negative
        and phase != TestPhase.STATEFUL
        and not any_negated_values([query_, cookies_, headers_, path_parameters_, body_])
    ):
        if generation_config.modes == [GenerationMode.NEGATIVE]:
            raise SkipTest("Impossible to generate negative test cases")
        reject()

    effective_generation_mode, phase_data = _describe_generation(
        generation_mode, phase, (query_, cookies_, headers_, path_parameters_, body_)
    )
    body_value, multipart_content_types = _split_form_body(body_.value)
    # Provenance is reported in this order: query, path, headers, cookies, body.
    pool_draws, semantic_draws, dictionary_draws, constants_draws = _collect_draws(
        (query_, path_parameters_, headers_, cookies_, body_)
    )
    header_values = headers_.value
    # A positive `Content-Type` header must describe the body it is sent with; a negated one stays a fuzz target.
    if media_type is not None and header_values and headers_.generator != GenerationMode.NEGATIVE:
        header_values = _pin_content_type_header(operation, header_values, headers, media_type)
    instance = operation.Case(
        media_type=media_type,
        path_parameters=path_parameters_.value or {},
        headers=header_values or CaseInsensitiveDict(),
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
            components=_component_modes(query_, path_parameters_, headers_, cookies_, body_),
            pool_draws=pool_draws,
            semantic_draws=semantic_draws,
            dictionary_draws=dictionary_draws,
            constants_draws=constants_draws,
        ),
    )
    auth_context = auths.AuthContext(
        operation=operation,
        app=operation.app,
    )
    supplied = _supplied_parameters(headers, query, cookies, pool_draws)
    auths.set_on_generated_case(instance, auth_context, auth_storage, supplied)
    return instance


def _mode_for(
    location: ParameterLocation, generation_mode: GenerationMode, negated: set[ParameterLocation] | None
) -> GenerationMode:
    if negated is None or location in negated:
        return generation_mode
    return GenerationMode.POSITIVE


def _ensure_explicit_body_is_serializable(operation: APIOperation, body: Any, media_type: str | None) -> None:
    # This explicit body payload comes for a media type that has a custom strategy registered
    # Such strategies only support binary payloads, otherwise they can't be serialized
    if not isinstance(body, bytes) and media_type and find_media_type_strategy(media_type) is not None:
        all_media_types = operation.get_request_payload_content_types()
        raise SerializationNotPossible.from_media_types(*all_media_types)


def _generate_body(
    draw: st.DrawFn,
    operation: APIOperation,
    ctx: HookContext,
    hooks: HookDispatcher | None,
    generation_mode: GenerationMode,
    generation_config: GenerationConfig,
    *,
    extra_data_source: ExtraDataSource | None,
    error_feedback: ErrorFeedbackStore | None,
    mix_examples: bool,
    constants_value_source: ConstantsPool | None,
) -> tuple[ValueContainer, str]:
    """Draw a body and the media type it is sent with."""
    candidates, generation_mode = _body_candidates(operation.body.items, generation_mode)
    parameter = draw(st.sampled_from(candidates))
    strategy = _get_body_strategy(
        parameter,
        operation,
        generation_config,
        draw,
        generation_mode,
        extra_data_source=extra_data_source,
        error_feedback=error_feedback,
        mix_examples=mix_examples,
        constants_value_source=constants_value_source,
    )
    strategy = apply_hooks(operation, ctx, hooks, strategy, ParameterLocation.BODY)
    media_type = _draw_body_media_type(draw, operation, parameter)
    if media_types.is_form_urlencoded(media_type):
        # The hybrid strategy wraps in `GeneratedValue` when it picks a captured pool
        # variant, so both positive and negative paths must unwrap before
        # transforming/filtering and rewrap to keep `pool_draws` flowing.
        strategy = strategy.map(
            wrap_map_hook_for_generated_value(_prepare_urlencoded_form, prune_constants=False)
        ).filter(wrap_filter_hook_for_generated_value(_is_valid_urlencoded_form))
    generated = _draw(draw, strategy, operation)
    # Negative strategy returns GeneratedValue, positive returns just value
    if not isinstance(generated, GeneratedValue):
        generated = GeneratedValue(generated, None)
    body = generated.value
    credentials = operation.schema.bootstrapped_credentials.get(operation.label)
    # Generated credentials never match an account, so logins and the session operations behind them stay untested.
    if credentials is not None and generation_mode.is_positive and isinstance(body, dict) and draw(st.booleans()):
        body = {**body, **credentials}
    container = ValueContainer(
        value=body,
        location="body",
        generator=generation_mode,
        meta=generated.meta,
        pool_draws=generated.pool_draws,
        semantic_draws=generated.semantic_draws,
        dictionary_draws=generated.dictionary_draws,
        constants_draws=generated.constants_draws,
    )
    return container, media_type


def _body_candidates(
    items: list[OpenApiBody], generation_mode: GenerationMode
) -> tuple[list[OpenApiBody], GenerationMode]:
    if generation_mode.is_negative:
        # Consider only schemas that are possible to negate
        candidates = [item for item in items if item.is_negatable]
        if candidates:
            return candidates, generation_mode
        # Not possible to negate body, fallback to positive data generation
        return items, GenerationMode.POSITIVE
    return items, generation_mode


def _draw_body_media_type(draw: st.DrawFn, operation: APIOperation, parameter: OpenApiBody) -> str:
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
    return media_type


def _prepare_urlencoded_form(value: Any) -> Any:
    # Keep the per-property content types a form body carries.
    if isinstance(value, FormBodyWithContentTypes):
        return FormBodyWithContentTypes(body=prepare_urlencoded(value.body), content_types=value.content_types)
    return prepare_urlencoded(value)


def _is_valid_urlencoded_form(value: Any) -> bool:
    if isinstance(value, FormBodyWithContentTypes):
        return is_valid_urlencoded(value.body)
    return is_valid_urlencoded(value)


PhaseData = ExamplesPhaseData | FuzzingPhaseData | StatefulPhaseData

_PHASE_DATA_TYPES: dict[TestPhase, type[PhaseData]] = {
    TestPhase.EXAMPLES: ExamplesPhaseData,
    TestPhase.FUZZING: FuzzingPhaseData,
    TestPhase.STATEFUL: StatefulPhaseData,
}


def _phase_data(
    phase: TestPhase,
    description: str | None,
    *,
    parameter: str | None = None,
    parameter_location: ParameterLocation | None = None,
    location: str | None = None,
    mutations: tuple[Mutation, ...] = (),
) -> PhaseData:
    return _PHASE_DATA_TYPES[phase](
        description=description,
        parameter=parameter,
        parameter_location=parameter_location,
        location=location,
        mutations=mutations,
    )


def _describe_generation(
    generation_mode: GenerationMode, phase: TestPhase, containers: tuple[ValueContainer, ...]
) -> tuple[GenerationMode, PhaseData]:
    """The mode the drawn case ended up in, and what made it negative."""
    # A schema-invalid dictionary draw carries negative content even when no mutator
    # produced surviving metadata (the overlay strips mutations for overwritten parameters).
    first_invalid_dictionary_draw: DictionaryDraw | None = next(
        (draw for container in containers for draw in container.dictionary_draws if not draw.matches_schema),
        None,
    )
    if not generation_mode.is_negative or (
        not any(
            container.generator == GenerationMode.NEGATIVE and container.meta is not None
            for container in containers
            if container.is_generated
        )
        and first_invalid_dictionary_draw is None
    ):
        return GenerationMode.POSITIVE, _phase_data(phase, "Positive test case")
    # Every negated container contributes; each mutation carries the location it was applied to.
    metadata = MutationMetadata(
        mutations=tuple(
            mutation
            for container in containers
            if container.generator == GenerationMode.NEGATIVE and container.meta is not None
            for mutation in container.meta.mutations
        )
    )
    if metadata.mutations:
        return generation_mode, _phase_data(
            phase,
            metadata.description,
            parameter=metadata.parameter,
            parameter_location=metadata.parameter_location,
            location=metadata.location,
            mutations=metadata.mutations,
        )
    # Mutators reject empty mutation lists and overlays drop emptied metadata, so an invalid draw is what is left.
    draw = first_invalid_dictionary_draw
    assert draw is not None
    return generation_mode, _phase_data(
        phase,
        f"Dictionary `{draw.dictionary}` entry violates the schema for `{draw.parameter_name}`",
        parameter=draw.parameter_name,
        parameter_location=_LOCATION_NAME_TO_ENUM.get(draw.parameter_location),
    )


def _split_form_body(value: Any) -> tuple[Any, dict[str, str] | None]:
    """Separate a form body from the per-property content types it was generated with."""
    if isinstance(value, FormBodyWithContentTypes):
        return value.body, value.content_types
    return value, None


def _collect_draws(
    containers: tuple[ValueContainer, ...],
) -> tuple[tuple[PoolDraw, ...], tuple[SemanticDraw, ...], tuple[DictionaryDraw, ...], tuple[ConstantDraw, ...]]:
    return (
        tuple(draw for container in containers for draw in container.pool_draws),
        tuple(draw for container in containers for draw in container.semantic_draws),
        tuple(draw for container in containers for draw in container.dictionary_draws),
        tuple(draw for container in containers for draw in container.constants_draws),
    )


def _component_modes(
    query: ValueContainer,
    path_parameters: ValueContainer,
    headers: ValueContainer,
    cookies: ValueContainer,
    body: ValueContainer,
) -> dict[ParameterLocation, ComponentInfo]:
    return {
        kind: ComponentInfo(mode=value.mode)
        for kind, value in [
            (ParameterLocation.QUERY, query),
            (ParameterLocation.PATH, path_parameters),
            (ParameterLocation.HEADER, headers),
            (ParameterLocation.COOKIE, cookies),
            (ParameterLocation.BODY, body),
        ]
        if value.mode is not None
    }


def _supplied_parameters(
    headers: dict[str, Any] | None,
    query: dict[str, Any] | None,
    cookies: dict[str, Any] | None,
    pool_draws: tuple[PoolDraw, ...],
) -> frozenset[tuple[ParameterLocation, str]]:
    # Values from links, explicit arguments or the resource pool are real; only freshly generated credentials are dropped.
    return frozenset(
        [
            (location, name)
            for location, given in (
                (ParameterLocation.HEADER, headers),
                (ParameterLocation.QUERY, query),
                (ParameterLocation.COOKIE, cookies),
            )
            for name in given or ()
        ]
        + [(ParameterLocation(draw.location), draw.parameter_name) for draw in pool_draws]
    )


OPTIONAL_BODY_RATE = 0.05


@dataclass(slots=True)
class FormBodyWithContentTypes:
    """Form body data with selected content types for properties."""

    body: dict[str, Any]
    content_types: dict[str, str]  # property_name -> selected content type


def _form_property_value(value: GeneratedValue | dict[str, Any], key: str) -> Any:
    """Reduce a `{key: value}` overlay result to its single value, keeping any constants provenance."""
    if isinstance(value, GeneratedValue):
        inner = value.value[key]
        if value.constants_draws:
            return GeneratedValue(
                inner,
                value.meta,
                value.pool_draws,
                value.semantic_draws,
                value.dictionary_draws,
                value.constants_draws,
            )
        return inner
    return value[key]


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
    if not parameter.is_required and (
        # An optional body whose schema admits no value can only be omitted.
        strategy is EMPTY_STRATEGY
        or draw(st.floats(min_value=0.0, max_value=1.0, allow_infinity=False, allow_nan=False, allow_subnormal=False))
        < OPTIONAL_BODY_RATE
    ):
        strategy |= _JUST_NOT_SET
    return strategy


def _build_form_strategy_with_encoding(
    parameter: OpenApiBody,
    operation: OpenApiOperation,
    generation_config: GenerationConfig,
    generation_mode: GenerationMode,
    constants_value_source: ConstantsPool | None = None,
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
            if generation_mode.is_negative and is_binary_format(properties[property_name]):
                continue
            strategies_for_types = []
            for ct in content_types:
                strategy = find_media_type_strategy(ct)
                if strategy is not None:
                    # Pair strategy with its content type so we know which was selected
                    strategies_for_types.append(st.tuples(st.just(ct), strategy))

            if strategies_for_types:
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
            strategy = strategy_factory(
                subschema,
                operation.label,
                ParameterLocation.BODY,
                parameter.media_type,
                generation_config,
                operation.schema.adapter.jsonschema_validator_cls,
            )
            if constants_value_source is not None and generation_mode.is_positive:
                strategy = build_constants_overlay_strategy(
                    st.fixed_dictionaries({property_name: strategy}),
                    source=constants_value_source,
                    schema_properties={property_name: subschema},
                    validator_cls=operation.schema.adapter.jsonschema_validator_cls,
                    location="body",
                    generation_config=generation_config,
                ).map(lambda value, key=property_name: _form_property_value(value, key))
            property_strategies[property_name] = strategy

    # Build fixed dictionary strategy with optional properties
    required = set(schema.get("required", []))
    required_strategies = {k: v for k, v in property_strategies.items() if k in required}
    optional_strategies = {k: _JUST_NOT_SET | v for k, v in property_strategies.items() if k not in required}

    @st.composite  # type: ignore[untyped-decorator]
    def build_body(draw: st.DrawFn) -> FormBodyWithContentTypes | GeneratedValue:
        body: dict[str, Any] = {}
        selected_content_types: dict[str, str] = {}
        constants_draws: list[ConstantDraw] = []

        def _take(strategy: st.SearchStrategy) -> Any:
            result = draw(strategy)
            if isinstance(result, GeneratedValue):
                constants_draws.extend(result.constants_draws)
                return result.value
            return result

        # Generate required properties
        for key, strategy in required_strategies.items():
            body[key] = _take(strategy)
        # Generate optional properties, filtering out NOT_SET
        for key, strategy in optional_strategies.items():
            value = _take(strategy)
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

        result = FormBodyWithContentTypes(body=body, content_types=selected_content_types)
        if constants_draws:
            return GeneratedValue(result, None, (), (), (), tuple(constants_draws))
        return result

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
    constants_value_source: ConstantsPool | None = None,
) -> st.SearchStrategy:
    # Check for custom encoding in form bodies (multipart/form-data or application/x-www-form-urlencoded)
    if parameter.media_type in FORM_MEDIA_TYPES:
        custom_strategy = _build_form_strategy_with_encoding(
            parameter,
            operation,
            generation_config,
            generation_mode,
            constants_value_source,
        )
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
        constants_value_source=constants_value_source,
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
    constants_value_source: ConstantsPool | None = None,
    credential_removal: CredentialRemoval | None = None,
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
            constants_value_source=constants_value_source,
            credential_removal=credential_removal,
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
        constants_value_source=constants_value_source,
        credential_removal=credential_removal,
    )
    strategy = apply_hooks(operation, ctx, hooks, strategy, location)
    new = _draw(draw, strategy, operation)
    if isinstance(new, GeneratedValue):
        meta = new.meta
        pool_draws = new.pool_draws
        semantic_draws = new.semantic_draws
        dictionary_draws = new.dictionary_draws
        constants_draws = new.constants_draws
        new = new.value
    else:
        meta = None
        pool_draws = ()
        semantic_draws = ()
        dictionary_draws = ()
        constants_draws = ()
    if new is not None:
        # Explicit values win over anything hooks put into the generated part
        copied = dict(value)
        copied.update((key, item) for key, item in new.items() if key not in value)
        return GeneratedValue(
            value=copied,
            meta=meta,
            pool_draws=pool_draws,
            semantic_draws=semantic_draws,
            dictionary_draws=dictionary_draws,
            constants_draws=constants_draws,
        )
    return GeneratedValue(
        value=value,
        meta=meta,
        pool_draws=pool_draws,
        semantic_draws=semantic_draws,
        dictionary_draws=dictionary_draws,
        constants_draws=constants_draws,
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
    constants_draws: tuple[ConstantDraw, ...] = ()

    @property
    def is_generated(self) -> bool:
        """If value was generated."""
        return self.generator is not None and (self.location == "body" or self.value is not None)

    @property
    def mode(self) -> GenerationMode | None:
        """Mode of the drawn value: a negative strategy may still yield a valid, unmutated value."""
        if (
            self.generator == GenerationMode.NEGATIVE
            and self.meta is None
            and all(draw.matches_schema for draw in self.dictionary_draws)
        ):
            return GenerationMode.POSITIVE
        return self.generator


def _pin_content_type_header(
    operation: APIOperation, headers: dict[str, Any], explicit: dict[str, Any] | None, media_type: str
) -> dict[str, Any]:
    """Point a generated `Content-Type` header parameter at the body media type when its schema admits it."""
    explicit_names = {name.lower() for name in explicit or ()}
    validator_cls = operation.schema.adapter.jsonschema_validator_cls
    for parameter in operation.headers:
        name = parameter.name
        if (
            name.lower() == "content-type"
            and name in headers
            and "content-type" not in explicit_names
            and parameter.admits(media_type, validator_cls)
        ):
            return {**headers, name: media_type}
    return headers


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
    constants_value_source: ConstantsPool | None = None,
    credential_removal: CredentialRemoval | None = None,
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
        constants_value_source=constants_value_source,
        credential_removal=credential_removal,
    )
    value = generated.value
    if value is not None and location == ParameterLocation.PATH:
        value = quote_all(value)
        if operation.schema._probe_state.path_decoder_strict:
            value = _strip_path_decoder_unsafe(value)

    used_generator: GenerationMode | None = generator
    # When we pass `explicit`, then its parts are excluded from generation of the final value
    # If the final value is the same, then other parameters were not generated, unless a mutation removed them
    if value == explicit and generated.meta is None:
        used_generator = None
    return ValueContainer(
        value=value,
        location=location,
        generator=used_generator,
        meta=generated.meta,
        pool_draws=generated.pool_draws,
        semantic_draws=generated.semantic_draws,
        dictionary_draws=generated.dictionary_draws,
        constants_draws=generated.constants_draws,
    )


def _draw_negated_locations(
    draw: st.DrawFn,
    operation: APIOperation,
    generation_mode: GenerationMode,
    *,
    explicit: dict[ParameterLocation, dict[str, Any] | None],
    body_is_generated: bool,
) -> set[ParameterLocation] | None:
    """Pick which negatable locations a negative case breaks; `None` leaves every location negative."""
    if not generation_mode.is_negative:
        return None
    candidates = []
    for location in (
        ParameterLocation.PATH,
        ParameterLocation.HEADER,
        ParameterLocation.COOKIE,
        ParameterLocation.QUERY,
    ):
        properties = cast(OpenApiParameterSet, operation.get_parameter_set(location)).schema["properties"]
        # Explicit values are sent as given, so a location they fully cover has nothing left to negate.
        # Only header names are case-insensitive.
        fold = str.lower if location == ParameterLocation.HEADER else str
        given = {fold(name) for name in explicit[location] or ()}
        if not properties or {fold(name) for name in properties} <= given:
            continue
        if location == ParameterLocation.PATH and not can_negate_path_parameters(operation):
            continue
        if location.is_in_header and not can_negate_headers(operation, location):
            continue
        candidates.append(location)
    if body_is_generated and operation.body and any(item.is_negatable for item in operation.body.items):
        candidates.append(ParameterLocation.BODY)
    if not candidates:
        return None
    # Breaking every location at once lets the first check a server runs hide how it handles the others.
    negated = {location for location in candidates if draw(st.booleans())}
    return negated or {draw(st.sampled_from(candidates))}


def can_negate_path_parameters(operation: APIOperation) -> bool:
    """Check if any path parameter can be negated."""
    # No path parameters to negate
    parameters = cast(OpenApiParameterSet, operation.path_parameters).schema["properties"]
    if not parameters:
        return True
    # A path value always serializes to a string, so a type change is invisible on the wire and the
    # constraint keywords the mutator will accept are the whole negatable surface.
    return any(
        has_negatable_target(
            parameter,
            location=ParameterLocation.PATH,
            allow_extra_parameters=operation.schema.config.generation.allow_extra_parameters,
        )
        for parameter in parameters.values()
    )


def can_negate_headers(operation: APIOperation, location: ParameterLocation) -> bool:
    """Check if any header can be negated."""
    container = cast(OpenApiParameterSet, operation.get_parameter_set(location))
    # No headers to negate
    headers = container.schema["properties"]
    if not headers:
        return True
    required = container.schema.get("required", ())
    # Omitting a required header always violates it; an optional one is negatable only through a constraint
    # on its value, and any string passes a plain string schema.
    return any(name in required or not _is_plain_header(header) for name, header in headers.items())


def get_sole_credential(operation: APIOperation) -> OpenApiParameter | None:
    """The required credential header that is the operation's only input and has a value worth negating."""
    if operation.body:
        return None
    parameters = list(islice(operation.iter_parameters(), 2))
    if len(parameters) != 1:
        return None
    parameter = cast(OpenApiParameter, parameters[0])
    if (
        parameter.location != ParameterLocation.HEADER
        or not parameter.is_required
        or ORIGINAL_SECURITY_TYPE_KEY not in parameter.definition
    ):
        return None
    schema = cast(OpenApiParameterSet, operation.headers).schema["properties"][parameter.name]
    # Any string satisfies a plain header, so leaving it out is the only negative request it has.
    if _is_plain_header(schema):
        return None
    return parameter


_PLAIN_HEADERS = ({"type": "string"}, *({"type": "string", "format": f} for f in PLAIN_HEADER_FORMATS))
_NULL_SCHEMA = {"type": "null"}


def _is_plain_header(schema: JsonSchema) -> bool:
    """Whether the header accepts any string, ignoring annotations and a `null` alternative."""
    if not isinstance(schema, dict):
        return False
    keywords = {
        key: value
        for key, value in schema.items()
        if key not in ANNOTATION_KEYWORDS and key != "nullable" and not key.startswith("x-")
    }
    if len(keywords) == 1:
        branches = keywords.get("anyOf", keywords.get("oneOf"))
        if isinstance(branches, list):
            return all(branch == _NULL_SCHEMA or _is_plain_header(branch) for branch in branches)
    if isinstance(keywords.get("type"), list) and sorted(keywords["type"]) == ["null", "string"]:
        keywords["type"] = "string"
    return keywords in _PLAIN_HEADERS


def get_parameters_strategy(
    operation: APIOperation,
    generation_mode: GenerationMode,
    location: ParameterLocation,
    generation_config: GenerationConfig,
    exclude: Iterable[str] = (),
    extra_data_source: ExtraDataSource | None = None,
    mix_examples: bool = True,
    error_feedback: ErrorFeedbackStore | None = None,
    constants_value_source: ConstantsPool | None = None,
    credential_removal: CredentialRemoval | None = None,
) -> st.SearchStrategy:
    """Create a new strategy for the case's component from the API operation parameters."""
    container = cast(OpenApiParameterSet, operation.get_parameter_set(location))
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
            constants_value_source=constants_value_source,
            credential_removal=credential_removal,
        )
    # No parameters defined for this location
    return _NONE_STRATEGY


def jsonify_python_specific_types(value: Any) -> Any:
    """Convert Python-specific values to their JSON equivalents.

    Builds a new value: the input may be a spec-declared example that every other case reuses.
    """
    if value is None or isinstance(value, bool):
        return to_wire_string(value)
    if isinstance(value, dict):
        return {key: jsonify_python_specific_types(item) for key, item in value.items()}
    if isinstance(value, list):
        return [jsonify_python_specific_types(item) for item in value]
    return value


def jsonify_query_parameters(value: dict[str, Any], optional: frozenset[str]) -> dict[str, Any]:
    """Convert query values to their JSON equivalents, omitting optional parameters that carry no value.

    Query strings have no rendering for a JSON null; leaving the parameter out is what "no value" means there.
    """
    return {
        key: jsonify_python_specific_types(item)
        for key, item in value.items()
        if item is not None or key not in optional
    }


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
    # Bearer tokens follow the header character set in force, so positive ones stay ASCII.
    custom_formats["_bearer_auth"] = custom_formats[HEADER_FORMAT].map("Bearer {}".format)
    custom_formats.update(get_header_format_strategies(mode))
    # Pinned to the character set in force, since this map goes to callers that cannot supply one.
    alphabet = Alphabet(allow_x00=generation_config.allow_x00, codec=generation_config.codec).as_strategy()
    for name, make_strategy in get_alphabet_format_strategies().items():
        custom_formats[name] = make_strategy(alphabet)
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
    schema = snapped_float32_clone(schema)
    return _canonical_strategy(schema, generation_config, validator_cls, location=location, media_type=media_type)


def _canonical_strategy(
    schema: JsonSchema,
    generation_config: GenerationConfig,
    validator_cls: type[jsonschema_rs.Validator],
    *,
    location: ParameterLocation | None = None,
    media_type: str | None = None,
) -> st.SearchStrategy[JsonValue]:
    """Strategy for a fully modeled document; raises `UnsupportedSchema` when the schema is not one."""
    if location is not None and location.is_in_header:
        # What a header may carry is a rule about characters, so it is spelled as one: every string
        # in the container obeys it, and the length and pattern keywords around it keep working.
        if location == ParameterLocation.COOKIE:
            alphabet = cookie_alphabet(generation_config)
        else:
            alphabet = header_alphabet(generation_config)
        formats = _build_header_formats(generation_config, GenerationMode.POSITIVE)
    else:
        alphabet = Alphabet(allow_x00=generation_config.allow_x00, codec=generation_config.codec)
        formats = _build_custom_formats(generation_config, GenerationMode.POSITIVE)
    return build(
        schema,
        draft=CANONICALIZE_DRAFT_BY_VALIDATOR[validator_cls],
        formats=formats,
        format_lengths=format_lengths_for(formats),
        alphabet=alphabet,
        # Every other carrier renders the value as text, where `2.0` is a different string than `2`.
        whole_floats=_carries_json(media_type),
    )


def _carries_json(media_type: str | None) -> bool:
    if media_type is None:
        return False
    try:
        return media_types.is_json(media_type)
    except MalformedMediaType:
        return False


def _build_header_formats(generation_config: GenerationConfig, mode: GenerationMode) -> dict[str, st.SearchStrategy]:
    """Format generators for a header container, minus the one the alphabet replaces.

    Leaving the plain header-value generator in would hand every plain string an opaque strategy
    that no length or pattern keyword can steer.
    """
    cache_key = (id(generation_config), mode, "header")
    cached = custom_formats_cache.get(cache_key)
    if cached is not MISSING:
        return cached
    formats = {
        name: strategy
        for name, strategy in _build_custom_formats(generation_config, mode).items()
        if name != HEADER_FORMAT
    }
    custom_formats_cache[cache_key] = formats
    return formats


def _can_skip_header_filter(schema: dict[str, Any]) -> bool:
    # All headers should have a known format key in order to avoid the header filter.
    # A header written as a boolean names no format, and claims either every value or none.
    return all(
        isinstance(sub_schema, dict) and sub_schema.get("format") in PLAIN_HEADER_FORMATS
        for sub_schema in schema.get("properties", {}).values()
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
    return st.deferred(
        lambda: negative_schema(
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
