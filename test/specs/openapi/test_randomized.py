from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache, partial
from typing import Any, NamedTuple

import hypothesis.errors
import jsonschema_rs
import pytest
from hypothesis import HealthCheck, Phase, given, settings
from hypothesis import strategies as st
from hypothesis.strategies import SearchStrategy

import schemathesis
import schemathesis.engine
from schemathesis.config import HealthCheck as SchemathesisHealthCheck
from schemathesis.config import SchemathesisConfig
from schemathesis.core.errors import InvalidSchema
from schemathesis.core.jsonschema.types import JsonSchema, JsonSchemaObject, JsonValue
from schemathesis.engine import events
from schemathesis.generation.jsonschema import build
from schemathesis.specs.openapi import definitions
from schemathesis.specs.openapi.formats import get_default_format_strategies

IGNORED_EXCEPTIONS = (hypothesis.errors.Unsatisfiable, hypothesis.errors.FailedHealthCheck)
config = SchemathesisConfig.from_dict({})
config.projects.default.update(suppress_health_check=[SchemathesisHealthCheck.all])
config.projects.default.phases.update(phases=["examples", "fuzzing"])
config.projects.default.generation.update(max_examples=10)

SEED = "seed"
# What every reference is repointed at, shaped so any media type has something to serialize.
SEED_SCHEMA: JsonSchemaObject = {
    "type": "object",
    "properties": {
        "name": {"type": "string", "maxLength": 8},
        "count": {"type": "integer"},
        "tags": {"type": "array", "items": {"type": "string", "maxLength": 4}, "maxItems": 2},
    },
    "required": ["name"],
}
# A drawn media type names no serializer; these do.
MEDIA_TYPES = (
    "application/json",
    "application/xml",
    "application/x-www-form-urlencoded",
    "multipart/form-data",
    "text/plain",
    "application/octet-stream",
)
# The meta-schemas assert `format: regex` on every `pattern`, which arbitrary text does not satisfy.
REGEX_FORMAT = st.sampled_from(["^a+$", "[0-9]{2}", "x|y", ".*", "[a-z]{1,4}"])
EMPTY: JsonSchemaObject = {"maxProperties": 0}
METHODS = ("get", "put", "post", "delete", "options", "head", "patch")
# `trace` arrived in Open API 3.0, `query` in 3.2.
METHODS_30 = (*METHODS, "trace")
METHODS_32 = (*METHODS_30, "query")
LOCATIONS_3 = ("query", "header", "path", "cookie")


# `eq=False` keeps the identity hash, so a spec holding its meta-schema can key a cache.
@dataclass(frozen=True, eq=False)
class Spec:
    """One Open API version, and the names its parts go by."""

    schema: JsonSchemaObject
    validator: jsonschema_rs.Validator
    draft: int
    container: str
    seed_location: tuple[str, ...]
    path_item: str
    operation: str
    request_body: str
    # Location -> the definition a parameter in it is drawn from.
    parameters: Mapping[str, str]
    methods: tuple[str, ...]
    body_as_parameter: bool = False
    media_type_keys: tuple[str, ...] = ()
    parameter_required: tuple[str, ...] = ("in",)

    @property
    def seed_pointer(self) -> str:
        return "#/" + "/".join((*self.seed_location, SEED))

    @property
    def has_webhooks(self) -> bool:
        return "webhooks" in self.schema["properties"]

    def seed_document(self) -> dict[str, Any]:
        document: dict[str, Any] = {SEED: SEED_SCHEMA}
        for key in reversed(self.seed_location):
            document = {key: document}
        return document


