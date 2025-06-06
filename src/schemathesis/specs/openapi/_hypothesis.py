from __future__ import annotations

import time
from contextlib import suppress
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, Optional, Union, cast
from urllib.parse import quote_plus
from weakref import WeakKeyDictionary

import jsonschema.protocols
from hypothesis import event, note, reject
from hypothesis import strategies as st
from hypothesis_jsonschema import from_schema
from requests.structures import CaseInsensitiveDict

from schemathesis.config import GenerationConfig
from schemathesis.core import NOT_SET, NotSet, media_types
from schemathesis.core.control import SkipTest
from schemathesis.core.errors import SERIALIZERS_SUGGESTION_MESSAGE, SerializationNotPossible
from schemathesis.core.transforms import deepclone
from schemathesis.core.transport import prepare_urlencoded
from schemathesis.generation.meta import (
    CaseMetadata,
    ComponentInfo,
    ComponentKind,
    ExplicitPhaseData,
    GeneratePhaseData,
    GenerationInfo,
    PhaseInfo,
    TestPhase,
)
from schemathesis.openapi.generation.filters import is_valid_header, is_valid_path, is_valid_query, is_valid_urlencoded
from schemathesis.schemas import APIOperation

from ... import auths
from ...generation import GenerationMode
from ...hooks import HookContext, HookDispatcher, apply_to_all_dispatchers
from .constants import LOCATION_TO_CONTAINER
from .formats import HEADER_FORMAT, STRING_FORMATS, get_default_format_strategies, header_values
from .media_types import MEDIA_TYPES
from .negative import negative_schema
from .negative.utils import can_negate
from .parameters import OpenAPIBody, OpenAPIParameter, parameters_to_json_schema
from .utils import is_header_location

SLASH = "/"
StrategyFactory = Callable[
    [Dict[str, Any], str, str, Optional[str], GenerationConfig, type[jsonschema.protocols.Validator]], st.SearchStrategy
]


