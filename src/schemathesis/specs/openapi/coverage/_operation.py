"""Operation-level coverage.

Enumerate cases for an OpenAPI operation by combining parameter, header, body, and
response coverage into concrete `Case` instances.
"""

from __future__ import annotations

from collections.abc import Callable, Generator
from dataclasses import dataclass, replace
from enum import Enum, auto
from itertools import combinations
from typing import TYPE_CHECKING, Any, TypeGuard, cast
from urllib.parse import unquote

import jsonschema_rs
from requests.structures import CaseInsensitiveDict

from schemathesis.core import NOT_SET, NotSet, media_types
from schemathesis.core.errors import InvalidSchema, MalformedMediaType
from schemathesis.core.jsonschema import (
    BUNDLE_STORAGE_KEY,
    make_validator,
    resolve_bundled_ref,
    resolve_bundled_root,
    schema_with_bundle,
)
from schemathesis.core.jsonschema.patterns import update_quantifier
from schemathesis.core.jsonschema.types import JsonSchemaObject, as_object_schema
from schemathesis.core.media_types import (
    FORM_MEDIA_TYPES,
    iter_matching_media_type_strategies,
)
from schemathesis.core.parameters import CONTAINER_TO_LOCATION, LOCATION_TO_CONTAINER, ParameterLocation
from schemathesis.core.timing import Instant
from schemathesis.core.transforms import UNRESOLVABLE, deepclone, is_array_index, resolve_path, split_body_key
from schemathesis.core.transport import HTTP_METHODS_SCHEMA
from schemathesis.generation import GenerationMode
from schemathesis.generation.case import Case
from schemathesis.generation.coverage import GenerationSession
from schemathesis.generation.hypothesis import examples
from schemathesis.generation.hypothesis._response_matching import find_matching_in_responses
from schemathesis.generation.meta import (
    CaseMetadata,
    ComponentInfo,
    CoverageScenario,
    GenerationInfo,
    PhaseInfo,
)
from schemathesis.specs.openapi._hypothesis import _build_custom_formats
from schemathesis.specs.openapi.adapter.parameters import OpenApiParameterSet, filter_schema_valid_examples
from schemathesis.specs.openapi.coverage._schema import (
    CoverageContext,
    GeneratedValue,
    HashSet,
    _to_hashable_key_with_json_parts,
    cover_schema_iter,
    drop_negatives_any_draft_admits,
)
from schemathesis.specs.openapi.coverage._wire import stringify_value
from schemathesis.specs.openapi.error_feedback import apply_adjustments
from schemathesis.specs.openapi.utils import parameter_types, sent_text_is_valid
from schemathesis.transport.prepare import prepare_path
from schemathesis.transport.serialization import quote_all

if TYPE_CHECKING:
    from hypothesis.strategies import SearchStrategy

    from schemathesis.config import GenerationConfig
    from schemathesis.core.error_feedback import ErrorFeedbackStore
    from schemathesis.core.parameters import ContainerName
    from schemathesis.core.transport import HttpMethod, HttpMethodSchema
    from schemathesis.resources import PoolDraw, ResourcePool
    from schemathesis.schemas import APIOperation, ParameterSet
    from schemathesis.specs.openapi.adapter.parameters import OpenApiBody, OpenApiParameter
    from schemathesis.specs.openapi.schemas import OpenApiOperation

# Case keyword arguments that describe the request body.
BODY_KEYS = ("body", "media_type", "multipart_content_types")


def _set_multipart_content_types(kwargs: dict[str, Any], content_types: dict[str, str] | None) -> None:
    if content_types:
        kwargs["multipart_content_types"] = content_types
    else:
        kwargs.pop("multipart_content_types", None)


