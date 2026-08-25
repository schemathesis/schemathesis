"""Operation-level coverage.

Enumerate cases for an OpenAPI operation by combining parameter, header, body, and
response coverage into concrete `Case` instances.
"""

from __future__ import annotations

from collections.abc import Callable, Generator
from dataclasses import dataclass
from enum import Enum, auto
from itertools import combinations
from typing import TYPE_CHECKING, Any, TypeGuard, cast

from schemathesis.core import NOT_SET, NotSet, media_types
from schemathesis.core.errors import InvalidSchema, MalformedMediaType
from schemathesis.core.jsonschema import BUNDLE_STORAGE_KEY, make_validator
from schemathesis.core.media_types import FORM_MEDIA_TYPES, find_media_type_strategy
from schemathesis.core.parameters import CONTAINER_TO_LOCATION, ParameterLocation
from schemathesis.core.timing import Instant
from schemathesis.core.transforms import deepclone
from schemathesis.generation import GenerationMode
from schemathesis.generation.case import Case
from schemathesis.generation.coverage import GenerationSession
from schemathesis.generation.hypothesis import examples
from schemathesis.generation.hypothesis._response_matching import find_matching_in_responses
from schemathesis.generation.hypothesis.builder import _case_to_kwargs
from schemathesis.generation.meta import (
    CaseMetadata,
    ComponentInfo,
    CoverageScenario,
    GenerationInfo,
    PhaseInfo,
)
from schemathesis.specs.openapi.adapter.parameters import OpenApiParameterSet
from schemathesis.specs.openapi.coverage._schema import CoverageContext, GeneratedValue, HashSet, cover_schema_iter
from schemathesis.specs.openapi.error_feedback import apply_adjustments
from schemathesis.transport.serialization import quote_all

if TYPE_CHECKING:
    import jsonschema_rs
    from hypothesis.strategies import SearchStrategy

    from schemathesis.config import GenerationConfig
    from schemathesis.core.error_feedback import ErrorFeedbackStore
    from schemathesis.core.parameters import ContainerName
    from schemathesis.core.transport import HttpMethod
    from schemathesis.resources import PoolDraw, ResourcePool
    from schemathesis.schemas import APIOperation, ParameterSet
    from schemathesis.specs.openapi.adapter.parameters import OpenApiBody


class Template:
    __slots__ = (
        "_components",
        "_serializers",
        "_template",
        "body_is_fallback_negative",
        "has_generated_required_body",
        "has_required_body",
        "seed_time",
        "unsatisfiable_required_parameter",
    )

    def __init__(self, serializers: dict[str, Callable]) -> None:
        self._components: dict[ParameterLocation, ComponentInfo] = {}
        self._template: dict[str, Any] = {}
        self._serializers = serializers
        # A required body that never produced a value, or a required parameter without a positive
        # value, leaves no valid positive request; a fallback-negative body forbids stacking a
        # second negative on top.
        self.has_required_body = False
        self.has_generated_required_body = False
        self.body_is_fallback_negative = False
        self.unsatisfiable_required_parameter = False
        self.seed_time = 0.0

    def can_emit(self, mode: GenerationMode) -> bool:
        if mode == GenerationMode.POSITIVE:
            return not (
                (self.has_required_body and not self.has_generated_required_body)
                or self.unsatisfiable_required_parameter
            )
        return not self.body_is_fallback_negative

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


class Dedup(Enum):
    # Keyed by the request either mode already sent.
    REQUEST = auto()
    # Keyed only by requests the negative stream sent; positives never block it.
    NEGATIVE_SET = auto()
    # Same disciplines, but keyed by the built case's wire form, where header spellings
    # differing only in casing collapse into one header.
    WIRE_REQUEST = auto()
    WIRE_NEGATIVE_SET = auto()