@st.composite  # type: ignore
def openapi_cases(
    draw: Callable,
    *,
    operation: APIOperation,
    hooks: HookDispatcher | None = None,
    auth_storage: auths.AuthStorage | None = None,
    generation_mode: GenerationMode = GenerationMode.POSITIVE,
    path_parameters: NotSet | dict[str, Any] = NOT_SET,
    headers: NotSet | dict[str, Any] = NOT_SET,
    cookies: NotSet | dict[str, Any] = NOT_SET,
    query: NotSet | dict[str, Any] = NOT_SET,
    body: Any = NOT_SET,
    media_type: str | None = None,
    phase: TestPhase = TestPhase.FUZZING,
    __is_stateful_phase: bool = False,
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
    strategy_factory = GENERATOR_MODE_TO_STRATEGY_FACTORY[generation_mode]

    phase_name = "stateful" if __is_stateful_phase else phase.value
    generation_config = operation.schema.config.generation_for(operation=operation, phase=phase_name)

    ctx = HookContext(operation=operation)

    path_parameters_ = generate_parameter(
        "path", path_parameters, operation, draw, ctx, hooks, generation_mode, generation_config
    )
    headers_ = generate_parameter("header", headers, operation, draw, ctx, hooks, generation_mode, generation_config)
    cookies_ = generate_parameter("cookie", cookies, operation, draw, ctx, hooks, generation_mode, generation_config)
    query_ = generate_parameter("query", query, operation, draw, ctx, hooks, generation_mode, generation_config)

    if body is NOT_SET:
        if operation.body:
            body_generator = generation_mode
            if generation_mode.is_negative:
                # Consider only schemas that are possible to negate
                candidates = [item for item in operation.body.items if can_negate(item.as_json_schema(operation))]
                # Not possible to negate body, fallback to positive data generation
                if not candidates:
                    candidates = operation.body.items
                    strategy_factory = make_positive_strategy
                    body_generator = GenerationMode.POSITIVE
            else:
                candidates = operation.body.items
            parameter = draw(st.sampled_from(candidates))
            strategy = _get_body_strategy(parameter, strategy_factory, operation, generation_config)
            strategy = apply_hooks(operation, ctx, hooks, strategy, "body")
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
                    raise SerializationNotPossible.from_media_types(*all_media_types)
                # Other media types are possible - avoid choosing this media type in the future
                event_text = f"Can't serialize data to `{parameter.media_type}`."
                note(f"{event_text} {SERIALIZERS_SUGGESTION_MESSAGE}")
                event(event_text)
                reject()  # type: ignore
            media_type, _ = draw(st.sampled_from(possible_media_types))
            if media_type is not None and media_types.parse(media_type) == (
                "application",
                "x-www-form-urlencoded",
            ):
                strategy = strategy.map(prepare_urlencoded).filter(is_valid_urlencoded)
            body_ = ValueContainer(value=draw(strategy), location="body", generator=body_generator)
        else:
            body_ = ValueContainer(value=body, location="body", generator=None)
    else:
        # This explicit body payload comes for a media type that has a custom strategy registered
        # Such strategies only support binary payloads, otherwise they can't be serialized
        if not isinstance(body, bytes) and media_type in MEDIA_TYPES:
            all_media_types = operation.get_request_payload_content_types()
            raise SerializationNotPossible.from_media_types(*all_media_types)
        body_ = ValueContainer(value=body, location="body", generator=None)

    # If we need to generate negative cases but no generated values were negated, then skip the whole test
    if generation_mode.is_negative and not any_negated_values([query_, cookies_, headers_, path_parameters_, body_]):
        if generation_config.modes == [GenerationMode.NEGATIVE]:
            raise SkipTest("Impossible to generate negative test cases")
        else:
            reject()

    _phase_data = {
        TestPhase.EXAMPLES: ExplicitPhaseData(),
        TestPhase.FUZZING: GeneratePhaseData(),
    }[phase]
    phase_data = cast(Union[ExplicitPhaseData, GeneratePhaseData], _phase_data)

    instance = operation.Case(
        media_type=media_type,
        path_parameters=path_parameters_.value or {},
        headers=headers_.value or CaseInsensitiveDict(),
        cookies=cookies_.value or {},
        query=query_.value or {},
        body=body_.value,
        _meta=CaseMetadata(
            generation=GenerationInfo(
                time=time.monotonic() - start,
                mode=generation_mode,
            ),
            phase=PhaseInfo(name=phase, data=phase_data),
            components={
                kind: ComponentInfo(mode=value.generator)
                for kind, value in [
                    (ComponentKind.QUERY, query_),
                    (ComponentKind.PATH_PARAMETERS, path_parameters_),
                    (ComponentKind.HEADERS, headers_),
                    (ComponentKind.COOKIES, cookies_),
                    (ComponentKind.BODY, body_),
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


_BODY_STRATEGIES_CACHE: WeakKeyDictionary = WeakKeyDictionary()


def _get_body_strategy(
    parameter: OpenAPIBody,
    strategy_factory: StrategyFactory,
    operation: APIOperation,
    generation_config: GenerationConfig,
) -> st.SearchStrategy:
    from schemathesis.specs.openapi.schemas import BaseOpenAPISchema

    if parameter.media_type in MEDIA_TYPES:
        return MEDIA_TYPES[parameter.media_type]
    # The cache key relies on object ids, which means that the parameter should not be mutated
    # Note, the parent schema is not included as each parameter belong only to one schema
    if parameter in _BODY_STRATEGIES_CACHE and strategy_factory in _BODY_STRATEGIES_CACHE[parameter]:
        return _BODY_STRATEGIES_CACHE[parameter][strategy_factory]
    schema = parameter.as_json_schema(operation)
    schema = operation.schema.prepare_schema(schema)
    assert isinstance(operation.schema, BaseOpenAPISchema)
    strategy = strategy_factory(
        schema, operation.label, "body", parameter.media_type, generation_config, operation.schema.validator_cls
    )
    if not parameter.is_required:
        strategy |= st.just(NOT_SET)
    _BODY_STRATEGIES_CACHE.setdefault(parameter, {})[strategy_factory] = strategy
    return strategy


def get_parameters_value(
    value: NotSet | dict[str, Any],
    location: str,
    draw: Callable,
    operation: APIOperation,
    ctx: HookContext,
    hooks: HookDispatcher | None,
    strategy_factory: StrategyFactory,
    generation_config: GenerationConfig,
) -> dict[str, Any] | None:
    """Get the final value for the specified location.

    If the value is not set, then generate it from the relevant strategy. Otherwise, check what is missing in it and
    generate those parts.
    """
    if isinstance(value, NotSet) or not value:
        strategy = get_parameters_strategy(operation, strategy_factory, location, generation_config)
        strategy = apply_hooks(operation, ctx, hooks, strategy, location)
        return draw(strategy)
    strategy = get_parameters_strategy(operation, strategy_factory, location, generation_config, exclude=value.keys())
    strategy = apply_hooks(operation, ctx, hooks, strategy, location)
    new = draw(strategy)
    if new is not None:
        copied = deepclone(value)
        copied.update(new)
        return copied
    return value


_PARAMETER_STRATEGIES_CACHE: WeakKeyDictionary = WeakKeyDictionary()


@dataclass
class ValueContainer:
    """Container for a value generated by a data generator or explicitly provided."""

    value: Any
    location: str
    generator: GenerationMode | None

    __slots__ = ("value", "location", "generator")

    @property
    def is_generated(self) -> bool:
        """If value was generated."""
        return self.generator is not None and (self.location == "body" or self.value is not None)


def any_negated_values(values: list[ValueContainer]) -> bool:
    """Check if any generated values are negated."""
    return any(value.generator == GenerationMode.NEGATIVE for value in values if value.is_generated)


def generate_parameter(
    location: str,
    explicit: NotSet | dict[str, Any],
    operation: APIOperation,
    draw: Callable,
    ctx: HookContext,
    hooks: HookDispatcher | None,
    generator: GenerationMode,
    generation_config: GenerationConfig,
) -> ValueContainer:
    """Generate a value for a parameter.

    Fallback to positive data generator if parameter can not be negated.
    """
    if generator.is_negative and (
        (location == "path" and not can_negate_path_parameters(operation))
        or (is_header_location(location) and not can_negate_headers(operation, location))
    ):
        # If we can't negate any parameter, generate positive ones
        # If nothing else will be negated, then skip the test completely
        strategy_factory = make_positive_strategy
        generator = GenerationMode.POSITIVE
    else:
        strategy_factory = GENERATOR_MODE_TO_STRATEGY_FACTORY[generator]
    value = get_parameters_value(explicit, location, draw, operation, ctx, hooks, strategy_factory, generation_config)
    used_generator: GenerationMode | None = generator
    if value == explicit:
        # When we pass `explicit`, then its parts are excluded from generation of the final value
        # If the final value is the same, then other parameters were generated at all
        used_generator = None
    return ValueContainer(value=value, location=location, generator=used_generator)


def can_negate_path_parameters(operation: APIOperation) -> bool:
    """Check if any path parameter can be negated."""
    schema = parameters_to_json_schema(operation, operation.path_parameters)
    # No path parameters to negate
    parameters = schema["properties"]
    if not parameters:
        return True
    return any(can_negate(parameter) for parameter in parameters.values())


def can_negate_headers(operation: APIOperation, location: str) -> bool:
    """Check if any header can be negated."""
    parameters = getattr(operation, LOCATION_TO_CONTAINER[location])
    schema = parameters_to_json_schema(operation, parameters)
    # No headers to negate
    headers = schema["properties"]
    if not headers:
        return True
    return any(header != {"type": "string"} for header in headers.values())


def get_schema_for_location(
    operation: APIOperation, location: str, parameters: Iterable[OpenAPIParameter]
) -> dict[str, Any]:
    schema = parameters_to_json_schema(operation, parameters)
    if location == "path":
        schema["required"] = list(schema["properties"])
        for prop in schema.get("properties", {}).values():
            if prop.get("type") == "string":
                prop.setdefault("minLength", 1)
    return operation.schema.prepare_schema(schema)


def get_parameters_strategy(
    operation: APIOperation,
    strategy_factory: StrategyFactory,
    location: str,
    generation_config: GenerationConfig,
    exclude: Iterable[str] = (),
) -> st.SearchStrategy:
    """Create a new strategy for the case's component from the API operation parameters."""
    from schemathesis.specs.openapi.schemas import BaseOpenAPISchema

    parameters = getattr(operation, LOCATION_TO_CONTAINER[location])
    if parameters:
        # The cache key relies on object ids, which means that the parameter should not be mutated
        nested_cache_key = (strategy_factory, location, tuple(sorted(exclude)))
        if operation in _PARAMETER_STRATEGIES_CACHE and nested_cache_key in _PARAMETER_STRATEGIES_CACHE[operation]:
            return _PARAMETER_STRATEGIES_CACHE[operation][nested_cache_key]
        schema = get_schema_for_location(operation, location, parameters)
        if location == "header" and exclude:
            # Remove excluded headers case-insensitively
            exclude_lower = {name.lower() for name in exclude}
            schema["properties"] = {
                key: value for key, value in schema["properties"].items() if key.lower() not in exclude_lower
            }
            if "required" in schema:
                schema["required"] = [key for key in schema["required"] if key.lower() not in exclude_lower]
        elif exclude:
            # Non-header locations: remove by exact name
            for name in exclude:
                schema["properties"].pop(name, None)
                with suppress(ValueError):
                    schema["required"].remove(name)
        if not schema["properties"] and strategy_factory is make_negative_strategy:
            # Nothing to negate - all properties were excluded
            strategy = st.none()
        else:
            assert isinstance(operation.schema, BaseOpenAPISchema)
            strategy = strategy_factory(
                schema, operation.label, location, None, generation_config, operation.schema.validator_cls
            )
            serialize = operation.get_parameter_serializer(location)
            if serialize is not None:
                strategy = strategy.map(serialize)
            filter_func = {
                "path": is_valid_path,
                "header": is_valid_header,
                "cookie": is_valid_header,
                "query": is_valid_query,
            }[location]
            # Headers with special format do not need filtration
            if not (is_header_location(location) and _can_skip_header_filter(schema)):
                strategy = strategy.filter(filter_func)
            # Path & query parameters will be cast to string anyway, but having their JSON equivalents for
            # `True` / `False` / `None` improves chances of them passing validation in apps
            # that expect boolean / null types
            # and not aware of Python-specific representation of those types
            if location == "path":
                strategy = strategy.map(quote_all).map(jsonify_python_specific_types)
            elif location == "query":
                strategy = strategy.map(jsonify_python_specific_types)
        _PARAMETER_STRATEGIES_CACHE.setdefault(operation, {})[nested_cache_key] = strategy
        return strategy
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


def _build_custom_formats(
    custom_formats: dict[str, st.SearchStrategy] | None, generation_config: GenerationConfig
) -> dict[str, st.SearchStrategy]:
    custom_formats = {**get_default_format_strategies(), **STRING_FORMATS, **(custom_formats or {})}
    if generation_config.exclude_header_characters is not None:
        custom_formats[HEADER_FORMAT] = header_values(exclude_characters=generation_config.exclude_header_characters)
    elif not generation_config.allow_x00:
        custom_formats[HEADER_FORMAT] = header_values(exclude_characters="\n\r\x00")
    return custom_formats


def make_positive_strategy(
    schema: dict[str, Any],
    operation_name: str,
    location: str,
    media_type: str | None,
    generation_config: GenerationConfig,
    validator_cls: type[jsonschema.protocols.Validator],
    custom_formats: dict[str, st.SearchStrategy] | None = None,
) -> st.SearchStrategy:
    """Strategy for generating values that fit the schema."""
    if is_header_location(location):
        # We try to enforce the right header values via "format"
        # This way, only allowed values will be used during data generation, which reduces the amount of filtering later
        # If a property schema contains `pattern` it leads to heavy filtering and worse performance - therefore, skip it
        for sub_schema in schema.get("properties", {}).values():
            if list(sub_schema) == ["type"] and sub_schema["type"] == "string":
                sub_schema.setdefault("format", HEADER_FORMAT)
    custom_formats = _build_custom_formats(custom_formats, generation_config)
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
    schema: dict[str, Any],
    operation_name: str,
    location: str,
    media_type: str | None,
    generation_config: GenerationConfig,
    validator_cls: type[jsonschema.protocols.Validator],
    custom_formats: dict[str, st.SearchStrategy] | None = None,
) -> st.SearchStrategy:
    custom_formats = _build_custom_formats(custom_formats, generation_config)
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
    location: str,
) -> st.SearchStrategy:
    """Apply all hooks related to the given location."""
    container = LOCATION_TO_CONTAINER[location]
    return apply_to_all_dispatchers(operation, ctx, hooks, strategy, container)