class Template:
    __slots__ = (
        "_components",
        "_content_type_header",
        "_json_encoded",
        "_optional_query",
        "_parameter_modes",
        "_serializers",
        "_template",
        "body_is_fallback_negative",
        "has_generated_required_body",
        "has_required_body",
        "seed_time",
        "unsatisfiable_required_parameter",
    )

    def __init__(
        self,
        serializers: dict[str, Callable],
        optional_query: frozenset[str],
        json_encoded: frozenset[tuple[str, str]],
    ) -> None:
        self._components: dict[ParameterLocation, ComponentInfo] = {}
        self._parameter_modes: dict[ParameterLocation, dict[str, GenerationMode]] = {}
        self._template: dict[str, Any] = {}
        self._serializers = serializers
        self._optional_query = optional_query
        self._json_encoded = json_encoded
        # Declared `Content-Type` header name and the body media types its schema admits.
        self._content_type_header: tuple[str, frozenset[str]] | None = None
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
        container = self._template.setdefault(location.container_name, {})
        container[name] = value.value
        self._parameter_modes.setdefault(location, {})[name] = value.generation_mode
        self._components[location] = ComponentInfo(mode=self._mode_for(location, container))

    def _mode_for(self, location: ParameterLocation, container: dict[str, Any]) -> GenerationMode:
        """The mode a location carries, given the values its container currently holds."""
        modes = self._parameter_modes.get(location, {})
        if any(modes.get(name) == GenerationMode.NEGATIVE for name in container):
            return GenerationMode.NEGATIVE
        return GenerationMode.POSITIVE

    def pin_content_type(self, name: str, admitted: frozenset[str]) -> None:
        self._content_type_header = (name, admitted)

    def _headers_for(self, media_type: str) -> dict[str, Any] | None:
        """Headers whose pinned `Content-Type` names `media_type`, or `None` when they stay as they are."""
        if self._content_type_header is None:
            return None
        name, admitted = self._content_type_header
        headers = self._template.get("headers")
        if media_type not in admitted or not isinstance(headers, dict) or name not in headers:
            return None
        return {**headers, name: media_type}

    def set_body(
        self, body: GeneratedValue, media_type: str, multipart_content_types: dict[str, str] | None = None
    ) -> None:
        self._template["body"] = body.value
        self._template["media_type"] = media_type
        _set_multipart_content_types(self._template, multipart_content_types)
        self._components[ParameterLocation.BODY] = ComponentInfo(mode=body.generation_mode)
        headers = self._headers_for(media_type)
        if headers is not None:
            self._template["headers"] = headers

    def _serialize(self, kwargs: dict[str, Any], components: dict[ParameterLocation, ComponentInfo]) -> dict[str, Any]:
        output = {}
        for container_name, value in kwargs.items():
            serializer = self._serializers.get(container_name)
            if container_name in ("headers", "cookies") and isinstance(value, dict):
                # A JSON-encoded parameter keeps its nested types until the serializer writes it as JSON text.
                value = {
                    name: item
                    if (container_name, name) in self._json_encoded
                    else stringify_value(item, container_name)
                    for name, item in value.items()
                }
            if serializer is not None:
                # Shallow-copy dict containers before serializing to avoid mutating
                # self._template through shared references in shallow-copy kwargs
                value = serializer(dict(value))
            if container_name == "query" and isinstance(value, dict):
                query_component = components.get(ParameterLocation.QUERY)
                if query_component is None or query_component.mode == GenerationMode.POSITIVE:
                    # Query strings have no rendering for a JSON null; leaving an optional parameter out is what
                    # "no value" means there. Negative data keeps `null`, since sending it is the point of the case.
                    value = {
                        name: item
                        for name, item in value.items()
                        if item is not None or name not in self._optional_query
                    }
                value = stringify_value(value, container_name)
            if container_name == "path_parameters" and isinstance(value, dict):
                # dict() copy prevents quote_all from mutating self._template
                value = stringify_value(quote_all(dict(value)), container_name)
            output[container_name] = value
        return output

    def unmodified(self) -> TemplateValue:
        raw = deepclone(self._template)
        components = self._components.copy()
        kwargs = self._serialize(raw, components)
        return TemplateValue(kwargs=kwargs, raw=raw, components=components)

    def with_body(
        self, *, media_type: str, value: GeneratedValue, multipart_content_types: dict[str, str] | None = None
    ) -> TemplateValue:
        raw = {**self._template, "media_type": media_type, "body": value.value}
        _set_multipart_content_types(raw, multipart_content_types)
        headers = self._headers_for(media_type)
        if headers is not None:
            raw["headers"] = headers
        components = {**self._components, ParameterLocation.BODY: ComponentInfo(mode=value.generation_mode)}
        kwargs = self._serialize(raw, components)
        return TemplateValue(kwargs=kwargs, raw=raw, components=components)

    def without_body(self) -> TemplateValue:
        # A `Content-Type` describing a body that is not there is a different oddity than sending no body at all.
        raw = {key: value for key, value in self._template.items() if key not in BODY_KEYS}
        components = {**self._components, ParameterLocation.BODY: ComponentInfo(mode=GenerationMode.NEGATIVE)}
        headers = raw.get("headers")
        if isinstance(headers, dict):
            headers = {name: value for name, value in headers.items() if name.lower() != "content-type"}
            raw["headers"] = headers
            components[ParameterLocation.HEADER] = ComponentInfo(mode=self._mode_for(ParameterLocation.HEADER, headers))
        kwargs = self._serialize(raw, components)
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
        kwargs = self._serialize(raw, components)
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


def _dedup_key_and_hashes(
    kwargs: dict[str, Any],
) -> tuple[dict[str, Any], tuple, dict[ParameterLocation, int]]:
    request = _dedup_key(kwargs)
    key, hashes = _to_hashable_key_with_json_parts(request)
    initial_hashes = {
        location: hashes[name]
        for name, location in CONTAINER_TO_LOCATION.items()
        if name != "headers" and name in hashes and request.get(name) == kwargs.get(name)
    }
    return request, key, initial_hashes


