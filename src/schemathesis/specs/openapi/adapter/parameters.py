from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from itertools import chain
from typing import TYPE_CHECKING, Any, cast

from schemathesis.config import GenerationConfig
from schemathesis.core import NOT_SET, NotSet
from schemathesis.core.adapter import OperationParameter
from schemathesis.core.errors import InvalidSchema
from schemathesis.core.jsonschema import BundleError, Bundler
from schemathesis.core.jsonschema.bundler import BUNDLE_STORAGE_KEY, BundleCache
from schemathesis.core.jsonschema.types import JsonSchema, JsonSchemaObject
from schemathesis.core.parameters import HEADER_LOCATIONS, ParameterLocation
from schemathesis.core.transforms import deepclone
from schemathesis.core.validation import check_header_name
from schemathesis.generation.modes import GenerationMode
from schemathesis.resources import ExtraDataSource
from schemathesis.schemas import APIOperation, ParameterSet
from schemathesis.specs.openapi.adapter.protocol import SpecificationAdapter
from schemathesis.specs.openapi.adapter.references import maybe_resolve
from schemathesis.specs.openapi.converter import to_json_schema
from schemathesis.specs.openapi.formats import HEADER_FORMAT

if TYPE_CHECKING:
    from hypothesis import strategies as st

    from schemathesis.core.compat import RefResolver


MISSING_SCHEMA_OR_CONTENT_MESSAGE = (
    "Can not generate data for {location} parameter `{name}`! "
    "It should have either `schema` or `content` keywords defined"
)

INVALID_SCHEMA_MESSAGE = (
    "Can not generate data for {location} parameter `{name}`! Its schema should be an object or boolean, got {schema}"
)

FORM_MEDIA_TYPES = frozenset(["multipart/form-data", "application/x-www-form-urlencoded"])


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
        "_examples",
    )

    def __post_init__(self) -> None:
        self._optimized_schema: JsonSchema | NotSet = NOT_SET
        self._unoptimized_schema: JsonSchema | NotSet = NOT_SET
        self._raw_schema: JsonSchema | NotSet = NOT_SET
        self._examples: list | NotSet = NOT_SET

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

    def _extract_examples(self) -> list[object]:
        """Extract examples from both single example and examples container."""
        examples: list[object] = []

        container = self.definition.get(self.adapter.examples_container_keyword)
        if isinstance(container, dict):
            examples.extend(ex["value"] for ex in container.values() if isinstance(ex, dict) and "value" in ex)
        elif isinstance(container, list):
            examples.extend(container)

        example = self.definition.get(self.adapter.example_keyword, NOT_SET)
        if example is not NOT_SET:
            examples.append(example)

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
        try:
            return ParameterLocation(self.definition["in"])
        except ValueError:
            return ParameterLocation.UNKNOWN

    def _get_raw_schema(self) -> JsonSchema:
        """Get raw parameter schema."""
        return self.adapter.extract_parameter_schema(self.definition)

    def _get_default_type(self) -> str | None:
        """Return default type if parameter is in string-type location."""
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
        "_examples",
        "_positive_strategy_cache",
        "_negative_strategy_cache",
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
        self._positive_strategy_cache: st.SearchStrategy | NotSet = NOT_SET
        self._negative_strategy_cache: st.SearchStrategy | NotSet = NOT_SET

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

    def get_strategy(
        self,
        operation: APIOperation,
        generation_config: GenerationConfig,
        generation_mode: GenerationMode,
        extra_data_source: ExtraDataSource | None = None,
    ) -> st.SearchStrategy:
        """Get a Hypothesis strategy for this body parameter."""
        use_cache = extra_data_source is None

        # Check cache based on generation mode (only when extra data sources are not used)
        if use_cache:
            if generation_mode == GenerationMode.POSITIVE:
                if self._positive_strategy_cache is not NOT_SET:
                    assert not isinstance(self._positive_strategy_cache, NotSet)
                    return self._positive_strategy_cache
            elif self._negative_strategy_cache is not NOT_SET:
                assert not isinstance(self._negative_strategy_cache, NotSet)
                return self._negative_strategy_cache

        # Import here to avoid circular dependency
        from schemathesis.specs.openapi._hypothesis import GENERATOR_MODE_TO_STRATEGY_FACTORY
        from schemathesis.specs.openapi.schemas import OpenApiSchema

        # Build the strategy
        strategy_factory = GENERATOR_MODE_TO_STRATEGY_FACTORY[generation_mode]
        schema = self.optimized_schema
        if extra_data_source is not None:
            schema = extra_data_source.augment(operation=operation, location=ParameterLocation.BODY, schema=schema)
        assert isinstance(operation.schema, OpenApiSchema)
        strategy = strategy_factory(
            schema,
            operation.label,
            ParameterLocation.BODY,
            self.media_type,
            generation_config,
            operation.schema.adapter.jsonschema_validator_cls,
        )

        # Cache the strategy
        if use_cache:
            if generation_mode == GenerationMode.POSITIVE:
                self._positive_strategy_cache = strategy
            else:
                self._negative_strategy_cache = strategy

        return strategy