@dataclass(slots=True)
class CaseEmitter:
    """Builds and deduplicates the cases one operation's coverage run emits."""

    operation: APIOperation
    correlated: dict[tuple[ParameterLocation, str], Any]
    correlated_draws: tuple[PoolDraw, ...]
    correlated_misses: tuple[tuple[str, str], ...]
    seen_positive: HashSet
    seen_negative: HashSet

    def is_new_request(self, kwargs: dict[str, Any], mode: GenerationMode) -> bool:
        # Repeating a request already sent tests nothing, whichever mode sent it first.
        key = _dedup_key(kwargs)
        if mode == GenerationMode.POSITIVE:
            return self.seen_positive.insert(key)
        return key not in self.seen_positive and self.seen_negative.insert(key)

    def _meta(
        self,
        *,
        generation: GenerationInfo,
        components: dict[ParameterLocation, ComponentInfo],
        phase: PhaseInfo,
        raw: dict[str, Any],
    ) -> CaseMetadata:
        # Typed parameter containers survive so revalidation judges the schema level, not the wire form.
        raw_containers: dict[ParameterLocation, Any] = {
            location: value
            for name, value in raw.items()
            if (location := CONTAINER_TO_LOCATION.get(cast("ContainerName", name))) is not None
            and location in components
            and location != ParameterLocation.BODY
        }
        # Draws/misses narrowed to the slots this request actually carries, so the pool is not misattributed.
        return CaseMetadata(
            generation=generation,
            components=components,
            phase=phase,
            pool_draws=_filter_draws_for_case(raw, self.correlated, self.correlated_draws),
            pool_misses=_filter_misses_for_case(raw, self.correlated_misses),
            raw_containers=raw_containers,
        )

    def build(
        self,
        data: TemplateValue,
        *,
        mode: GenerationMode,
        elapsed: float,
        scenario: CoverageScenario,
        description: str,
        location: str | None = None,
        parameter: str | None = None,
        parameter_location: ParameterLocation | None = None,
        method: HttpMethod | None = None,
        kwargs: dict[str, Any] | None = None,
        raw: dict[str, Any] | None = None,
    ) -> Case:
        kwargs = data.kwargs if kwargs is None else kwargs
        raw = data.raw if raw is None else raw
        extra: dict[str, Any] = {"method": method} if method is not None else {}
        return self.operation.Case(
            **kwargs,
            **extra,
            _meta=self._meta(
                generation=GenerationInfo(time=elapsed, mode=mode),
                components=data.components,
                phase=PhaseInfo.coverage(
                    scenario=scenario,
                    description=description,
                    location=location,
                    parameter=parameter,
                    parameter_location=parameter_location,
                ),
                raw=raw,
            ),
        )

    def emit(
        self,
        data: TemplateValue,
        *,
        mode: GenerationMode,
        elapsed: float,
        scenario: CoverageScenario,
        description: str,
        location: str | None = None,
        parameter: str | None = None,
        parameter_location: ParameterLocation | None = None,
        method: HttpMethod | None = None,
        kwargs: dict[str, Any] | None = None,
        raw: dict[str, Any] | None = None,
        dedup: Dedup = Dedup.REQUEST,
    ) -> Case | None:
        checked_kwargs = data.kwargs if kwargs is None else kwargs
        if dedup is Dedup.REQUEST and not self.is_new_request(checked_kwargs, mode):
            return None
        if dedup is Dedup.NEGATIVE_SET and not self.seen_negative.insert(_dedup_key(checked_kwargs)):
            return None
        case = self.build(
            data,
            mode=mode,
            elapsed=elapsed,
            scenario=scenario,
            description=description,
            location=location,
            parameter=parameter,
            parameter_location=parameter_location,
            method=method,
            kwargs=kwargs,
            raw=raw,
        )
        if dedup is Dedup.WIRE_REQUEST and not self.is_new_request(_case_to_kwargs(case), mode):
            return None
        if dedup is Dedup.WIRE_NEGATIVE_SET and not self.seen_negative.insert(_dedup_key(_case_to_kwargs(case))):
            return None
        return case


def _replay(
    first: GeneratedValue, rest: Generator[GeneratedValue, None, None] | None = None
) -> Generator[GeneratedValue, None, None]:
    """Hand a value back to the front of the generator it came off."""
    yield first
    if rest is not None:
        yield from rest