@dataclass(slots=True)
class CaseEmitter:
    """Builds and deduplicates the cases one operation's coverage run emits."""

    operation: APIOperation
    correlated: dict[tuple[ParameterLocation, str], Any]
    correlated_draws: tuple[PoolDraw, ...]
    correlated_misses: tuple[tuple[str, str], ...]
    seen_positive: HashSet
    seen_negative: HashSet

    def is_new_request(self, kwargs: dict[str, Any], mode: GenerationMode) -> tuple[bool, dict[ParameterLocation, int]]:
        # Repeating a request already sent tests nothing, whichever mode sent it first.
        request, key, initial_hashes = _dedup_key_and_hashes(kwargs)
        if mode == GenerationMode.POSITIVE:
            is_new = self.seen_positive.insert(request, key=key)
        else:
            is_new = self.seen_negative.insert_unless_in(self.seen_positive, request, key=key)
        return is_new, initial_hashes

    def _meta(
        self,
        *,
        generation: GenerationInfo,
        components: dict[ParameterLocation, ComponentInfo],
        phase: PhaseInfo,
        raw: dict[str, Any],
        initial_hashes: dict[ParameterLocation, int],
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
        meta = CaseMetadata(
            generation=generation,
            components=components,
            phase=phase,
            pool_draws=_filter_draws_for_case(raw, self.correlated, self.correlated_draws),
            pool_misses=_filter_misses_for_case(raw, self.correlated_misses),
            raw_containers=raw_containers,
        )
        meta._initial_hashes.update(
            (location, hash_value) for location, hash_value in initial_hashes.items() if location in components
        )
        return meta

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
        initial_hashes: dict[ParameterLocation, int] | None = None,
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
                initial_hashes={} if initial_hashes is None else initial_hashes,
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
        initial_hashes: dict[ParameterLocation, int] = {}
        if dedup is Dedup.REQUEST:
            is_new, initial_hashes = self.is_new_request(checked_kwargs, mode)
            if not is_new:
                return None
        if dedup is Dedup.NEGATIVE_SET:
            request, key, initial_hashes = _dedup_key_and_hashes(checked_kwargs)
            if not self.seen_negative.insert(request, key=key):
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
            initial_hashes=initial_hashes,
        )
        if dedup is Dedup.WIRE_REQUEST:
            is_new, _ = self.is_new_request(_wire_kwargs(case), mode)
            if not is_new:
                return None
        if dedup is Dedup.WIRE_NEGATIVE_SET and not self.seen_negative.insert(_dedup_key(_wire_kwargs(case))):
            return None
        return case


def _case_to_kwargs(case: Case) -> dict:
    kwargs = {}
    for container_name in LOCATION_TO_CONTAINER.values():
        value = getattr(case, container_name)
        if isinstance(value, CaseInsensitiveDict) and value:
            kwargs[container_name] = dict(value)
        elif value and value is not NOT_SET:
            kwargs[container_name] = value
    return kwargs


def _wire_kwargs(case: Case) -> dict[str, Any]:
    # Keyed like the template, so a case that sends the same request as a body or parameter case is caught.
    kwargs = _case_to_kwargs(case)
    if case.body is not NOT_SET:
        kwargs["body"] = case.body
        kwargs["media_type"] = case.media_type
        _set_multipart_content_types(kwargs, case.multipart_content_types)
    return kwargs


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
    if not normalized:
        # An empty query sends the same request as no query at all.
        return {key: value for key, value in kwargs.items() if key != "query"}
    return {**kwargs, "query": normalized}


_GATING_KEYS = frozenset({"example", "examples", "default", "enum", "const"})


def _is_pool_eligible(schema: object) -> TypeGuard[dict[str, Any]]:
    return isinstance(schema, dict) and not (_GATING_KEYS & schema.keys())


def _with_captured_value_first(
    schema: dict[str, Any], value: object, validator_cls: type[jsonschema_rs.Validator]
) -> dict[str, Any]:
    """Lead with a value the API returned earlier; declared values stay as their own cases."""
    if _is_pool_eligible(schema):
        return {**schema, "examples": [value]}
    # Declared values are often placeholders the API does not know, so only the constraints decide.
    constraints = {key: item for key, item in schema.items() if key not in ("example", "examples", "default")}
    if not make_validator(constraints, validator_cls).is_valid(value):
        return schema
    if "enum" in schema:
        return {**schema, "enum": [value, *(member for member in schema["enum"] if member != value)]}
    declared = [*schema.get("examples", []), *([schema["example"]] if "example" in schema else [])]
    updated = {key: item for key, item in schema.items() if key != "example"}
    updated["examples"] = [value, *(item for item in declared if item != value)]
    return updated