SPECS: dict[str, Spec] = {
    "2.0": Spec(
        schema=definitions.SWAGGER_20,
        validator=definitions.SWAGGER_20_VALIDATOR,
        draft=jsonschema_rs.Draft4,
        container="definitions",
        seed_location=("definitions",),
        path_item="pathItem",
        operation="operation",
        request_body="bodyParameter",
        # Swagger 2.0 writes each parameter location as its own definition.
        parameters={
            "query": "queryParameterSubSchema",
            "header": "headerParameterSubSchema",
            "path": "pathParameterSubSchema",
            "formData": "formDataParameterSubSchema",
        },
        methods=METHODS,
        body_as_parameter=True,
        media_type_keys=("consumes", "produces"),
        parameter_required=("name", "in", "type"),
    ),
    "3.0": Spec(
        schema=definitions.OPENAPI_30,
        validator=definitions.OPENAPI_30_VALIDATOR,
        draft=jsonschema_rs.Draft4,
        container="definitions",
        seed_location=("components", "schemas"),
        path_item="PathItem",
        operation="Operation",
        request_body="RequestBody",
        parameters=dict.fromkeys(LOCATIONS_3, "Parameter"),
        methods=METHODS_30,
    ),
    "3.1": Spec(
        schema=definitions.OPENAPI_31,
        validator=definitions.OPENAPI_31_VALIDATOR,
        draft=jsonschema_rs.Draft202012,
        container="$defs",
        seed_location=("components", "schemas"),
        path_item="path-item",
        operation="operation",
        request_body="request-body",
        parameters=dict.fromkeys(LOCATIONS_3, "parameter"),
        methods=METHODS_30,
    ),
    "3.2": Spec(
        schema=definitions.OPENAPI_32,
        validator=definitions.OPENAPI_32_VALIDATOR,
        draft=jsonschema_rs.Draft202012,
        container="$defs",
        seed_location=("components", "schemas"),
        path_item="path-item",
        operation="operation",
        request_body="request-body",
        parameters=dict.fromkeys(LOCATIONS_3, "parameter"),
        methods=METHODS_32,
    ),
}


class Entry(NamedTuple):
    """One path item, and the operation spliced into it under `method`."""

    item: dict[str, Any]
    method: str
    operation: dict[str, Any]


SCHEMA_KEYWORDS = ("additionalProperties", "items", "not", "propertyNames", "contains", "if", "then", "else")
SCHEMA_MAP_KEYWORDS = ("properties", "patternProperties", "$defs", "definitions", "dependentSchemas")
SCHEMA_LIST_KEYWORDS = ("allOf", "anyOf", "oneOf", "prefixItems")
# How far past its required keys a drawn object may run, and how big arrays and free strings get.
KEY_SLACK = 3
MAX_ITEMS = 2
MAX_LENGTH = 12
# The document also carries `paths`, `webhooks` and its definition container.
ROOT_KEY_SLACK = 8
MAX_PATH_ITEMS = 2
MAX_WEBHOOKS = 1


def _bounded(node: JsonSchema, *, root: bool = False) -> JsonSchema:
    """The meta-schema with every object, array and free string capped."""
    # One filter rejection anywhere kills the whole example, so smaller documents mean more of them.
    if not isinstance(node, dict):
        return node
    result = dict(node)
    for keyword in SCHEMA_KEYWORDS:
        if isinstance(result.get(keyword), dict):
            result[keyword] = _bounded(result[keyword])
    for keyword in SCHEMA_MAP_KEYWORDS:
        if isinstance(result.get(keyword), dict):
            result[keyword] = {name: _bounded(entry) for name, entry in result[keyword].items()}
    for keyword in SCHEMA_LIST_KEYWORDS:
        if isinstance(result.get(keyword), list):
            result[keyword] = [_bounded(entry, root=root) for entry in result[keyword]]
    types = result.get("type")
    types = {types} if isinstance(types, str) else set(types or ())
    if ("object" in types or {"properties", "patternProperties"} & result.keys()) and not root:
        result.setdefault("maxProperties", len(result.get("required", ())) + KEY_SLACK)
    if "array" in types or "items" in result:
        result.setdefault("maxItems", MAX_ITEMS)
    if types == {"string"} and not {"pattern", "format", "enum", "const"} & result.keys():
        result.setdefault("maxLength", MAX_LENGTH)
    return result