OPENAPI_20_EXCLUDE_KEYS = frozenset(["required", "name", "in", "title", "description"])


def extract_parameter_schema_v2(parameter: Mapping[str, Any]) -> JsonSchemaObject:
    # In Open API 2.0, schema for non-body parameters lives directly in the parameter definition
    return {key: value for key, value in parameter.items() if key not in OPENAPI_20_EXCLUDE_KEYS}


def extract_parameter_schema_v3(parameter: Mapping[str, Any]) -> JsonSchema:
    if "schema" in parameter:
        if not isinstance(parameter["schema"], (dict, bool)):
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
    resolver: RefResolver,
    bundler: Bundler,
    bundle_cache: dict[int, tuple[dict[str, Any], dict[str, str]]],
) -> tuple[dict[str, Any], dict[str, str]]:
    """Bundle a parameter definition to make it self-contained."""
    param_id = id(parameter)
    if param_id in bundle_cache:
        cached_definition, cached_name_to_uri = bundle_cache[param_id]
        return deepclone(cached_definition), dict(cached_name_to_uri)

    scope, definition = maybe_resolve(parameter, resolver, "")
    schema = definition.get("schema")
    name_to_uri = {}
    if schema is not None:
        definition = {k: v for k, v in definition.items() if k != "schema"}
        # Push the resolved scope so nested $refs are resolved relative to the parameter's location
        resolver.push_scope(scope)
        try:
            bundled = bundler.bundle(schema, resolver, inline_recursive=True)
            definition["schema"] = bundled.schema
            name_to_uri = bundled.name_to_uri
        except BundleError as exc:
            location = parameter.get("in", "")
            name = parameter.get("name", "<UNKNOWN>")
            raise InvalidSchema.from_bundle_error(exc, location, name) from exc
        finally:
            resolver.pop_scope()

    definition_ = cast(dict, definition)
    result = definition_, name_to_uri
    bundle_cache[param_id] = (deepclone(definition_), dict(name_to_uri))
    return result


OPENAPI_20_DEFAULT_BODY_MEDIA_TYPE = "application/json"
OPENAPI_20_DEFAULT_FORM_MEDIA_TYPE = "multipart/form-data"


def iter_parameters_v2(
    definition: Mapping[str, Any],
    shared_parameters: Sequence[Mapping[str, Any]],
    default_media_types: list[str],
    resolver: RefResolver,
    adapter: SpecificationAdapter,
    bundler: Bundler,
    bundle_cache: BundleCache,
) -> Iterator[OperationParameter]:
    media_types = definition.get("consumes", default_media_types)
    # For `in=body` parameters, we imply `application/json` as the default media type because it is the most common.
    body_media_types = media_types or (OPENAPI_20_DEFAULT_BODY_MEDIA_TYPE,)
    # If an API operation has parameters with `in=formData`, Schemathesis should know how to serialize it.
    # We can't be 100% sure what media type is expected by the server and chose `multipart/form-data` as
    # the default because it is broader since it allows us to upload files.
    form_data_media_types = media_types or (OPENAPI_20_DEFAULT_FORM_MEDIA_TYPE,)

    form_parameters = []
    form_name_to_uri = {}
    for parameter in chain(definition.get("parameters", []), shared_parameters):
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
            for param in chain(definition.get("parameters", []), shared_parameters):
                _, param = maybe_resolve(param, resolver, "")
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
        for media_type in form_data_media_types:
            # Individual `formData` parameters are joined into a single "composite" one.
            yield OpenApiBody.from_form_parameters(
                definition=form_data, media_type=media_type, name_to_uri=form_name_to_uri, adapter=adapter
            )