def _with_body_pool_values(
    schema: dict[str, Any], correlated: dict[tuple[ParameterLocation, str], Any], validator_cls: type
) -> dict[str, Any]:
    """Lead body fields the pool can fill with captured values, through objects, array items and `$ref`."""
    bundled = schema.get(BUNDLE_STORAGE_KEY)
    # Shallower fields first, so a captured whole object wins over captured fields inside it.
    fields = sorted(
        ((name, value) for (location, name), value in correlated.items() if location == ParameterLocation.BODY),
        key=lambda item: item[0].count("/"),
    )
    filled: set[str] = set()
    updated = schema
    for name, value in fields:
        path = split_body_key(name)
        if path[0] in filled:
            continue
        candidate = _with_leaf_example(updated, path, value, bundled, schema, validator_cls)
        if candidate is not None:
            updated = candidate
            if len(path) == 1:
                filled.add(name)
    return updated


def _with_leaf_example(
    node: dict[str, Any],
    path: list[str],
    value: object,
    bundled: object,
    body_schema: dict[str, Any],
    validator_cls: type,
) -> dict[str, Any] | None:
    """Copy of `node` whose schema at `path` lists `value` as its example; None when `path` can't take it."""
    resolved = _inline_ref(node, bundled)
    if resolved is None:
        return None
    step, *rest = path
    properties = resolved.get("properties")
    if not isinstance(properties, dict):
        properties = {}
    # A digit segment is an array index only when the node has no property of that name.
    is_property = step in properties
    if is_property:
        child = properties[step]
    elif is_array_index(step):
        # Every array item shares the `items` schema, so the value reaches each generated item.
        child = resolved.get("items")
    else:
        child = None
    if not isinstance(child, dict):
        return None
    if rest:
        replacement = _with_leaf_example(child, rest, value, bundled, body_schema, validator_cls)
        if replacement is None:
            return None
    else:
        if not _is_pool_eligible(child):
            return None
        if not make_validator(schema_with_bundle(child, body_schema), validator_cls).is_valid(value):
            return None
        replacement = {**child, "examples": [value]}
    if is_property:
        return {**resolved, "properties": {**properties, step: replacement}}
    return {**resolved, "items": replacement}


def _inline_ref(node: dict[str, Any], bundled: object) -> dict[str, Any] | None:
    """`node` with its bundled `$ref` target inlined; None when sibling keywords would clash with the target."""
    target = resolve_bundled_ref(node, bundled)
    if target is node:
        return node
    siblings = {key: item for key, item in node.items() if key != "$ref"}
    if siblings.keys() & target.keys():
        return None
    return {**siblings, **target}


def _generate_coverage_values_from_custom_strategy(
    media_type: str,
) -> Generator[tuple[str, GeneratedValue], None, None]:
    """Generate coverage values from a custom media type strategy."""
    for resolved_media_type, strategy in iter_matching_media_type_strategies(media_type):
        value: str | bytes = examples.generate_one(strategy)
        yield (
            resolved_media_type,
            GeneratedValue.with_positive(
                value=value,
                scenario=CoverageScenario.EXAMPLE_VALUE,
                description=f"Custom media type: {resolved_media_type}",
            ),
        )


def _generate_multipart_body_from_custom_strategies(
    body: OpenApiBody,
) -> tuple[dict[str, Any], dict[str, str]] | None:
    """Generate a body dict for multipart forms using custom encoding strategies, with each part's content type.

    Returns None if the body doesn't have custom encoding strategies or isn't a form type.
    """
    if body.media_type not in FORM_MEDIA_TYPES:
        return None

    schema = resolve_bundled_root(as_object_schema(body.definition.get("schema", {})))
    properties = schema.get("properties", {})
    required = schema.get("required", [])

    result: dict[str, Any] = {}
    content_types: dict[str, str] = {}

    for prop_name in properties:
        for ct in body.get_property_content_types(prop_name):
            match = next(iter_matching_media_type_strategies(ct), None)
            if match is not None:
                content_types[prop_name], strategy = match
                result[prop_name] = examples.generate_one(strategy)
                break

    for prop_name in required:
        if prop_name not in result:
            prop_schema = properties.get(prop_name, {})
            result[prop_name] = b"" if prop_schema.get("format") == "binary" else ""

    return (result, content_types) if content_types else None


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
        location = ParameterLocation(draw.location)
        expected = correlated[(location, draw.parameter_name)]
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
        location = ParameterLocation(miss[0])
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
        value = resolve_path(container, split_body_key(parameter_name))
        return _SENTINEL_ABSENT if value is UNRESOLVABLE else value
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


def _coverage_context(
    run: CoverageRun,
    schema: dict[str, Any],
    location: ParameterLocation,
    generation_modes: list[GenerationMode],
    *,
    is_required: bool,
    media_type: tuple[str, str] | None = None,
) -> CoverageContext:
    return CoverageContext(
        session=run.session,
        root_schema=schema,
        location=location,
        media_type=media_type,
        generation_modes=generation_modes,
        is_required=is_required,
        custom_formats=run.custom_formats,
        validator_cls=run.validator_cls,
        update_pattern=run.update_pattern,
        allow_extra_parameters=run.generation_config.allow_extra_parameters,
    )


def _json_media_type(parameter: OpenApiParameter) -> tuple[str, str] | None:
    """The media type of a parameter that travels as JSON text rather than in its location's own style."""
    if next(iter(parameter.definition.get("content", {})), None) == "application/json":
        return ("application", "json")
    return None


