"""Operation-level coverage.

Enumerate cases for an OpenAPI operation by combining parameter, header, body, and
response coverage into concrete `Case` instances.
"""

from __future__ import annotations

from collections.abc import Callable, Generator
from dataclasses import dataclass
from itertools import combinations
from time import perf_counter
from typing import TYPE_CHECKING, Any, TypeGuard

from schemathesis.core import NOT_SET, NotSet, media_types
from schemathesis.core.errors import InvalidSchema, MalformedMediaType
from schemathesis.core.jsonschema import make_validator
from schemathesis.core.media_types import FORM_MEDIA_TYPES, MEDIA_TYPE_STRATEGIES, find_media_type_strategy
from schemathesis.core.parameters import CONTAINER_TO_LOCATION, ParameterLocation
from schemathesis.core.transforms import deepclone
from schemathesis.generation import GenerationMode
from schemathesis.generation.case import Case
from schemathesis.generation.hypothesis import examples
from schemathesis.generation.hypothesis._response_matching import find_matching_in_responses
from schemathesis.generation.hypothesis.builder import _case_to_kwargs
from schemathesis.generation.meta import (
    CaseMetadata,
    ComponentInfo,
    CoveragePhaseData,
    CoverageScenario,
    GenerationInfo,
    PhaseInfo,
)
from schemathesis.specs.openapi.adapter.parameters import OpenApiParameterSet
from schemathesis.specs.openapi.coverage._schema import CoverageContext, GeneratedValue, HashSet, cover_schema_iter
from schemathesis.specs.openapi.error_feedback import apply_adjustments
from schemathesis.transport.serialization import quote_all

if TYPE_CHECKING:
    from schemathesis.config import GenerationConfig
    from schemathesis.core.error_feedback import ErrorFeedbackStore
    from schemathesis.resources import ExtraDataSource, PoolDraw
    from schemathesis.schemas import APIOperation, ParameterSet
    from schemathesis.specs.openapi.adapter.parameters import OpenApiBody


class Instant:
    __slots__ = ("start",)

    def __init__(self) -> None:
        self.start = perf_counter()

    @property
    def elapsed(self) -> float:
        return perf_counter() - self.start


