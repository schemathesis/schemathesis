from __future__ import annotations

import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any, cast
from urllib.parse import quote_plus

import jsonschema.protocols
from hypothesis import event, note, reject
from hypothesis import strategies as st
from hypothesis_jsonschema import from_schema
from requests.structures import CaseInsensitiveDict

from schemathesis.config import GenerationConfig
from schemathesis.core import NOT_SET, media_types
from schemathesis.core.control import SkipTest
from schemathesis.core.errors import SERIALIZERS_SUGGESTION_MESSAGE, MalformedMediaType, SerializationNotPossible
from schemathesis.core.jsonschema.types import JsonSchema
from schemathesis.core.parameters import ParameterLocation
from schemathesis.core.transport import prepare_urlencoded
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
from schemathesis.openapi.generation.filters import is_valid_urlencoded
from schemathesis.resources import ExtraDataSource
from schemathesis.schemas import APIOperation
from schemathesis.specs.openapi.adapter.parameters import FORM_MEDIA_TYPES, OpenApiBody, OpenApiParameterSet
from schemathesis.specs.openapi.negative.mutations import MutationMetadata
from schemathesis.specs.openapi.negative.utils import is_binary_format

from ... import auths
from ...generation import GenerationMode
from ...hooks import HookContext, HookDispatcher, apply_to_all_dispatchers
from .formats import (
    DEFAULT_HEADER_EXCLUDE_CHARACTERS,
    HEADER_FORMAT,
    STRING_FORMATS,
    get_default_format_strategies,
    header_values,
)
from .media_types import MEDIA_TYPES
from .negative import GeneratedValue, negative_schema
from .negative.utils import can_negate