def _positive_fallback(run: CoverageRun, parameter: OpenApiParameter, schema: dict[str, Any]) -> GeneratedValue | None:
    """The value a positive run would seed for this parameter, if any."""
    generator = cover_schema_iter(
        _coverage_context(
            run, schema, parameter.location, [GenerationMode.POSITIVE], is_required=parameter.is_required
        ),
        schema,
    )
    return next(generator, None)


def _reject_malformed_definition(
    operation: APIOperation, schema: JsonSchemaObject, validator_cls: type[jsonschema_rs.Validator]
) -> None:
    """Report a definition its own draft rejects as fuzzing does, instead of walking keywords of the wrong type."""
    try:
        make_validator(schema, validator_cls)
    except jsonschema_rs.ValidationError as exc:
        raise InvalidSchema.from_jsonschema_error(
            exc, path=operation.path, method=operation.method, config=operation.schema.config.output
        ) from None
    except ValueError:
        # Not a draft violation: a value JSON cannot hold, e.g. YAML `!!binary` bytes, which the walk handles itself.
        return


def _seed_parameters(run: CoverageRun) -> None:
    operation = run.operation
    template = run.template
    generators = run.generators
    generation_modes = run.generation_modes
    validator_cls = run.validator_cls
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
        base = getattr(operation, target_location.container_name).schema
        adjusted = apply_adjustments(
            operation=operation,
            location=target_location,
            schema=base,
            store=error_feedback,
        )
        result: dict[str, Any] | None = None
        # `apply_adjustments` returns the input unchanged when there are no observations;
        # only splice when something was actually inferred.
        if adjusted is not base and isinstance(adjusted, dict):
            result = adjusted["properties"]
        inferred_properties_per_location[target_location] = result
        return result

    for parameter in operation.iter_parameters():
        location = parameter.location
        name = parameter.name
        schema = as_object_schema(parameter.unoptimized_schema)
        schema_is_clone = False
        if error_feedback is not None and isinstance(schema, dict):
            inferred_properties = _inferred_properties(location)
            if inferred_properties is not None:
                inferred = inferred_properties.get(name)
                if isinstance(inferred, dict):
                    schema = {**schema, **inferred}
                    schema_is_clone = True
        examples = filter_schema_valid_examples(parameter.examples, schema, validator_cls)
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
        pool_value = correlated.get((location, name))
        if pool_value is not None:
            schema = _with_captured_value_first(schema, pool_value, validator_cls)
        gen = cover_schema_iter(
            _coverage_context(
                run,
                schema,
                location,
                generation_modes,
                is_required=parameter.is_required,
                media_type=_json_media_type(parameter),
            ),
            schema,
        )
        if parameter.wire_bounds:
            gen = _drop_negatives_the_schema_admits(gen, parameter.without_wire_bounds(schema), validator_cls)
        value = next(gen, NOT_SET)
        # Pin the template's Content-Type to the body media type when CT is declared as an explicit
        # header parameter — otherwise body cases inherit a fuzzed CT (often empty) and ship bodies
        # that downstream tools can't dispatch. CT-mutation variants still flow through the iterator.
        if location == ParameterLocation.HEADER and name.lower() == "content-type" and operation.body:
            admitted = [
                alternative.media_type
                for alternative in operation.body
                if parameter.admits(alternative.media_type, validator_cls)
            ]
            if admitted:
                template.pin_content_type(name, frozenset(admitted))
                value = GeneratedValue.with_positive(
                    value=admitted[0],
                    scenario=CoverageScenario.VALID_STRING,
                    description="Valid Content-Type pinned to body media type",
                )
        if isinstance(value, NotSet):
            if location != ParameterLocation.PATH and not parameter.is_required:
                continue
            if location == ParameterLocation.PATH:
                # Interpolated into the URL, so it needs a non-empty value even when its schema offers none.
                schema = dict(schema)
                schema.setdefault("type", "string")
                schema.setdefault("minLength", 1)
            # Dropping a required parameter would leave it out of every case built off this template, so
            # those cases would fail on the omission instead of the mutation they target.
            fallback = _positive_fallback(run, parameter, schema)
            if fallback is None and location == ParameterLocation.PATH:
                fallback = GeneratedValue(
                    "value",
                    generation_mode=GenerationMode.NEGATIVE,
                    scenario=CoverageScenario.UNSUPPORTED_PATH_PATTERN,
                    description="Sample value for unsupported path parameter pattern",
                    parameter=name,
                    location="/",
                )
            # Without a positive value no case built off this template is a valid positive request.
            if fallback is None or fallback.generation_mode == GenerationMode.NEGATIVE:
                template.unsatisfiable_required_parameter = True
            if fallback is None:
                continue
            template.add_parameter(location, name, fallback)
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