def _dedup_key(kwargs: dict[str, Any]) -> dict[str, Any]:
    """Key cases by the request they send, not by the value that produced it."""
    query = kwargs.get("query")
    if not isinstance(query, dict):
        return kwargs
    normalized = {}
    for key, value in query.items():
        if type(value) is list:
            # An empty list sends nothing; a one-item list sends what the bare value would.
            if not value:
                continue
            if len(value) == 1:
                value = value[0]
        normalized[key] = value
    return {**kwargs, "query": normalized}


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
            # Having a list here ensures there will be multiple query parameters with the same name
            return [_stringify_value(item, container_name) for item in val]
        # use comma-separated values style for arrays
        return ",".join(str(_stringify_value(sub, container_name)) for sub in val)
    if isinstance(val, dict):
        # Headers/cookies/query are typically all-string dicts; skip the per-value recursion.
        if all(type(v) is str for v in val.values()):
            return dict(val)
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


@dataclass(slots=True)
class CoverageRun:
    """Everything the coverage stages share for one operation."""

    operation: APIOperation
    template: Template
    emitter: CaseEmitter
    generators: dict[tuple[ParameterLocation, str], Generator[GeneratedValue, None, None]]
    generation_modes: list[GenerationMode]
    generation_config: GenerationConfig
    custom_formats: dict[str, SearchStrategy]
    validator_cls: type[jsonschema_rs.Validator]
    update_pattern: Callable[[str, int | None, int | None], str] | None
    session: GenerationSession | None
    error_feedback: ErrorFeedbackStore | None
    responses: list[tuple[str, object]]
    correlated: dict[tuple[ParameterLocation, str], Any]


def _seed_parameters(run: CoverageRun) -> None:
    operation = run.operation
    template = run.template
    generators = run.generators
    generation_modes = run.generation_modes
    generation_config = run.generation_config
    custom_formats = run.custom_formats
    validator_cls = run.validator_cls
    update_pattern = run.update_pattern
    session = run.session
    error_feedback = run.error_feedback
    responses = run.responses
    correlated = run.correlated
    instant = Instant()
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
                session=session,
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
                        session=session,
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
                # A negative fallback means the required path parameter has no representable positive value.
                if value.generation_mode == GenerationMode.NEGATIVE:
                    template.unsatisfiable_required_parameter = True
                template.add_parameter(location, name, value)
                continue
            if parameter.is_required:
                template.unsatisfiable_required_parameter = True
            continue
        # Positive values precede negative ones, so a negative seed means the required parameter has no
        # positive value; the positive case built from this template would be invalid.
        if parameter.is_required and value.generation_mode == GenerationMode.NEGATIVE:
            template.unsatisfiable_required_parameter = True
        template.add_parameter(location, name, value)
        if value.generation_mode == GenerationMode.NEGATIVE:
            # The seeded value only ever ships under some other method, so a parameter left with
            # nothing else would go untested under its own.
            following = next(gen, None)
            gen = _replay(value) if following is None else _replay(following, gen)
        generators[(location, name)] = gen
    template.seed_time = instant.elapsed
    template.has_required_body = bool(operation.body and any(b.is_required for b in operation.body))