def iter_parameters_v3(
    definition: Mapping[str, Any],
    shared_parameters: Sequence[Mapping[str, Any]],
    default_media_types: list[str],
    resolver: RefResolver,
    adapter: SpecificationAdapter,
    bundler: Bundler,
    bundle_cache: BundleCache,
) -> Iterator[OperationParameter]:
    # Open API 3.0 has the `requestBody` keyword, which may contain multiple different payload variants.
    # TODO: Typing
    operation = definition

    for parameter in chain(definition.get("parameters", []), shared_parameters):
        parameter, name_to_uri = _bundle_parameter(parameter, resolver, bundler, bundle_cache)
        location = parameter.get("in")
        if not isinstance(location, str):
            continue
        if location in HEADER_LOCATIONS:
            check_header_name(parameter["name"])

        yield OpenApiParameter.from_definition(definition=parameter, name_to_uri=name_to_uri, adapter=adapter)

    request_body_or_ref = operation.get("requestBody")
    if request_body_or_ref is not None:
        scope, request_body_or_ref = maybe_resolve(request_body_or_ref, resolver, "")
        # It could be an object inside `requestBodies`, which could be a reference itself
        body_scope, request_body = maybe_resolve(request_body_or_ref, resolver, scope)

        required = request_body.get("required", False)
        for media_type, content in request_body["content"].items():
            resource_name = None
            schema = content.get("schema")
            name_to_uri = {}
            if isinstance(schema, dict):
                content = dict(content)
                if "$ref" in schema:
                    resource_name = resource_name_from_ref(schema["$ref"])
                # Push the resolved scope so nested $refs are resolved relative to the requestBody's location
                resolver.push_scope(body_scope)
                try:
                    to_bundle = cast(dict[str, Any], schema)
                    bundled = bundler.bundle(to_bundle, resolver, inline_recursive=True)
                    content["schema"] = bundled.schema
                    name_to_uri = bundled.name_to_uri
                except BundleError as exc:
                    raise InvalidSchema.from_bundle_error(exc, "body") from exc
                finally:
                    resolver.pop_scope()
            yield OpenApiBody.from_definition(
                definition=content,
                is_required=required,
                media_type=media_type,
                resource_name=resource_name,
                name_to_uri=name_to_uri,
                adapter=adapter,
            )