def _is_invalid_positive(template: Template, value: GeneratedValue) -> bool:
    """Whether a required parameter without a positive value already invalidates this body case."""
    return value.generation_mode == GenerationMode.POSITIVE and template.unsatisfiable_required_parameter


def _container_without_wire_bounds(
    schema: JsonSchemaObject, parameter_set: ParameterSet[OpenApiParameter]
) -> JsonSchemaObject | None:
    """Restore what the document declares wherever a serialization-implied bound replaced it."""
    properties = schema["properties"]
    declared = {
        parameter.name: parameter.without_wire_bounds(properties[parameter.name])
        for parameter in parameter_set
        if parameter.wire_bounds and isinstance(properties.get(parameter.name), dict)
    }
    if not declared:
        return None
    return {**schema, "properties": {**properties, **declared}}


def _drop_negatives_the_schema_admits(
    values: Generator[GeneratedValue, None, None],
    schema: JsonSchemaObject,
    validator_cls: type[jsonschema_rs.Validator],
) -> Generator[GeneratedValue, None, None]:
    """Keep only the negatives the API contract actually rejects."""
    try:
        validator = make_validator(schema, validator_cls)
    except Exception:
        # Schema rejected by `jsonschema_rs` — validity is unknown, so keep everything.
        yield from values
        return
    for value in values:
        if value.generation_mode == GenerationMode.NEGATIVE and validator.is_valid(value.value):
            continue
        yield value


def _body_cases(run: CoverageRun) -> Generator[Case, None, None]:
    operation = run.operation
    template = run.template
    emitter = run.emitter
    generation_modes = run.generation_modes
    validator_cls = run.validator_cls
    error_feedback = run.error_feedback
    correlated = run.correlated
    for body in operation.body:
        instant = Instant()

        multipart = _generate_multipart_body_from_custom_strategies(body)
        custom_values: list[tuple[str, dict[str, str] | None, GeneratedValue]]
        if multipart is not None:
            multipart_body, part_content_types = multipart
            custom_values = [
                (
                    body.media_type,
                    part_content_types,
                    GeneratedValue.with_positive(
                        value=multipart_body,
                        scenario=CoverageScenario.EXAMPLE_VALUE,
                        description="Multipart body with custom encoding",
                    ),
                )
            ]
        else:
            custom_values = [
                (media_type, None, value)
                for media_type, value in _generate_coverage_values_from_custom_strategy(body.media_type)
            ]

        if custom_values:
            first_media_type, first_content_types, first_custom_value = custom_values[0]
            if body.is_required:
                template.has_generated_required_body = True
            elapsed = instant.elapsed
            if "body" not in template:
                template.seed_time += elapsed
                template.set_body(first_custom_value, first_media_type, first_content_types)
            if GenerationMode.POSITIVE in generation_modes:
                for resolved_media_type, content_types, custom_value in custom_values:
                    if _is_invalid_positive(template, custom_value):
                        continue
                    data = template.with_body(
                        value=custom_value, media_type=resolved_media_type, multipart_content_types=content_types
                    )
                    yield emitter.build(
                        data,
                        mode=custom_value.generation_mode,
                        elapsed=elapsed,
                        scenario=custom_value.scenario,
                        description=custom_value.description,
                        location=custom_value.location,
                        parameter=resolved_media_type,
                        parameter_location=ParameterLocation.BODY,
                    )
            continue

        schema = as_object_schema(body.unoptimized_schema)
        _reject_malformed_definition(operation, schema, validator_cls)
        schema_is_clone = False
        if error_feedback is not None:
            adjusted = apply_adjustments(
                operation=operation,
                location=ParameterLocation.BODY,
                schema=schema,
                store=error_feedback,
            )
            if adjusted is not schema:
                schema = as_object_schema(adjusted)
                schema_is_clone = True
        examples = body.examples
        if examples:
            # A spec's `examples` may not match its own schema (real-world specs often disagree); an
            # unvalidated one seeds the generator with a value it can never converge on, silently
            # collapsing positive body generation for the whole operation.
            examples = filter_schema_valid_examples(examples, schema, validator_cls)
        if examples:
            if not schema_is_clone:
                schema = dict(schema)
            schema["examples"] = examples
        schema = _with_body_pool_values(schema, correlated, validator_cls)
        try:
            media_type = media_types.parse(body.media_type)
        except MalformedMediaType as exc:
            raise InvalidSchema.from_malformed_media_type(
                exc, body.media_type, path=operation.path, method=operation.method
            ) from exc
        body_ctx = _coverage_context(
            run, schema, ParameterLocation.BODY, generation_modes, is_required=body.is_required, media_type=media_type
        )
        gen = drop_negatives_any_draft_admits(body_ctx, schema, cover_schema_iter(body_ctx, schema))
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
                    _coverage_context(
                        run,
                        schema,
                        ParameterLocation.BODY,
                        [GenerationMode.POSITIVE],
                        is_required=body.is_required,
                        media_type=media_type,
                    ),
                    schema,
                )
                first_positive = next(pos_gen, NOT_SET)
                if isinstance(first_positive, NotSet):
                    template.body_is_fallback_negative = True
                    template.set_body(value, body.media_type)
                else:
                    template.set_body(first_positive, body.media_type)
        if not _is_invalid_positive(template, value):
            case = _emit_body_case(run, value, body.media_type, elapsed)
            # A deduplicated first value skips the rest of this body alternative.
            if case is None:
                continue
            yield case
        iterator = iter(gen)
        while True:
            instant = Instant()
            try:
                next_value = next(iterator)
                if _is_invalid_positive(template, next_value):
                    continue
                case = _emit_body_case(run, next_value, body.media_type, instant.elapsed)
                if case is not None:
                    yield case
            except StopIteration:
                break