@lru_cache
def _metaschema(spec: Spec) -> JsonSchemaObject:
    bounded = _bounded(spec.schema)
    assert isinstance(bounded, dict)
    return bounded


@lru_cache
def _formats() -> dict[str, SearchStrategy]:
    return {**get_default_format_strategies(), "regex": REGEX_FORMAT}


def _built(spec: Spec, schema: JsonSchema) -> SearchStrategy[JsonValue]:
    return build(schema, draft=spec.draft, formats=_formats())


def _as_object(value: JsonValue) -> dict[str, Any]:
    """A drawn definition, checked rather than cast."""
    assert isinstance(value, dict), value
    return value


@lru_cache
def _definition(spec: Spec, name: str, location: str | None = None) -> SearchStrategy[dict[str, Any]]:
    # `type` is left off several Swagger 2.0 definitions, so an unpinned draw comes back as a number.
    pinned: JsonSchemaObject = {"type": "object"}
    if location is not None:
        pinned["properties"] = {"in": {"const": location}}
        pinned["required"] = list(spec.parameter_required)
    body = {
        "allOf": [{"$ref": f"#/{spec.container}/{name}"}, pinned],
        spec.container: _metaschema(spec)[spec.container],
    }
    return _built(spec, body).map(_as_object)


@lru_cache
def _skeleton(spec: Spec) -> SearchStrategy[dict[str, Any]]:
    # `paths`, `webhooks` and the container are replaced wholesale, so drawing their contents is waste.
    pinned = {"paths": EMPTY, "webhooks": EMPTY, spec.container: EMPTY, "components": EMPTY}
    overlay = {
        "properties": {key: value for key, value in pinned.items() if key in spec.schema["properties"]},
        "maxProperties": len(spec.schema.get("required", ())) + ROOT_KEY_SLACK,
    }
    return _built(spec, {"allOf": [_bounded(spec.schema, root=True), overlay]}).map(_as_object)


@lru_cache
def _entry(spec: Spec) -> SearchStrategy[Entry]:
    # Sampling leans on the first enum member, so an unpinned `in` never reaches `path` or `cookie`.
    parameters = st.tuples(
        *[st.none() | _definition(spec, name, location) for location, name in spec.parameters.items()]
    ).map(lambda drawn: [value for value in drawn if value is not None])
    return st.builds(
        Entry,
        _definition(spec, spec.path_item),
        st.sampled_from(spec.methods),
        st.builds(
            partial(_operation, spec=spec),
            _definition(spec, spec.operation),
            parameters,
            _definition(spec, spec.request_body),
        ),
    )


@lru_cache
def openapi_documents(version: str) -> SearchStrategy[dict[str, Any]]:
    """Documents drawn from the meta-schema of one Open API version."""
    # Whatever the spec declares is reachable, not only what a hand-written strategy remembered.
    spec = SPECS[version]
    entries = st.lists(_entry(spec), min_size=1, max_size=MAX_PATH_ITEMS)
    hooks = st.lists(_entry(spec), max_size=MAX_WEBHOOKS) if spec.has_webhooks else st.just([])
    media_types = st.lists(st.sampled_from(MEDIA_TYPES), min_size=1, max_size=2, unique=True)
    return st.builds(partial(_assemble, spec=spec), _skeleton(spec), entries, hooks, media_types)


def _operation(
    operation: dict[str, Any], parameters: list[dict[str, Any]], request_body: dict[str, Any], *, spec: Spec
) -> dict[str, Any]:
    # The meta-schema leaves all three optional, so they are almost never drawn together.
    result = dict(operation)
    named = [_named(parameter, f"p{index}") for index, parameter in enumerate(parameters)]
    if spec.body_as_parameter:
        # Swagger 2.0 writes a body as a parameter, and `formData` claims the same slot.
        if not any(parameter.get("in") == "formData" for parameter in named):
            named.append({**request_body, "in": "body", "name": "body", "required": True})
    else:
        result["requestBody"] = request_body
    result["parameters"] = named
    result.setdefault("responses", {"200": {"description": "OK"}})
    return result