def resource_name_from_ref(reference: str) -> str:
    return reference.rsplit("/", maxsplit=1)[1]


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

    __slots__ = ("items", "location", "_schema", "_schema_cache", "_strategy_cache")

    def __init__(self, location: ParameterLocation, items: list[OpenApiParameter] | None = None) -> None:
        self.location = location
        self.items = items or []
        self._schema: dict | NotSet = NOT_SET
        self._schema_cache: dict[frozenset[str], dict[str, Any]] = {}
        self._strategy_cache: dict[tuple[frozenset[str], GenerationMode], st.SearchStrategy] = {}

    @property
    def schema(self) -> dict[str, Any]:
        if self._schema is NOT_SET:
            self._schema = parameters_to_json_schema(self.items, self.location)
        assert not isinstance(self._schema, NotSet)
        return self._schema

    def get_schema_with_exclusions(self, exclude: Iterable[str]) -> dict[str, Any]:
        """Get cached schema with specified parameters excluded."""
        exclude_key = frozenset(exclude)

        if exclude_key in self._schema_cache:
            return self._schema_cache[exclude_key]

        schema = self.schema
        if exclude_key:
            # Need to exclude some parameters - create a shallow copy to avoid mutating cached schema
            schema = dict(schema)
            if self.location == ParameterLocation.HEADER:
                # Remove excluded headers case-insensitively
                exclude_lower = {name.lower() for name in exclude_key}
                schema["properties"] = {
                    key: value for key, value in schema["properties"].items() if key.lower() not in exclude_lower
                }
                if "required" in schema:
                    schema["required"] = [key for key in schema["required"] if key.lower() not in exclude_lower]
            else:
                # Non-header locations: remove by exact name
                schema["properties"] = {
                    key: value for key, value in schema["properties"].items() if key not in exclude_key
                }
                if "required" in schema:
                    schema["required"] = [key for key in schema["required"] if key not in exclude_key]

        self._schema_cache[exclude_key] = schema
        return schema

    def get_strategy(
        self,
        operation: APIOperation,
        generation_config: GenerationConfig,
        generation_mode: GenerationMode,
        exclude: Iterable[str] = (),
        extra_data_source: ExtraDataSource | None = None,
    ) -> st.SearchStrategy:
        """Get a Hypothesis strategy for this parameter set with specified exclusions."""
        exclude_key = frozenset(exclude)
        cache_key = (exclude_key, generation_mode)

        use_cache = extra_data_source is None

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
            quote_all,
        )
        from schemathesis.specs.openapi.negative import GeneratedValue
        from schemathesis.specs.openapi.schemas import OpenApiSchema

        # Get schema with exclusions
        schema: JsonSchema = self.get_schema_with_exclusions(exclude)
        if extra_data_source is not None:
            schema = extra_data_source.augment(operation=operation, location=self.location, schema=schema)

        # `JsonSchema` can be boolean (`True` / `False`), normalize to an object schema for downstream usage.
        if isinstance(schema, bool):
            schema = {} if schema else {"not": {}}
        assert isinstance(schema, dict)
        schema_obj: JsonSchemaObject = schema

        strategy_factory = GENERATOR_MODE_TO_STRATEGY_FACTORY[generation_mode]

        if not schema_obj["properties"] and strategy_factory is make_negative_strategy:
            # Nothing to negate - all properties were excluded
            strategy = st.none()
        else:
            assert isinstance(operation.schema, OpenApiSchema)
            strategy = strategy_factory(
                schema_obj,
                operation.label,
                self.location,
                None,
                generation_config,
                operation.schema.adapter.jsonschema_validator_cls,
            )

            # For negative strategies, we need to handle GeneratedValue wrappers
            is_negative = strategy_factory is make_negative_strategy

            serialize = operation.get_parameter_serializer(self.location)
            if serialize is not None:
                if is_negative:
                    # Apply serialize only to the value part of GeneratedValue
                    strategy = strategy.map(lambda x: GeneratedValue(serialize(x.value), x.meta))
                else:
                    strategy = strategy.map(serialize)

            filter_func = {
                ParameterLocation.PATH: is_valid_path,
                ParameterLocation.HEADER: is_valid_header,
                ParameterLocation.COOKIE: is_valid_header,
                ParameterLocation.QUERY: is_valid_query,
            }[self.location]
            # Headers with special format do not need filtration
            if not (self.location.is_in_header and _can_skip_header_filter(schema)):
                if is_negative:
                    # Apply filter only to the value part of GeneratedValue
                    strategy = strategy.filter(lambda x: filter_func(x.value))
                else:
                    strategy = strategy.filter(filter_func)

            # Path & query parameters will be cast to string anyway, but having their JSON equivalents for
            # `True` / `False` / `None` improves chances of them passing validation in apps
            # that expect boolean / null types
            # and not aware of Python-specific representation of those types
            if self.location == ParameterLocation.PATH:
                if is_negative:
                    strategy = strategy.map(
                        lambda x: GeneratedValue(quote_all(jsonify_python_specific_types(x.value)), x.meta)
                    )
                else:
                    strategy = strategy.map(quote_all).map(jsonify_python_specific_types)
            elif self.location == ParameterLocation.QUERY:
                if is_negative:
                    strategy = strategy.map(lambda x: GeneratedValue(jsonify_python_specific_types(x.value), x.meta))
                else:
                    strategy = strategy.map(jsonify_python_specific_types)

        if use_cache:
            self._strategy_cache[cache_key] = strategy
        return strategy


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


def _merge_parameters_to_object_schema(
    parameters: Iterable[tuple[str, Any, bool]], location: ParameterLocation
) -> dict[str, Any]:
    """Merge parameter data into a JSON Schema object."""
    properties = {}
    required = []
    bundled = {}

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
            # Headers: add HEADER_FORMAT for plain string types
            if location.is_in_header and list(subschema) == ["type"] and subschema["type"] == "string":
                subschema = {**subschema, "format": HEADER_FORMAT}

            # Path parameters: ensure string types have minLength >= 1
            elif location == ParameterLocation.PATH and subschema.get("type") == "string":
                if "minLength" not in subschema:
                    subschema = {**subschema, "minLength": 1}

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