def _emit_body_case(run: CoverageRun, value: GeneratedValue, media_type: str, elapsed: float) -> Case | None:
    return run.emitter.emit(
        run.template.with_body(value=value, media_type=media_type),
        mode=value.generation_mode,
        elapsed=elapsed,
        scenario=value.scenario,
        description=value.description,
        location=value.location,
        parameter=media_type,
        parameter_location=ParameterLocation.BODY,
    )


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
    # The first request of a run, so nothing sent earlier can repeat it.
    assert case is not None
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
                kwargs = {k: v for k, v in kwargs.items() if k not in BODY_KEYS}
                raw = {k: v for k, v in raw.items() if k not in BODY_KEYS}

            if value.generation_mode == GenerationMode.NEGATIVE:
                if not template.can_emit(GenerationMode.NEGATIVE):
                    # Skip: would emit a case with NEGATIVE body + NEGATIVE param.
                    continue
            else:
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


def _declared_methods(operation: APIOperation, sent_path: str) -> set[str]:
    """Methods the schema routes to the sent URL, including through templated sibling paths."""
    schema = operation.schema
    methods = set(schema[operation.path])
    for template in schema.path_templates:
        if template.path == operation.path:
            continue
        match = template.pattern.fullmatch(sent_path)
        if match is None:
            continue
        # Routers decode path segments before handing them to the handler.
        values = {name: unquote(value) for name, value in zip(template.names, match.groups(), strict=True)}
        path_item = schema[template.path]
        for method in path_item:
            if method in methods or method not in HTTP_METHODS_SCHEMA:
                continue
            try:
                sibling = path_item[method]
            except InvalidSchema:
                continue
            if _segments_fit_parameters(sibling, values):
                methods.add(method)
    return methods


def _segments_fit_parameters(operation: APIOperation, values: dict[str, str]) -> bool:
    # A segment the parameter schema rejects may still be routed elsewhere, so the method stays undeclared.
    for parameter in operation.path_parameters:
        value = values.get(parameter.name)
        if value is None:
            continue
        schema = parameter.validation_schema
        validator = make_validator(schema, parameter.adapter.jsonschema_validator_cls)
        if not sent_text_is_valid(value, validator.is_valid, parameter_types(schema)):
            return False
    return True


def _unexpected_methods(
    run: CoverageRun, unexpected_methods: set[str], unexpected_methods_seen: set[tuple[str, str]] | None
) -> Generator[Case, None, None]:
    operation = run.operation
    template = run.template
    emitter = run.emitter
    sent_path = prepare_path(operation.path, template.unmodified().kwargs.get("path_parameters"))
    # Path-level: each `(path, method)` pair runs once across declared operations.
    methods = sorted(unexpected_methods - _declared_methods(operation, sent_path))
    filter_set = operation.schema.filter_set
    for method in methods:
        # Excluding an operation means "never send this request", even when the schema does not declare it.
        if filter_set.is_explicitly_excluded(replace(operation, method=cast("HttpMethodSchema", method), label="")):
            continue
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