SLASH = "/"
StrategyFactory = Callable[
    [JsonSchema, str, ParameterLocation, str | None, GenerationConfig, type[jsonschema.protocols.Validator]],
    st.SearchStrategy,
]


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
    start = time.monotonic()

    generation_config = operation.schema.config.generation_for(operation=operation, phase=phase.value)

    ctx = HookContext(operation=operation)

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
    )

    if body is NOT_SET:
        if operation.body:
            body_generator = generation_mode
            if generation_mode.is_negative:
                # Consider only schemas that are possible to negate
                candidates = [item for item in operation.body.items if can_negate(item.optimized_schema)]
                # Not possible to negate body, fallback to positive data generation
                if not candidates:
                    candidates = operation.body.items
                    body_generator = GenerationMode.POSITIVE
            else:
                candidates = operation.body.items
            parameter = draw(st.sampled_from(candidates))
            strategy = _get_body_strategy(
                parameter, operation, generation_config, draw, body_generator, extra_data_source=extra_data_source
            )
            strategy = apply_hooks(operation, ctx, hooks, strategy, ParameterLocation.BODY)
            # Parameter may have a wildcard media type. In this case, choose any supported one
            possible_media_types = sorted(
                operation.schema.transport.get_matching_media_types(parameter.media_type), key=lambda x: x[0]
            )
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
            if media_type is not None and media_types.parse(media_type) == (
                "application",
                "x-www-form-urlencoded",
            ):
                # Helper to transform FormBodyWithContentTypes while preserving it
                def prepare_urlencoded_form(x: Any) -> Any:
                    if isinstance(x, FormBodyWithContentTypes):
                        return FormBodyWithContentTypes(body=prepare_urlencoded(x.body), content_types=x.content_types)
                    return prepare_urlencoded(x)

                def is_valid_urlencoded_form(x: Any) -> bool:
                    if isinstance(x, FormBodyWithContentTypes):
                        return is_valid_urlencoded(x.body)
                    return is_valid_urlencoded(x)

                if body_generator.is_negative:
                    # For negative strategies, unwrap GeneratedValue, apply transformation, then rewrap
                    strategy = strategy.map(
                        lambda x: GeneratedValue(prepare_urlencoded_form(x.value), x.meta)
                        if isinstance(x, GeneratedValue)
                        else prepare_urlencoded_form(x)
                    ).filter(lambda x: is_valid_urlencoded_form(x.value if isinstance(x, GeneratedValue) else x))
                else:
                    strategy = strategy.map(prepare_urlencoded_form).filter(is_valid_urlencoded_form)
            body_result = draw(strategy)
            body_metadata = None
            # Negative strategy returns GeneratedValue, positive returns just value
            if isinstance(body_result, GeneratedValue):
                body_metadata = body_result.meta
                body_result = body_result.value
            body_ = ValueContainer(value=body_result, location="body", generator=body_generator, meta=body_metadata)
        else:
            body_ = ValueContainer(value=body, location="body", generator=None, meta=None)
    else:
        # This explicit body payload comes for a media type that has a custom strategy registered
        # Such strategies only support binary payloads, otherwise they can't be serialized
        if not isinstance(body, bytes) and media_type and _find_media_type_strategy(media_type) is not None:
            all_media_types = operation.get_request_payload_content_types()
            raise SerializationNotPossible.from_media_types(*all_media_types)
        body_ = ValueContainer(value=body, location="body", generator=None, meta=None)

    # If we need to generate negative cases but no generated values were negated, then skip the whole test
    if generation_mode.is_negative and not any_negated_values([query_, cookies_, headers_, path_parameters_, body_]):
        if generation_config.modes == [GenerationMode.NEGATIVE]:
            raise SkipTest("Impossible to generate negative test cases")
        else:
            reject()

    # Extract mutation metadata from negated values and create phase-appropriate data
    if generation_mode.is_negative:
        negated_container = None
        for container in [query_, cookies_, headers_, path_parameters_, body_]:
            if container.generator == GenerationMode.NEGATIVE and container.meta is not None:
                negated_container = container
                break

        if negated_container and negated_container.meta:
            metadata = negated_container.meta
            location_map = {
                "query": ParameterLocation.QUERY,
                "path": ParameterLocation.PATH,
                "header": ParameterLocation.HEADER,
                "cookie": ParameterLocation.COOKIE,
                "body": ParameterLocation.BODY,
            }
            parameter_location = location_map.get(negated_container.location)
            _phase_data = {
                TestPhase.EXAMPLES: ExamplesPhaseData(
                    description=metadata.description,
                    parameter=metadata.parameter,
                    parameter_location=parameter_location,
                    location=metadata.location,
                ),
                TestPhase.FUZZING: FuzzingPhaseData(
                    description=metadata.description,
                    parameter=metadata.parameter,
                    parameter_location=parameter_location,
                    location=metadata.location,
                ),
                TestPhase.STATEFUL: StatefulPhaseData(
                    description=metadata.description,
                    parameter=metadata.parameter,
                    parameter_location=parameter_location,
                    location=metadata.location,
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
                time=time.monotonic() - start,
                mode=generation_mode,
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


def _maybe_set_optional_body(
    strategy: st.SearchStrategy,
    parameter: OpenApiBody,
    draw: st.DrawFn,
) -> st.SearchStrategy:
    """Add NOT_SET option to strategy for optional body parameters."""
    if (
        not parameter.is_required
        and draw(st.floats(min_value=0.0, max_value=1.0, allow_infinity=False, allow_nan=False, allow_subnormal=False))
        < OPTIONAL_BODY_RATE
    ):
        strategy |= st.just(NOT_SET)
    return strategy


def _find_media_type_strategy(content_type: str) -> st.SearchStrategy[bytes] | None:
    """Find a registered strategy for a content type, supporting wildcard patterns."""
    # Try exact match first
    if content_type in MEDIA_TYPES:
        return MEDIA_TYPES[content_type]

    try:
        main, sub = media_types.parse(content_type)
    except MalformedMediaType:
        return None

    # Check registered media types for wildcard matches
    for registered_type, strategy in MEDIA_TYPES.items():
        try:
            target_main, target_sub = media_types.parse(registered_type)
        except MalformedMediaType:
            continue
        # Match if both main and sub types are compatible
        # "*" in either the requested or registered type acts as a wildcard
        main_match = main == "*" or target_main == "*" or main == target_main
        sub_match = sub == "*" or target_sub == "*" or sub == target_sub
        if main_match and sub_match:
            return strategy

    return None


def _build_form_strategy_with_encoding(
    parameter: OpenApiBody,
    operation: APIOperation,
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
                strategy = _find_media_type_strategy(ct)
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
            from schemathesis.specs.openapi.schemas import OpenApiSchema

            assert isinstance(operation.schema, OpenApiSchema)
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
    optional_strategies = {k: st.just(NOT_SET) | v for k, v in property_strategies.items() if k not in required}

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
) -> st.SearchStrategy:
    # Check for custom encoding in form bodies (multipart/form-data or application/x-www-form-urlencoded)
    if parameter.media_type in FORM_MEDIA_TYPES:
        custom_strategy = _build_form_strategy_with_encoding(parameter, operation, generation_config, generation_mode)
        if custom_strategy is not None:
            return custom_strategy

    # Check for custom media type strategy
    custom_strategy = _find_media_type_strategy(parameter.media_type)
    if custom_strategy is not None:
        # Always use custom strategies for raw bodies - they produce transmittable bytes.
        # In negative mode, bypassing them would generate non-bytes values (e.g., integers)
        # that can't be sent over HTTP for raw binary media types like application/x-tar.
        return custom_strategy

    # Use the cached strategy from the parameter
    strategy = parameter.get_strategy(
        operation, generation_config, generation_mode, extra_data_source=extra_data_source
    )
    return _maybe_set_optional_body(strategy, parameter, draw)


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
) -> tuple[dict[str, Any] | None, Any]:
    """Get the final value for the specified location.

    If the value is not set, then generate it from the relevant strategy. Otherwise, check what is missing in it and
    generate those parts.
    """
    if value is None:
        strategy = get_parameters_strategy(
            operation, generation_mode, location, generation_config, extra_data_source=extra_data_source
        )
        strategy = apply_hooks(operation, ctx, hooks, strategy, location)
        result = draw(strategy)
        # Negative strategy returns GeneratedValue, positive returns just value
        if isinstance(result, GeneratedValue):
            return result.value, result.meta
        return result, None
    strategy = get_parameters_strategy(
        operation,
        generation_mode,
        location,
        generation_config,
        exclude=value.keys(),
        extra_data_source=extra_data_source,
    )
    strategy = apply_hooks(operation, ctx, hooks, strategy, location)
    new = draw(strategy)
    metadata = None
    # Negative strategy returns GeneratedValue, positive returns just value
    if isinstance(new, GeneratedValue):
        new, metadata = new.value, new.meta
    if new is not None:
        copied = dict(value)
        copied.update(new)
        return copied, metadata
    return value, metadata


@dataclass
class ValueContainer:
    """Container for a value generated by a data generator or explicitly provided."""

    value: Any
    location: str
    generator: GenerationMode | None
    meta: MutationMetadata | None

    __slots__ = ("value", "location", "generator", "meta")

    @property
    def is_generated(self) -> bool:
        """If value was generated."""
        return self.generator is not None and (self.location == "body" or self.value is not None)


def any_negated_values(values: list[ValueContainer]) -> bool:
    """Check if any generated values are negated."""
    return any(value.generator == GenerationMode.NEGATIVE for value in values if value.is_generated)


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
    value, metadata = get_parameters_value(
        explicit,
        location,
        draw,
        operation,
        ctx,
        hooks,
        generator,
        generation_config,
        extra_data_source=extra_data_source,
    )
    used_generator: GenerationMode | None = generator
    if value == explicit:
        # When we pass `explicit`, then its parts are excluded from generation of the final value
        # If the final value is the same, then other parameters were generated at all
        if value is not None and location == ParameterLocation.PATH:
            value = quote_all(value)
        used_generator = None
    return ValueContainer(value=value, location=location, generator=used_generator, meta=metadata)


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
    return any(
        header not in ({"type": "string"}, {"type": "string", "format": HEADER_FORMAT}) for header in headers.values()
    )


def get_parameters_strategy(
    operation: APIOperation,
    generation_mode: GenerationMode,
    location: ParameterLocation,
    generation_config: GenerationConfig,
    exclude: Iterable[str] = (),
    extra_data_source: ExtraDataSource | None = None,
) -> st.SearchStrategy:
    """Create a new strategy for the case's component from the API operation parameters."""
    container = getattr(operation, location.container_name)
    if container:
        return container.get_strategy(
            operation,
            generation_config,
            generation_mode,
            exclude,
            extra_data_source=extra_data_source,
        )
    # No parameters defined for this location
    return st.none()


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


def _build_custom_formats(generation_config: GenerationConfig) -> dict[str, st.SearchStrategy]:
    custom_formats = {**get_default_format_strategies(), **STRING_FORMATS}
    header_values_kwargs = {}
    if generation_config.exclude_header_characters is not None:
        header_values_kwargs["exclude_characters"] = generation_config.exclude_header_characters
        if not generation_config.allow_x00:
            header_values_kwargs["exclude_characters"] += "\x00"
    elif not generation_config.allow_x00:
        header_values_kwargs["exclude_characters"] = DEFAULT_HEADER_EXCLUDE_CHARACTERS + "\x00"
    if generation_config.codec is not None:
        header_values_kwargs["codec"] = generation_config.codec
    if header_values_kwargs:
        custom_formats[HEADER_FORMAT] = header_values(**header_values_kwargs)
    return custom_formats


def make_positive_strategy(
    schema: JsonSchema,
    operation_name: str,
    location: ParameterLocation,
    media_type: str | None,
    generation_config: GenerationConfig,
    validator_cls: type[jsonschema.protocols.Validator],
) -> st.SearchStrategy:
    """Strategy for generating values that fit the schema."""
    custom_formats = _build_custom_formats(generation_config)
    return from_schema(
        schema,
        custom_formats=custom_formats,
        allow_x00=generation_config.allow_x00,
        codec=generation_config.codec,
    )


def _can_skip_header_filter(schema: dict[str, Any]) -> bool:
    # All headers should contain HEADER_FORMAT in order to avoid header filter
    return all(sub_schema.get("format") == HEADER_FORMAT for sub_schema in schema.get("properties", {}).values())


def make_negative_strategy(
    schema: JsonSchema,
    operation_name: str,
    location: ParameterLocation,
    media_type: str | None,
    generation_config: GenerationConfig,
    validator_cls: type[jsonschema.protocols.Validator],
) -> st.SearchStrategy:
    custom_formats = _build_custom_formats(generation_config)
    return negative_schema(
        schema,
        operation_name=operation_name,
        location=location,
        media_type=media_type,
        custom_formats=custom_formats,
        generation_config=generation_config,
        validator_cls=validator_cls,
    )


GENERATOR_MODE_TO_STRATEGY_FACTORY = {
    GenerationMode.POSITIVE: make_positive_strategy,
    GenerationMode.NEGATIVE: make_negative_strategy,
}


def quote_all(parameters: dict[str, Any]) -> dict[str, Any]:
    """Apply URL quotation for all values in a dictionary."""
    # Even though, "." is an unreserved character, it has a special meaning in "." and ".." strings.
    # It will change the path:
    #   - http://localhost/foo/./ -> http://localhost/foo/
    #   - http://localhost/foo/../ -> http://localhost/
    # Which is not desired as we need to test precisely the original path structure.

    for key, value in parameters.items():
        if isinstance(value, str):
            if value == ".":
                parameters[key] = "%2E"
            elif value == "..":
                parameters[key] = "%2E%2E"
            else:
                parameters[key] = quote_plus(value)
    return parameters


def apply_hooks(
    operation: APIOperation,
    ctx: HookContext,
    hooks: HookDispatcher | None,
    strategy: st.SearchStrategy,
    location: ParameterLocation,
) -> st.SearchStrategy:
    """Apply all hooks related to the given location."""
    return apply_to_all_dispatchers(operation, ctx, hooks, strategy, location.container_name)