def _named(parameter: dict[str, Any], name: str) -> dict[str, Any]:
    # A drawn name is arbitrary text, which no path template can carry and no header may be empty.
    result = {**parameter, "name": name}
    if result.get("in") == "path":
        result["required"] = True
    return result


def _assemble(
    document: dict[str, Any], entries: list[Entry], hooks: list[Entry], media_types: list[str], *, spec: Spec
) -> dict[str, Any]:
    result = {**document, "paths": _items(entries)}
    if hooks:
        result["webhooks"] = {f"h{index}": item for index, item in enumerate(_items(hooks).values())}
    result.update(spec.seed_document())
    for key in spec.media_type_keys:
        result[key] = list(media_types)
    return _rewritten(result, media_types, spec.seed_pointer)


def _items(entries: list[Entry]) -> dict[str, dict[str, Any]]:
    items = {}
    for index, entry in enumerate(entries):
        item = entry.item
        shared = item.get("parameters")
        if isinstance(shared, list):
            # A reference carries nothing else, so naming it would make the document invalid.
            item = {
                **item,
                "parameters": [
                    _named(parameter, f"i{position}") if "$ref" not in parameter else parameter
                    for position, parameter in enumerate(shared)
                    if isinstance(parameter, dict)
                ],
            }
        names = [
            parameter["name"]
            for source in (item.get("parameters"), entry.operation.get("parameters"))
            if isinstance(source, list)
            for parameter in source
            if isinstance(parameter, dict) and parameter.get("in") == "path"
        ]
        template = f"/p{index}" + "".join(f"/{{{name}}}" for name in names)
        items[template] = {**item, entry.method: entry.operation}
    return items


def _rewritten(document: dict[str, Any], media_types: list[str], pointer: str) -> dict[str, Any]:
    """Every reference resolves, and every body is written in a media type that can be encoded."""

    def walk(value: JsonValue) -> JsonValue:
        if isinstance(value, dict):
            result: dict[str, JsonValue] = {key: walk(item) for key, item in value.items()}
            if isinstance(result.get("$ref"), str):
                result["$ref"] = pointer
            content = result.get("content")
            if isinstance(content, dict):
                entries = list(content.values()) or [{"schema": {"$ref": pointer}}]
                result["content"] = {
                    media_types[index % len(media_types)]: entry for index, entry in enumerate(entries)
                }
            return result
        if isinstance(value, list):
            return [walk(item) for item in value]
        return value

    return _as_object(walk(document))


def _is_rejection(error: Exception) -> bool:
    if isinstance(error, IGNORED_EXCEPTIONS):
        return True
    if not isinstance(error, InvalidSchema):
        return False
    # Parsing reports an `AttributeError` as an invalid schema, hiding a value the code misread.
    return not isinstance(error.__cause__, AttributeError)


@pytest.mark.parametrize("version", sorted(SPECS))
@given(data=st.data())
@settings(phases=[Phase.generate], deadline=None, suppress_health_check=list(HealthCheck))
@pytest.mark.usefixtures("mocked_call")
def test_random_schemas(version, data):
    raw = data.draw(openapi_documents(version))
    assert SPECS[version].validator.is_valid(raw), raw
    schema = schemathesis.openapi.from_dict(raw, config=config)
    for event in schemathesis.engine.from_schema(schema).execute():
        assert not isinstance(event, events.FatalError), repr(event)
        if isinstance(event, events.NonFatalError) and not _is_rejection(event.value):
            raise AssertionError(str(event.info)) from event.value