class Template:
    __slots__ = ("_components", "_template", "_serializers")

    def __init__(self, serializers: dict[str, Callable]) -> None:
        self._components: dict[ParameterLocation, ComponentInfo] = {}
        self._template: dict[str, Any] = {}
        self._serializers = serializers

    def __contains__(self, key: str) -> bool:
        return key in self._template

    def __getitem__(self, key: str) -> dict:
        return self._template[key]

    def get(self, key: str, default: Any = None) -> dict:
        return self._template.get(key, default)

    def add_parameter(self, location: ParameterLocation, name: str, value: GeneratedValue) -> None:
        info = self._components.get(location)
        if info is None:
            self._components[location] = ComponentInfo(mode=value.generation_mode)
        elif value.generation_mode == GenerationMode.NEGATIVE:
            info.mode = GenerationMode.NEGATIVE

        container = self._template.setdefault(location.container_name, {})
        container[name] = value.value

    def set_body(self, body: GeneratedValue, media_type: str) -> None:
        self._template["body"] = body.value
        self._template["media_type"] = media_type
        self._components[ParameterLocation.BODY] = ComponentInfo(mode=body.generation_mode)

    def _serialize(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        output = {}
        for container_name, value in kwargs.items():
            serializer = self._serializers.get(container_name)
            if container_name in ("headers", "cookies") and isinstance(value, dict):
                value = _stringify_value(value, container_name)
            if serializer is not None:
                # Shallow-copy dict containers before serializing to avoid mutating
                # self._template through shared references in shallow-copy kwargs
                if isinstance(value, dict):
                    value = dict(value)
                value = serializer(value)
            if container_name == "query" and isinstance(value, dict):
                value = _stringify_value(value, container_name)
            if container_name == "path_parameters" and isinstance(value, dict):
                # dict() copy prevents quote_all from mutating self._template
                value = _stringify_value(quote_all(dict(value)), container_name)
            output[container_name] = value
        return output

    def unmodified(self) -> TemplateValue:
        raw = deepclone(self._template)
        kwargs = self._serialize(raw)
        return TemplateValue(kwargs=kwargs, raw=raw, components=self._components.copy())

    def with_body(self, *, media_type: str, value: GeneratedValue) -> TemplateValue:
        raw = {**self._template, "media_type": media_type, "body": value.value}
        kwargs = self._serialize(raw)
        components = {**self._components, ParameterLocation.BODY: ComponentInfo(mode=value.generation_mode)}
        return TemplateValue(kwargs=kwargs, raw=raw, components=components)

    def with_parameter(self, *, location: ParameterLocation, name: str, value: GeneratedValue) -> TemplateValue:
        container = self._template[location.container_name]
        return self.with_location(
            location=location,
            value={**container, name: value.value},
            generation_mode=value.generation_mode,
        )

    def with_location(
        self, *, location: ParameterLocation, value: Any, generation_mode: GenerationMode
    ) -> TemplateValue:
        raw = {**self._template, location.container_name: value}
        components = {**self._components, location: ComponentInfo(mode=generation_mode)}
        kwargs = self._serialize(raw)
        return TemplateValue(kwargs=kwargs, raw=raw, components=components)


@dataclass(slots=True)
class TemplateValue:
    kwargs: dict[str, Any]
    raw: dict[str, Any]
    components: dict[ParameterLocation, ComponentInfo]


def _stringify_value(val: Any, container_name: str) -> Any:
    if val is None:
        return "null"
    if val is True:
        return "true"
    if val is False:
        return "false"
    if isinstance(val, int | float):
        return str(val)
    if isinstance(val, list):
        if container_name == "query":
            # Having a list here ensures there will be multiple query parameters wit the same name
            return [_stringify_value(item, container_name) for item in val]
        # use comma-separated values style for arrays
        return ",".join(str(_stringify_value(sub, container_name)) for sub in val)
    if isinstance(val, dict):
        return {key: _stringify_value(sub, container_name) for key, sub in val.items()}
    return val


_GATING_KEYS = frozenset({"example", "examples", "default", "enum", "const"})


def _is_pool_eligible(schema: object) -> TypeGuard[dict[str, Any]]:
    return isinstance(schema, dict) and not (_GATING_KEYS & schema.keys())


class _NestedOverlay:
    """Sentinel distinguishing per-leaf sub-field overlays from raw pool object values."""

    __slots__ = ("fields",)

    def __init__(self, fields: dict[str, Any]) -> None:
        self.fields = fields


def _body_pool_overlays(
    *,
    correlated: dict[tuple[ParameterLocation, str], Any],
    body_schema: Any,
    validator_cls: type,
) -> dict[str, Any]:
    """Return pool overlay values for body properties valid against the destination schema."""
    if not isinstance(body_schema, dict):
        return {}
    properties = body_schema.get("properties")
    if not isinstance(properties, dict):
        return {}
    overlays: dict[str, Any] = {}
    for prop_name, prop_schema in properties.items():
        if _is_pool_eligible(prop_schema):
            value = correlated.get((ParameterLocation.BODY, prop_name))
            if value is not None:
                try:
                    if make_validator(prop_schema, validator_cls).is_valid(value):
                        overlays[prop_name] = value
                        continue
                except Exception:
                    pass
        # Fall through to the nested branch even when the top-level lookup misses:
        # an object-typed property is pool-eligible but its overlay key lives one level deeper.
        if isinstance(prop_schema, dict) and isinstance(prop_schema.get("properties"), dict):
            nested = _nested_body_pool_overlay(
                correlated=correlated, outer_name=prop_name, inner_schema=prop_schema, validator_cls=validator_cls
            )
            if nested:
                overlays[prop_name] = _NestedOverlay(nested)
    return overlays


def _nested_body_pool_overlay(
    *,
    correlated: dict[tuple[ParameterLocation, str], Any],
    outer_name: str,
    inner_schema: dict[str, Any],
    validator_cls: type,
) -> dict[str, Any]:
    inner_props = inner_schema.get("properties")
    assert isinstance(inner_props, dict), "caller must validate inner_schema['properties'] is a dict"
    inner: dict[str, Any] = {}
    for sub_name, sub_schema in inner_props.items():
        if not _is_pool_eligible(sub_schema):
            continue
        value = correlated.get((ParameterLocation.BODY, f"{outer_name}/{sub_name}"))
        if value is None:
            continue
        try:
            if not make_validator(sub_schema, validator_cls).is_valid(value):
                continue
        except Exception:
            continue
        inner[sub_name] = value
    return inner


def _generate_coverage_values_from_custom_strategy(
    media_type: str,
) -> Generator[GeneratedValue, None, None]:
    """Generate coverage values from a custom media type strategy."""
    strategy = find_media_type_strategy(media_type)
    if strategy is None:
        return

    value: str | bytes = examples.generate_one(strategy)
    yield GeneratedValue.with_positive(
        value=value,
        scenario=CoverageScenario.EXAMPLE_VALUE,
        description=f"Custom media type: {media_type}",
    )


def _generate_multipart_body_from_custom_strategies(body: OpenApiBody) -> dict[str, Any] | None:
    """Generate a body dict for multipart forms using custom encoding strategies.

    Returns None if the body doesn't have custom encoding strategies or isn't a form type.
    """
    if body.media_type not in FORM_MEDIA_TYPES:
        return None

    schema = body.definition.get("schema", {})
    properties = schema.get("properties", {})
    required = schema.get("required", [])

    result: dict[str, Any] = {}
    has_custom_strategy = False

    for prop_name in properties:
        content_type = body.get_property_content_type(prop_name)
        if not content_type:
            continue

        content_types = content_type if isinstance(content_type, list) else content_type.split(",")
        for ct in content_types:
            strategy = find_media_type_strategy(ct.strip())
            if strategy is not None:
                result[prop_name] = examples.generate_one(strategy)
                has_custom_strategy = True
                break

    for prop_name in required:
        if prop_name not in result:
            prop_schema = properties.get(prop_name, {})
            result[prop_name] = b"" if prop_schema.get("format") == "binary" else ""

    return result if has_custom_strategy else None


def _filter_draws_for_case(
    raw: dict[str, Any],
    correlated: dict[tuple[ParameterLocation, str], Any],
    draws: tuple[PoolDraw, ...],
) -> tuple[PoolDraw, ...]:
    """Keep only draws whose pooled value is actually present in the yielded case.

    Coverage variants can omit an optional resource-bound slot or replace it with a mutated
    value; in either case the pool was not consumed for that slot in this specific case, so
    the draw shouldn't carry over into the analyzer's per-case stats.

    Operates on the pre-serialization `raw` view so the comparison is a plain ``==`` against
    the original pool value — no URL-quoting or stringification reversal needed.
    """
    if not draws:
        return ()
    result: list[PoolDraw] = []
    for draw in draws:
        try:
            location = ParameterLocation(draw.location)
        except ValueError:
            continue
        expected = correlated.get((location, draw.parameter_name))
        if expected is None:
            continue
        actual = _case_slot_value(raw, location, draw.parameter_name)
        if actual is _SENTINEL_ABSENT:
            continue
        if actual == expected:
            result.append(draw)
    return tuple(result)


def _filter_misses_for_case(
    raw: dict[str, Any],
    misses: tuple[tuple[str, str], ...],
) -> tuple[tuple[str, str], ...]:
    """Keep misses whose slot is present in the yielded case (synthesised value, not a pool draw).

    A miss is "engine wanted to draw, pool was empty, slot still got a synthesised value".
    Cases that omit the slot entirely (e.g. missing-parameter coverage probes) didn't actually
    attempt the fill, so they shouldn't count as misses for this case.
    """
    if not misses:
        return ()
    result: list[tuple[str, str]] = []
    for miss in misses:
        try:
            location = ParameterLocation(miss[0])
        except ValueError:
            continue
        if _case_slot_value(raw, location, miss[1]) is not _SENTINEL_ABSENT:
            result.append(miss)
    return tuple(result)


# Sentinel used by `_case_slot_value` to distinguish "absent" from "present with value None".
_SENTINEL_ABSENT = object()


def _case_slot_value(kwargs: dict[str, Any], location: ParameterLocation, parameter_name: str) -> Any:
    """Look up the value at `(location, parameter_name)` in the yielded case kwargs.

    Returns ``_SENTINEL_ABSENT`` when the slot is not present at all. For nested body fields
    like ``"shipping/location_id"``, walks the path one segment at a time.
    """
    container = kwargs.get(location.container_name)
    if container is None:
        return _SENTINEL_ABSENT
    if location == ParameterLocation.BODY:
        if not isinstance(container, dict):
            return _SENTINEL_ABSENT
        cursor: Any = container
        for segment in parameter_name.split("/"):
            if not isinstance(cursor, dict) or segment not in cursor:
                return _SENTINEL_ABSENT
            cursor = cursor[segment]
        return cursor
    if not isinstance(container, dict) or parameter_name not in container:
        return _SENTINEL_ABSENT
    return container[parameter_name]


def iter_coverage_cases(
    *,
    operation: APIOperation,
    generation_modes: list[GenerationMode],
    generate_duplicate_query_parameters: bool,
    unexpected_methods: set[str],
    generation_config: GenerationConfig,
    extra_data_source: ExtraDataSource | None = None,
    unexpected_methods_seen: set[tuple[str, str]] | None = None,
    error_feedback: ErrorFeedbackStore | None = None,
) -> Generator[Case, None, None]:
    generators: dict[tuple[ParameterLocation, str], Generator[GeneratedValue, None, None]] = {}
    serializers = operation.get_parameter_serializers()
    template = Template(serializers)

    instant = Instant()
    responses = list(operation.responses.iter_examples())
    custom_formats = operation.schema.get_custom_format_strategies(generation_config, GenerationMode.POSITIVE)

    seen_negative = HashSet()
    seen_positive = HashSet()
    capabilities = operation.schema.get_coverage_capabilities()
    validator_cls = capabilities.validator_cls
    update_pattern = capabilities.update_pattern
    assert validator_cls is not None, "Coverage phase requires a JSON schema validator class"

    correlated: dict[tuple[ParameterLocation, str], Any]
    correlated_draws: tuple[PoolDraw, ...]
    correlated_misses: tuple[tuple[str, str], ...]
    if extra_data_source is not None:
        pool_pick = extra_data_source.pick_correlated_values(operation=operation)
        correlated = pool_pick.values
        correlated_draws = pool_pick.draws
        correlated_misses = pool_pick.misses
    else:
        correlated = {}
        correlated_draws = ()
        correlated_misses = ()

    def _build_meta(
        *,
        generation: GenerationInfo,
        components: dict[ParameterLocation, ComponentInfo],
        phase: PhaseInfo,
        raw: dict[str, Any],
    ) -> CaseMetadata:
        # Preserve typed parameter containers so revalidation can validate against
        # the schema's abstraction level, not the stringified wire form on the case.
        # Body is excluded — it doesn't go through parameter stringification.
        raw_containers: dict[ParameterLocation, Any] = {
            location: value
            for name, value in raw.items()
            if (location := CONTAINER_TO_LOCATION.get(name)) is not None
            and location in components
            and location != ParameterLocation.BODY
        }
        # Filter operation-level draws/misses to only those whose slot actually appears in
        # the yielded request. Coverage variants that omit an optional resource-bound slot,
        # or synthesised probes that drop one parameter while keeping a pooled path param,
        # would otherwise over- or under-attribute the pool.
        return CaseMetadata(
            generation=generation,
            components=components,
            phase=phase,
            pool_draws=_filter_draws_for_case(raw, correlated, correlated_draws),
            pool_misses=_filter_misses_for_case(raw, correlated_misses),
            raw_containers=raw_containers,
        )

    inferred_properties_per_location: dict[ParameterLocation, dict[str, Any] | None] = {}

    def _inferred_properties(target_location: ParameterLocation) -> dict[str, Any] | None:
        if target_location in inferred_properties_per_location:
            return inferred_properties_per_location[target_location]
        # Caller guards with `error_feedback is not None`; the narrowing is invisible inside the closure.
        assert error_feedback is not None
        container = getattr(operation, target_location.container_name, None)
        result: dict[str, Any] | None = None
        if isinstance(container, OpenApiParameterSet):
            base = container.schema
            adjusted = apply_adjustments(
                operation=operation,
                location=target_location,
                schema=base,
                store=error_feedback,
            )
            # `apply_adjustments` returns the input unchanged when there are no observations;
            # only splice when something was actually inferred.
            if adjusted is not base and isinstance(adjusted, dict):
                properties = adjusted.get("properties")
                if isinstance(properties, dict):
                    result = properties
        inferred_properties_per_location[target_location] = result
        return result

    for parameter in operation.iter_parameters():
        location = parameter.location
        name = parameter.name
        schema = parameter.unoptimized_schema
        schema_is_clone = False
        if error_feedback is not None and isinstance(schema, dict):
            inferred_properties = _inferred_properties(location)
            if inferred_properties is not None:
                inferred = inferred_properties.get(name)
                if isinstance(inferred, dict):
                    schema = {**schema, **inferred}
                    schema_is_clone = True
        examples = parameter.examples
        if examples and schema_is_clone:
            try:
                parameter_validator = make_validator(schema, validator_cls)
            except Exception:
                parameter_validator = None
            if parameter_validator is not None:
                examples = [example for example in examples if parameter_validator.is_valid(example)]
        if examples:
            if not schema_is_clone:
                schema = dict(schema)
                schema_is_clone = True
            schema["examples"] = examples
        for value in find_matching_in_responses(responses, parameter.name):
            if not schema_is_clone:
                schema = dict(schema)
                schema_is_clone = True
            schema.setdefault("examples", []).append(value)
        if _is_pool_eligible(schema):
            pool_value = correlated.get((location, name))
            if pool_value is not None:
                schema = {**schema, "examples": [pool_value]}
        gen = cover_schema_iter(
            CoverageContext(
                root_schema=schema,
                location=location,
                media_type=None,
                generation_modes=generation_modes,
                is_required=parameter.is_required,
                custom_formats=custom_formats,
                validator_cls=validator_cls,
                update_pattern=update_pattern,
                allow_extra_parameters=generation_config.allow_extra_parameters,
            ),
            schema,
        )
        value = next(gen, NOT_SET)
        # Pin the template's Content-Type to the body media type when CT is declared as an explicit
        # header parameter — otherwise body cases inherit a fuzzed CT (often empty) and ship bodies
        # that downstream tools can't dispatch. CT-mutation variants still flow through the iterator.
        if location == ParameterLocation.HEADER and name.lower() == "content-type" and operation.body:
            value = GeneratedValue.with_positive(
                value=operation.body[0].media_type,
                scenario=CoverageScenario.VALID_STRING,
                description="Valid Content-Type pinned to body media type",
            )
        if isinstance(value, NotSet):
            if location == ParameterLocation.PATH:
                # Can't skip path parameters - they should be filled
                schema = dict(schema)
                schema.setdefault("type", "string")
                schema.setdefault("minLength", 1)
                gen = cover_schema_iter(
                    CoverageContext(
                        root_schema=schema,
                        location=location,
                        media_type=None,
                        generation_modes=[GenerationMode.POSITIVE],
                        is_required=parameter.is_required,
                        custom_formats=custom_formats,
                        validator_cls=validator_cls,
                        update_pattern=update_pattern,
                        allow_extra_parameters=generation_config.allow_extra_parameters,
                    ),
                    schema,
                )
                value = next(
                    gen,
                    GeneratedValue(
                        "value",
                        generation_mode=GenerationMode.NEGATIVE,
                        scenario=CoverageScenario.UNSUPPORTED_PATH_PATTERN,
                        description="Sample value for unsupported path parameter pattern",
                        parameter=name,
                        location="/",
                    ),
                )
                template.add_parameter(location, name, value)
                continue
            continue
        template.add_parameter(location, name, value)
        generators[(location, name)] = gen
    template_time = instant.elapsed
    has_required_body = operation.body and any(b.is_required for b in operation.body)
    has_generated_required_body = False
    # Set when the body template substrate had to fall back to a negative value because positive
    # coverage yielded nothing (e.g. readOnly + allOf composition makes every template option
    # unsatisfiable, or every `oneOf` branch overlaps). When set, parameter-mutation cases must
    # skip NEGATIVE param values — those would mix two negatives in one case (the existing body
    # plus the param mutation). POSITIVE param values still flow through: the case is overall
    # negative because of the body, but the parameter's positive value still reaches the wire,
    # which is what coverage tracking needs.
    template_body_is_fallback_negative = False
    if operation.body:
        for body in operation.body:
            instant = Instant()

            multipart_body = _generate_multipart_body_from_custom_strategies(body)
            if multipart_body is not None:
                if body.is_required:
                    has_generated_required_body = True
                if "body" not in template:
                    template.set_body(
                        GeneratedValue.with_positive(
                            value=multipart_body,
                            scenario=CoverageScenario.EXAMPLE_VALUE,
                            description="Multipart body with custom encoding",
                        ),
                        body.media_type,
                    )
                continue

            custom_gen = _generate_coverage_values_from_custom_strategy(body.media_type)
            first_custom_value = next(custom_gen, None)

            if first_custom_value is not None:
                if body.is_required:
                    has_generated_required_body = True
                elapsed = instant.elapsed
                if "body" not in template:
                    template_time += elapsed
                    template.set_body(first_custom_value, body.media_type)
                data = template.with_body(value=first_custom_value, media_type=body.media_type)
                yield operation.Case(
                    **data.kwargs,
                    _meta=_build_meta(
                        generation=GenerationInfo(time=elapsed, mode=first_custom_value.generation_mode),
                        components=data.components,
                        phase=PhaseInfo.coverage(
                            scenario=first_custom_value.scenario,
                            description=first_custom_value.description,
                            location=first_custom_value.location,
                            parameter=body.media_type,
                            parameter_location=ParameterLocation.BODY,
                        ),
                        raw=data.raw,
                    ),
                )
                continue

            schema = body.unoptimized_schema
            schema_is_clone = False
            if error_feedback is not None:
                adjusted = apply_adjustments(
                    operation=operation,
                    location=ParameterLocation.BODY,
                    schema=schema,
                    store=error_feedback,
                )
                if adjusted is not schema:
                    schema = adjusted
                    schema_is_clone = True
            examples = body.examples
            if examples and schema_is_clone:
                # Drop examples invalidated by inferred constraints so coverage falls back to schema generation.
                try:
                    body_validator = make_validator(schema, validator_cls)
                except Exception:
                    body_validator = None
                if body_validator is not None:
                    examples = [example for example in examples if body_validator.is_valid(example)]
            if examples:
                if not schema_is_clone:
                    schema = dict(schema)
                # User-registered media types should only handle text / binary data
                if body.media_type in MEDIA_TYPE_STRATEGIES:
                    schema["examples"] = [example for example in examples if isinstance(example, str | bytes)]
                else:
                    schema["examples"] = examples
            body_overlays = _body_pool_overlays(correlated=correlated, body_schema=schema, validator_cls=validator_cls)
            if body_overlays:
                schema = dict(schema)
                schema_properties = dict(schema["properties"])
                for prop_name, value in body_overlays.items():
                    prop_schema = schema_properties[prop_name]
                    assert isinstance(prop_schema, dict), "_body_pool_overlays only emits dict-schema keys"
                    if isinstance(value, _NestedOverlay):
                        # Splice per leaf so the coverage generator still fills sibling fields.
                        sub_props = dict(prop_schema.get("properties") or {})
                        for sub_name, sub_value in value.fields.items():
                            sub_schema = sub_props[sub_name]
                            assert isinstance(sub_schema, dict), "_nested_body_pool_overlay only emits dict-schema keys"
                            sub_props[sub_name] = {**sub_schema, "examples": [sub_value]}
                        schema_properties[prop_name] = {**prop_schema, "properties": sub_props}
                    else:
                        schema_properties[prop_name] = {**prop_schema, "examples": [value]}
                schema["properties"] = schema_properties
            try:
                media_type = media_types.parse(body.media_type)
            except MalformedMediaType as exc:
                raise InvalidSchema.from_malformed_media_type(
                    exc, body.media_type, path=operation.path, method=operation.method
                ) from exc
            gen = cover_schema_iter(
                CoverageContext(
                    root_schema=schema,
                    location=ParameterLocation.BODY,
                    media_type=media_type,
                    generation_modes=generation_modes,
                    is_required=body.is_required,
                    custom_formats=custom_formats,
                    validator_cls=validator_cls,
                    update_pattern=update_pattern,
                    allow_extra_parameters=generation_config.allow_extra_parameters,
                ),
                schema,
            )
            value = next(gen, NOT_SET)
            if isinstance(value, NotSet) or (
                body.media_type in MEDIA_TYPE_STRATEGIES and not isinstance(value.value, str | bytes)
            ):
                continue
            if body.is_required:
                has_generated_required_body = True
            elapsed = instant.elapsed
            if "body" not in template:
                template_time += elapsed
                if value.generation_mode == GenerationMode.POSITIVE:
                    template.set_body(value, body.media_type)
                else:
                    # The template must be a valid positive baseline so that
                    # parameter-mutation cases (e.g. missing required header) only
                    # invalidate the one thing being tested.  If the first body value is
                    # a negative mutation (NEGATIVE-only mode), generate a positive value
                    # separately and prefer it for the template.
                    pos_gen = cover_schema_iter(
                        CoverageContext(
                            root_schema=schema,
                            location=ParameterLocation.BODY,
                            media_type=media_type,
                            generation_modes=[GenerationMode.POSITIVE],
                            is_required=body.is_required,
                            custom_formats=custom_formats,
                            validator_cls=validator_cls,
                            update_pattern=update_pattern,
                            allow_extra_parameters=generation_config.allow_extra_parameters,
                        ),
                        schema,
                    )
                    first_positive = next(pos_gen, NOT_SET)
                    if isinstance(first_positive, NotSet):
                        template_body_is_fallback_negative = True
                        template.set_body(value, body.media_type)
                    else:
                        template.set_body(first_positive, body.media_type)
            data = template.with_body(value=value, media_type=body.media_type)
            yield operation.Case(
                **data.kwargs,
                _meta=_build_meta(
                    generation=GenerationInfo(
                        time=elapsed,
                        mode=value.generation_mode,
                    ),
                    components=data.components,
                    phase=PhaseInfo.coverage(
                        scenario=value.scenario,
                        description=value.description,
                        location=value.location,
                        parameter=body.media_type,
                        parameter_location=ParameterLocation.BODY,
                    ),
                    raw=data.raw,
                ),
            )
            iterator = iter(gen)
            while True:
                instant = Instant()
                try:
                    next_value = next(iterator)
                    if body.media_type in MEDIA_TYPE_STRATEGIES and not isinstance(next_value.value, str | bytes):
                        continue

                    data = template.with_body(value=next_value, media_type=body.media_type)
                    yield operation.Case(
                        **data.kwargs,
                        _meta=_build_meta(
                            generation=GenerationInfo(
                                time=instant.elapsed,
                                mode=next_value.generation_mode,
                            ),
                            components=data.components,
                            phase=PhaseInfo.coverage(
                                scenario=next_value.scenario,
                                description=next_value.description,
                                location=next_value.location,
                                parameter=body.media_type,
                                parameter_location=ParameterLocation.BODY,
                            ),
                            raw=data.raw,
                        ),
                    )
                except StopIteration:
                    break
    elif GenerationMode.POSITIVE in generation_modes and (not has_required_body or has_generated_required_body):
        data = template.unmodified()
        seen_positive.insert(data.kwargs)
        yield operation.Case(
            **data.kwargs,
            _meta=_build_meta(
                generation=GenerationInfo(
                    time=template_time,
                    mode=GenerationMode.POSITIVE,
                ),
                components=data.components,
                phase=PhaseInfo.coverage(
                    scenario=CoverageScenario.DEFAULT_POSITIVE_TEST, description="Default positive test case"
                ),
                raw=data.raw,
            ),
        )

    for (location, name), gen in generators.items():
        iterator = iter(gen)
        # CT-mutation cases test Content-Type validation, not body validation; carrying the
        # template's body would conflate the two sweeps (matches the missing-CT special-case below).
        is_content_type_mutation = location == ParameterLocation.HEADER and name.lower() == "content-type"
        while True:
            instant = Instant()
            try:
                value = next(iterator)
                data = template.with_parameter(location=location, name=name, value=value)
            except StopIteration:
                break

            kwargs = data.kwargs
            raw = data.raw
            if is_content_type_mutation:
                kwargs = {k: v for k, v in kwargs.items() if k not in ("body", "media_type")}
                raw = {k: v for k, v in raw.items() if k not in ("body", "media_type")}

            if value.generation_mode == GenerationMode.NEGATIVE:
                if template_body_is_fallback_negative:
                    # Skip: would emit a case with NEGATIVE body + NEGATIVE param.
                    continue
                seen_negative.insert(kwargs)
            elif value.generation_mode == GenerationMode.POSITIVE:
                if has_required_body and not has_generated_required_body and not is_content_type_mutation:
                    continue
                if not seen_positive.insert(kwargs):
                    continue

            yield operation.Case(
                **kwargs,
                _meta=_build_meta(
                    generation=GenerationInfo(time=instant.elapsed, mode=value.generation_mode),
                    components=data.components,
                    phase=PhaseInfo.coverage(
                        scenario=value.scenario,
                        description=value.description,
                        location=value.location,
                        parameter=name,
                        parameter_location=location,
                    ),
                    raw=raw,
                ),
            )
    if template_body_is_fallback_negative:
        # The remaining blocks emit NEGATIVE param-mutation cases (missing/duplicate/etc.)
        # built off the template body. Combined with a fallback-negative body they would
        # mix two negatives in one case.
        return
    if GenerationMode.NEGATIVE in generation_modes:
        # Path-level: each `(path, method)` pair runs once across declared operations.
        methods = sorted(unexpected_methods - set(operation.schema[operation.path]))
        for method in methods:
            if unexpected_methods_seen is not None:
                key = (operation.path, method)
                if key in unexpected_methods_seen:
                    continue
                unexpected_methods_seen.add(key)
            instant = Instant()
            data = template.unmodified()
            yield operation.Case(
                **data.kwargs,
                method=method.upper(),
                _meta=_build_meta(
                    generation=GenerationInfo(time=instant.elapsed, mode=GenerationMode.NEGATIVE),
                    components=data.components,
                    phase=PhaseInfo.coverage(
                        scenario=CoverageScenario.UNSPECIFIED_HTTP_METHOD,
                        description=f"Unspecified HTTP method: {method.upper()}",
                    ),
                    raw=data.raw,
                ),
            )
        # Generate duplicate query parameters
        # NOTE: if the query schema has no constraints, then we may have no negative test cases at all
        # as they all will match the original schema and therefore will be considered as positive ones
        if generate_duplicate_query_parameters and operation.query and "query" in template:
            container = template["query"]
            for parameter in operation.query:
                if parameter.definition.get("in") == "querystring":
                    # Duplicate parameter semantics don't apply to querystring parameters;
                    # they use content-based serialization, not individual key-value pairs.
                    continue
                instant = Instant()
                # Could be absent if value schema can't be negated
                # I.e. contains just `default` value without any other keywords
                value = container.get(parameter.name, NOT_SET)
                if value is not NOT_SET:
                    data = template.with_location(
                        location=ParameterLocation.QUERY,
                        value={**container, parameter.name: [value, value]},
                        generation_mode=GenerationMode.NEGATIVE,
                    )
                    yield operation.Case(
                        **data.kwargs,
                        _meta=_build_meta(
                            generation=GenerationInfo(time=instant.elapsed, mode=GenerationMode.NEGATIVE),
                            components=data.components,
                            phase=PhaseInfo.coverage(
                                scenario=CoverageScenario.DUPLICATE_PARAMETER,
                                description=f"Duplicate `{parameter.name}` query parameter",
                                parameter=parameter.name,
                                parameter_location=ParameterLocation.QUERY,
                            ),
                            raw=data.raw,
                        ),
                    )
        # Generate missing required parameters
        for parameter in operation.iter_parameters():
            if parameter.is_required and parameter.location != ParameterLocation.PATH:
                instant = Instant()
                name = parameter.name
                location = parameter.location
                container = template.get(location.container_name, {})
                data = template.with_location(
                    location=location,
                    value={k: v for k, v in container.items() if k != name},
                    generation_mode=GenerationMode.NEGATIVE,
                )
                kwargs = data.kwargs
                raw = data.raw
                # For missing Content-Type header test, don't send body
                if location == ParameterLocation.HEADER and name.lower() == "content-type":
                    kwargs = {k: v for k, v in kwargs.items() if k not in ("body", "media_type")}
                    raw = {k: v for k, v in raw.items() if k not in ("body", "media_type")}

                if seen_negative.insert(kwargs):
                    yield operation.Case(
                        **kwargs,
                        _meta=_build_meta(
                            generation=GenerationInfo(time=instant.elapsed, mode=GenerationMode.NEGATIVE),
                            components=data.components,
                            phase=PhaseInfo.coverage(
                                scenario=CoverageScenario.MISSING_PARAMETER,
                                description=f"Missing `{name}` at {location.value}",
                                parameter=name,
                                parameter_location=location,
                            ),
                            raw=raw,
                        ),
                    )
    # Generate combinations for each location
    for location, parameter_set in [
        (ParameterLocation.QUERY, operation.query),
        (ParameterLocation.HEADER, operation.headers),
        (ParameterLocation.COOKIE, operation.cookies),
    ]:
        if not parameter_set:
            continue

        container_name = location.container_name
        base_container = template.get(container_name, {})

        # Get required and optional parameters
        required = {p.name for p in parameter_set if p.is_required}
        all_params = {p.name for p in parameter_set}
        optional = sorted(all_params - required)

        # Helper function to create and yield a case
        def make_case(
            container_values: dict,
            scenario: CoverageScenario,
            description: str,
            _location: ParameterLocation,
            _parameter: str | None,
            _generation_mode: GenerationMode,
            _instant: Instant,
        ) -> Case:
            data = template.with_location(location=_location, value=container_values, generation_mode=_generation_mode)
            return operation.Case(
                **data.kwargs,
                _meta=_build_meta(
                    generation=GenerationInfo(
                        time=_instant.elapsed,
                        mode=_generation_mode,
                    ),
                    components=data.components,
                    phase=PhaseInfo.coverage(
                        scenario=scenario,
                        description=description,
                        parameter=_parameter,
                        parameter_location=_location,
                    ),
                    raw=data.raw,
                ),
            )

        def _combination_schema(
            combination: dict[str, Any], _required: set[str], _parameter_set: ParameterSet
        ) -> dict[str, Any]:
            return {
                "properties": {
                    parameter.name: parameter.optimized_schema
                    for parameter in _parameter_set
                    if parameter.name in combination
                },
                "required": list(_required),
                "additionalProperties": False,
            }

        def _yield_negative(
            subschema: dict[str, Any], _location: ParameterLocation, is_required: bool
        ) -> Generator[Case, None, None]:
            iterator = iter(
                cover_schema_iter(
                    CoverageContext(
                        root_schema=subschema,
                        location=_location,
                        media_type=None,
                        generation_modes=[GenerationMode.NEGATIVE],
                        is_required=is_required,
                        custom_formats=custom_formats,
                        validator_cls=validator_cls,
                        update_pattern=update_pattern,
                        allow_extra_parameters=generation_config.allow_extra_parameters,
                    ),
                    subschema,
                )
            )
            while True:
                instant = Instant()
                try:
                    more = next(iterator)
                    yield make_case(
                        more.value,
                        more.scenario,
                        more.description,
                        _location,
                        more.parameter,
                        GenerationMode.NEGATIVE,
                        instant,
                    )
                except StopIteration:
                    break

        # 1. Generate only required properties
        if required and all_params != required:
            only_required = {k: v for k, v in base_container.items() if k in required}
            if GenerationMode.POSITIVE in generation_modes and not (
                has_required_body and not has_generated_required_body
            ):
                yield make_case(
                    only_required,
                    CoverageScenario.OBJECT_ONLY_REQUIRED,
                    "Only required properties",
                    location,
                    None,
                    GenerationMode.POSITIVE,
                    Instant(),
                )
            if GenerationMode.NEGATIVE in generation_modes:
                subschema = _combination_schema(only_required, required, parameter_set)
                for case in _yield_negative(subschema, location, is_required=bool(required)):
                    kwargs = _case_to_kwargs(case)
                    if not seen_negative.insert(kwargs):
                        continue
                    assert case.meta is not None
                    assert isinstance(case.meta.phase.data, CoveragePhaseData)
                    # Already generated in one of the blocks above
                    if (
                        location != "path"
                        and case.meta.phase.data.scenario != CoverageScenario.OBJECT_MISSING_REQUIRED_PROPERTY
                    ):
                        yield case

        # 2. Generate combinations with required properties and one optional property
        for opt_param in optional:
            combo = {k: v for k, v in base_container.items() if k in required or k == opt_param}
            if combo != base_container and GenerationMode.POSITIVE in generation_modes:
                if not (has_required_body and not has_generated_required_body):
                    yield make_case(
                        combo,
                        CoverageScenario.OBJECT_REQUIRED_AND_OPTIONAL,
                        f"All required properties and optional '{opt_param}'",
                        location,
                        None,
                        GenerationMode.POSITIVE,
                        Instant(),
                    )
                if GenerationMode.NEGATIVE in generation_modes:
                    subschema = _combination_schema(combo, required, parameter_set)
                    for case in _yield_negative(subschema, location, is_required=bool(required)):
                        assert case.meta is not None
                        assert isinstance(case.meta.phase.data, CoveragePhaseData)
                        # Already generated in one of the blocks above
                        if (
                            location != "path"
                            and case.meta.phase.data.scenario != CoverageScenario.OBJECT_MISSING_REQUIRED_PROPERTY
                        ):
                            yield case

        # 3. Generate one combination for each size from 2 to N-1 of optional parameters
        if (
            len(optional) > 1
            and GenerationMode.POSITIVE in generation_modes
            and not (has_required_body and not has_generated_required_body)
        ):
            for size in range(2, len(optional)):
                for combination in combinations(optional, size):
                    combo = {k: v for k, v in base_container.items() if k in required or k in combination}
                    if combo != base_container:
                        yield make_case(
                            combo,
                            CoverageScenario.OBJECT_REQUIRED_AND_OPTIONAL,
                            f"All required and {size} optional properties",
                            location,
                            None,
                            GenerationMode.POSITIVE,
                            Instant(),
                        )
                        break