def _body_cases(run: CoverageRun) -> Generator[Case, None, None]:
    operation = run.operation
    template = run.template
    emitter = run.emitter
    generation_modes = run.generation_modes
    generation_config = run.generation_config
    custom_formats = run.custom_formats
    validator_cls = run.validator_cls
    update_pattern = run.update_pattern
    session = run.session
    error_feedback = run.error_feedback
    correlated = run.correlated
    for body in operation.body:
        instant = Instant()

        multipart_body = _generate_multipart_body_from_custom_strategies(body)
        if multipart_body is not None:
            if body.is_required:
                template.has_generated_required_body = True
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
                template.has_generated_required_body = True
            elapsed = instant.elapsed
            if "body" not in template:
                template.seed_time += elapsed
                template.set_body(first_custom_value, body.media_type)
            data = template.with_body(value=first_custom_value, media_type=body.media_type)
            yield emitter.build(
                data,
                mode=first_custom_value.generation_mode,
                elapsed=elapsed,
                scenario=first_custom_value.scenario,
                description=first_custom_value.description,
                location=first_custom_value.location,
                parameter=body.media_type,
                parameter_location=ParameterLocation.BODY,
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
                session=session,
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
        if isinstance(value, NotSet):
            continue
        if body.is_required:
            template.has_generated_required_body = True
        elapsed = instant.elapsed
        if "body" not in template:
            template.seed_time += elapsed
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
                        session=session,
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
                    template.body_is_fallback_negative = True
                    template.set_body(value, body.media_type)
                else:
                    template.set_body(first_positive, body.media_type)
        data = template.with_body(value=value, media_type=body.media_type)
        case = emitter.emit(
            data,
            mode=value.generation_mode,
            elapsed=elapsed,
            scenario=value.scenario,
            description=value.description,
            location=value.location,
            parameter=body.media_type,
            parameter_location=ParameterLocation.BODY,
        )
        if case is None:
            continue
        yield case
        iterator = iter(gen)
        while True:
            instant = Instant()
            try:
                next_value = next(iterator)
                data = template.with_body(value=next_value, media_type=body.media_type)
                case = emitter.emit(
                    data,
                    mode=next_value.generation_mode,
                    elapsed=instant.elapsed,
                    scenario=next_value.scenario,
                    description=next_value.description,
                    location=next_value.location,
                    parameter=body.media_type,
                    parameter_location=ParameterLocation.BODY,
                )
                if case is not None:
                    yield case
            except StopIteration:
                break


def _default_positive(run: CoverageRun) -> Generator[Case, None, None]:
    template = run.template
    emitter = run.emitter
    if GenerationMode.POSITIVE not in run.generation_modes or not template.can_emit(GenerationMode.POSITIVE):
        return
    data = template.unmodified()
    case = emitter.emit(
        data,
        mode=GenerationMode.POSITIVE,
        elapsed=template.seed_time,
        scenario=CoverageScenario.DEFAULT_POSITIVE_TEST,
        description="Default positive test case",
    )
    if case is not None:
        yield case


def _parameter_mutations(run: CoverageRun) -> Generator[Case, None, None]:
    template = run.template
    emitter = run.emitter
    generators = run.generators
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
                if not template.can_emit(GenerationMode.NEGATIVE):
                    # Skip: would emit a case with NEGATIVE body + NEGATIVE param.
                    continue
            elif value.generation_mode == GenerationMode.POSITIVE:
                if (
                    template.has_required_body
                    and not template.has_generated_required_body
                    and not is_content_type_mutation
                ):
                    continue
                if template.unsatisfiable_required_parameter:
                    # A required parameter has no positive value, so no positive case is valid.
                    continue

            case = emitter.emit(
                data,
                mode=value.generation_mode,
                elapsed=instant.elapsed,
                scenario=value.scenario,
                description=value.description,
                location=value.location,
                parameter=name,
                parameter_location=location,
                kwargs=kwargs,
                raw=raw,
            )
            if case is not None:
                yield case


def _unexpected_methods(
    run: CoverageRun, unexpected_methods: set[str], unexpected_methods_seen: set[tuple[str, str]] | None
) -> Generator[Case, None, None]:
    operation = run.operation
    template = run.template
    emitter = run.emitter
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
        yield emitter.build(
            data,
            mode=GenerationMode.NEGATIVE,
            elapsed=instant.elapsed,
            scenario=CoverageScenario.UNSPECIFIED_HTTP_METHOD,
            description=f"Unspecified HTTP method: {method.upper()}",
            method=cast("HttpMethod", method.upper()),
        )


def _duplicate_query(run: CoverageRun, generate_duplicate_query_parameters: bool) -> Generator[Case, None, None]:
    operation = run.operation
    template = run.template
    emitter = run.emitter
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
                yield emitter.build(
                    data,
                    mode=GenerationMode.NEGATIVE,
                    elapsed=instant.elapsed,
                    scenario=CoverageScenario.DUPLICATE_PARAMETER,
                    description=f"Duplicate `{parameter.name}` query parameter",
                    parameter=parameter.name,
                    parameter_location=ParameterLocation.QUERY,
                )


def _missing_required(run: CoverageRun) -> Generator[Case, None, None]:
    operation = run.operation
    template = run.template
    emitter = run.emitter
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

            case = emitter.emit(
                data,
                mode=GenerationMode.NEGATIVE,
                elapsed=instant.elapsed,
                scenario=CoverageScenario.MISSING_PARAMETER,
                description=f"Missing `{name}` at {location.value}",
                parameter=name,
                parameter_location=location,
                kwargs=kwargs,
                raw=raw,
                dedup=Dedup.NEGATIVE_SET,
            )
            if case is not None:
                yield case


def _container_combinations(run: CoverageRun) -> Generator[Case, None, None]:
    operation = run.operation
    template = run.template
    emitter = run.emitter
    generation_modes = run.generation_modes
    generation_config = run.generation_config
    custom_formats = run.custom_formats
    validator_cls = run.validator_cls
    update_pattern = run.update_pattern
    session = run.session
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
            _dedup: Dedup = Dedup.WIRE_REQUEST,
        ) -> Case | None:
            data = template.with_location(location=_location, value=container_values, generation_mode=_generation_mode)
            return emitter.emit(
                data,
                mode=_generation_mode,
                elapsed=_instant.elapsed,
                scenario=scenario,
                description=description,
                parameter=_parameter,
                parameter_location=_location,
                dedup=_dedup,
            )

        def _combination_schema(
            combination: dict[str, Any], _required: set[str], _parameter_set: ParameterSet
        ) -> dict[str, Any]:
            properties = {
                parameter.name: parameter.optimized_schema
                for parameter in _parameter_set
                if parameter.name in combination
            }
            # A required parameter the template could not seed is absent from the combination; requiring it
            # anyway would describe a container nothing satisfies.
            schema: dict[str, Any] = {
                "properties": properties,
                "required": [name for name in _required if name in properties],
                "additionalProperties": False,
            }
            # Each parameter keeps its bundled definitions next to itself, but their references point at
            # the document root - which is this schema, not the parameter it came from.
            bundle: dict[str, Any] = {}
            for property_schema in properties.values():
                if isinstance(property_schema, dict):
                    bundle.update(property_schema.get(BUNDLE_STORAGE_KEY) or {})
            if bundle:
                schema[BUNDLE_STORAGE_KEY] = bundle
            return schema

        def _yield_negative(
            subschema: dict[str, Any], _location: ParameterLocation, is_required: bool, _dedup: Dedup
        ) -> Generator[Case, None, None]:
            iterator = iter(
                cover_schema_iter(
                    CoverageContext(
                        session=session,
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
                except StopIteration:
                    break
                case = make_case(
                    more.value,
                    more.scenario,
                    more.description,
                    _location,
                    more.parameter,
                    GenerationMode.NEGATIVE,
                    instant,
                    _dedup,
                )
                # Deduplicate before filtering so even a filtered case blocks a later wire duplicate.
                if case is None or more.scenario == CoverageScenario.OBJECT_MISSING_REQUIRED_PROPERTY:
                    continue
                yield case

        # 1. Generate only required properties
        if required and all_params != required:
            only_required = {k: v for k, v in base_container.items() if k in required}
            if GenerationMode.POSITIVE in generation_modes and not (
                template.has_required_body and not template.has_generated_required_body
            ):
                case = make_case(
                    only_required,
                    CoverageScenario.OBJECT_ONLY_REQUIRED,
                    "Only required properties",
                    location,
                    None,
                    GenerationMode.POSITIVE,
                    Instant(),
                )
                if case is not None:
                    yield case
            if GenerationMode.NEGATIVE in generation_modes:
                subschema = _combination_schema(only_required, required, parameter_set)
                yield from _yield_negative(subschema, location, bool(required), Dedup.WIRE_NEGATIVE_SET)

        # 2. Generate combinations with required properties and one optional property
        for opt_param in optional:
            combo = {k: v for k, v in base_container.items() if k in required or k == opt_param}
            if combo != base_container and GenerationMode.POSITIVE in generation_modes:
                if not (template.has_required_body and not template.has_generated_required_body):
                    case = make_case(
                        combo,
                        CoverageScenario.OBJECT_REQUIRED_AND_OPTIONAL,
                        f"All required properties and optional '{opt_param}'",
                        location,
                        None,
                        GenerationMode.POSITIVE,
                        Instant(),
                    )
                    if case is not None:
                        yield case
                if GenerationMode.NEGATIVE in generation_modes:
                    subschema = _combination_schema(combo, required, parameter_set)
                    yield from _yield_negative(subschema, location, bool(required), Dedup.WIRE_REQUEST)

        # 3. Generate one combination for each size from 2 to N-1 of optional parameters
        if (
            len(optional) > 1
            and GenerationMode.POSITIVE in generation_modes
            and not (template.has_required_body and not template.has_generated_required_body)
        ):
            for size in range(2, len(optional)):
                for combination in combinations(optional, size):
                    combo = {k: v for k, v in base_container.items() if k in required or k in combination}
                    if combo != base_container:
                        case = make_case(
                            combo,
                            CoverageScenario.OBJECT_REQUIRED_AND_OPTIONAL,
                            f"All required and {size} optional properties",
                            location,
                            None,
                            GenerationMode.POSITIVE,
                            Instant(),
                        )
                        if case is not None:
                            yield case
                        break


def iter_coverage_cases(
    *,
    operation: APIOperation,
    generation_modes: list[GenerationMode],
    generate_duplicate_query_parameters: bool,
    unexpected_methods: set[str],
    generation_config: GenerationConfig,
    extra_data_source: ResourcePool | None = None,
    unexpected_methods_seen: set[tuple[str, str]] | None = None,
    error_feedback: ErrorFeedbackStore | None = None,
    session: GenerationSession | None = None,
) -> Generator[Case, None, None]:
    generators: dict[tuple[ParameterLocation, str], Generator[GeneratedValue, None, None]] = {}
    serializers = operation.get_parameter_serializers()
    template = Template(serializers)

    responses = list(operation.responses.iter_examples())
    custom_formats = operation.schema.get_custom_format_strategies(generation_config, GenerationMode.POSITIVE)

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

    emitter = CaseEmitter(
        operation=operation,
        correlated=correlated,
        correlated_draws=correlated_draws,
        correlated_misses=correlated_misses,
        seen_positive=HashSet(),
        seen_negative=HashSet(),
    )
    run = CoverageRun(
        operation=operation,
        template=template,
        emitter=emitter,
        generators=generators,
        generation_modes=generation_modes,
        generation_config=generation_config,
        custom_formats=custom_formats,
        validator_cls=validator_cls,
        update_pattern=update_pattern,
        session=session,
        error_feedback=error_feedback,
        responses=responses,
        correlated=correlated,
    )
    _seed_parameters(run)
    if operation.body:
        yield from _body_cases(run)
    else:
        yield from _default_positive(run)
    yield from _parameter_mutations(run)
    if not template.can_emit(GenerationMode.NEGATIVE):
        # The remaining blocks emit NEGATIVE param-mutation cases (missing/duplicate/etc.)
        # built off the template body. Combined with a fallback-negative body they would
        # mix two negatives in one case.
        return
    if GenerationMode.NEGATIVE in generation_modes:
        yield from _unexpected_methods(run, unexpected_methods, unexpected_methods_seen)
        yield from _duplicate_query(run, generate_duplicate_query_parameters)
        yield from _missing_required(run)
    yield from _container_combinations(run)