def _content_type_probes(run: CoverageRun) -> Generator[Case, None, None]:
    operation = run.operation
    transport = operation.schema.transport
    # An operation with no serializable body is reported as untestable; a probe would hide that.
    if operation.body and all(
        transport.get_first_matching_media_type(body.media_type) is None for body in operation.body
    ):
        return
    template = run.template
    emitter = run.emitter
    data = template.unmodified()
    data = TemplateValue(
        kwargs=data.kwargs,
        raw=data.raw,
        components={**data.components, ParameterLocation.HEADER: ComponentInfo(mode=GenerationMode.NEGATIVE)},
    )
    for scenario, content_type, description in (
        (
            CoverageScenario.MALFORMED_CONTENT_TYPE,
            "multipart/form-data",
            "Malformed Content-Type: multipart/form-data without boundary",
        ),
        (
            CoverageScenario.UNSUPPORTED_CONTENT_TYPE,
            "application/xml" if any(body.media_type == "text/plain" for body in run.operation.body) else "text/plain",
            None,
        ),
    ):
        if scenario == CoverageScenario.UNSUPPORTED_CONTENT_TYPE and not run.operation.body:
            continue
        headers = {
            name: value for name, value in data.kwargs.get("headers", {}).items() if name.lower() != "content-type"
        }
        headers["Content-Type"] = content_type
        kwargs = {**data.kwargs, "headers": headers}
        raw_headers = {
            name: value for name, value in data.raw.get("headers", {}).items() if name.lower() != "content-type"
        }
        raw_headers["Content-Type"] = content_type
        raw = {**data.raw, "headers": raw_headers}
        if (
            scenario == CoverageScenario.MALFORMED_CONTENT_TYPE
            and data.kwargs.get("media_type") == "multipart/form-data"
        ):
            kwargs = {key: value for key, value in kwargs.items() if key not in BODY_KEYS}
            raw = {key: value for key, value in raw.items() if key not in BODY_KEYS}
        yield emitter.build(
            data,
            mode=GenerationMode.NEGATIVE,
            elapsed=Instant().elapsed,
            scenario=scenario,
            description=description or f"Unsupported Content-Type: {content_type}",
            location="/",
            parameter="Content-Type",
            parameter_location=ParameterLocation.HEADER,
            kwargs=kwargs,
            raw=raw,
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
                kwargs = {k: v for k, v in kwargs.items() if k not in BODY_KEYS}
                raw = {k: v for k, v in raw.items() if k not in BODY_KEYS}

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
    if template.has_required_body:
        yield from _missing_body(run)


def _missing_body(run: CoverageRun) -> Generator[Case, None, None]:
    """Omit a body the operation declares it cannot do without."""
    instant = Instant()
    case = run.emitter.emit(
        run.template.without_body(),
        mode=GenerationMode.NEGATIVE,
        elapsed=instant.elapsed,
        scenario=CoverageScenario.MISSING_PARAMETER,
        description="Missing request body",
        parameter_location=ParameterLocation.BODY,
        dedup=Dedup.NEGATIVE_SET,
    )
    if case is not None:
        yield case


def _container_combinations(run: CoverageRun) -> Generator[Case, None, None]:
    operation = run.operation
    template = run.template
    emitter = run.emitter
    generation_modes = run.generation_modes
    validator_cls = run.validator_cls
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
                parameter.name: parameter.unoptimized_schema
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
            subschema: dict[str, Any],
            _location: ParameterLocation,
            is_required: bool,
            _dedup: Dedup,
            _declared: JsonSchemaObject | None,
        ) -> Generator[Case, None, None]:
            iterator = cover_schema_iter(
                _coverage_context(run, subschema, _location, [GenerationMode.NEGATIVE], is_required=is_required),
                subschema,
            )
            if _declared is not None:
                iterator = _drop_negatives_the_schema_admits(iterator, _declared, validator_cls)
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
            if GenerationMode.POSITIVE in generation_modes and template.can_emit(GenerationMode.POSITIVE):
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
                declared = _container_without_wire_bounds(subschema, parameter_set)
                yield from _yield_negative(subschema, location, bool(required), Dedup.WIRE_NEGATIVE_SET, declared)

        # 2. Generate combinations with required properties and one optional property
        for opt_param in optional:
            combo = {k: v for k, v in base_container.items() if k in required or k == opt_param}
            if combo == base_container:
                continue
            if GenerationMode.POSITIVE in generation_modes and template.can_emit(GenerationMode.POSITIVE):
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
                declared = _container_without_wire_bounds(subschema, parameter_set)
                yield from _yield_negative(subschema, location, bool(required), Dedup.WIRE_REQUEST, declared)

        # 3. Generate one combination for each size from 2 to N-1 of optional parameters
        if (
            len(optional) > 1
            and GenerationMode.POSITIVE in generation_modes
            and template.can_emit(GenerationMode.POSITIVE)
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
    operation: OpenApiOperation,
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
    template = Template(
        serializers,
        optional_query=frozenset(parameter.name for parameter in operation.query if not parameter.is_required),
        json_encoded=frozenset(
            (parameter.location.container_name, parameter.name)
            for parameter in (*operation.headers, *operation.cookies)
            if _json_media_type(parameter) is not None
        ),
    )

    responses = list(operation.responses.iter_examples())
    custom_formats = _build_custom_formats(generation_config, GenerationMode.POSITIVE)
    validator_cls = operation.schema.adapter.jsonschema_validator_cls
    update_pattern = update_quantifier

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
    for parameter_set in (operation.path_parameters, operation.headers, operation.cookies, operation.query):
        if parameter_set:
            _reject_malformed_definition(operation, cast(OpenApiParameterSet, parameter_set).schema, validator_cls)
    _seed_parameters(run)
    if operation.has_skipped_required_body:
        # No body can be built, so every request is body-less; omitting the required body is the
        # only mutation this operation isolates, and any other one would be judged on the body.
        if GenerationMode.NEGATIVE in generation_modes:
            yield from _missing_body(run)
        return
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
        yield from _content_type_probes(run)
        yield from _duplicate_query(run, generate_duplicate_query_parameters)
        yield from _missing_required(run)
    yield from _container_combinations(run)
