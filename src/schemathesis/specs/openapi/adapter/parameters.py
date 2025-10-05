from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from itertools import chain
from typing import TYPE_CHECKING, Any, Iterable, Iterator, Mapping, Sequence, cast

from schemathesis.core import NOT_SET, NotSet
from schemathesis.core.adapter import OperationParameter
from schemathesis.core.errors import InvalidSchema
from schemathesis.core.jsonschema import BundleError, Bundler
from schemathesis.core.jsonschema.bundler import BUNDLE_STORAGE_KEY
from schemathesis.core.jsonschema.types import JsonSchema, JsonSchemaObject
from schemathesis.core.parameters import HEADER_LOCATIONS, ParameterLocation
from schemathesis.core.validation import check_header_name
from schemathesis.schemas import ParameterSet
from schemathesis.specs.openapi.adapter.protocol import SpecificationAdapter
from schemathesis.specs.openapi.adapter.references import maybe_resolve
from schemathesis.specs.openapi.converter import to_json_schema

if TYPE_CHECKING:
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
    adapter: SpecificationAdapter

    __slots__ = (
        "definition",
        "is_required",
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
    def from_definition(cls, *, definition: Mapping[str, Any], adapter: SpecificationAdapter) -> OpenApiParameter:
        is_required = definition.get("required", False)
        return cls(definition=definition, is_required=is_required, adapter=adapter)

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

    __slots__ = (
        "definition",
        "is_required",
        "media_type",
        "resource_name",
        "adapter",
        "_optimized_schema",
        "_unoptimized_schema",
        "_raw_schema",
        "_examples",
    )

    @classmethod
    def from_definition(
        cls,
        *,
        definition: Mapping[str, Any],
        is_required: bool,
        media_type: str,
        resource_name: str | None,
        adapter: SpecificationAdapter,
    ) -> OpenApiBody:
        return cls(
            definition=definition,
            is_required=is_required,
            media_type=media_type,
            resource_name=resource_name,
            adapter=adapter,
        )

    @classmethod
    def from_form_parameters(
        cls,
        *,
        definition: Mapping[str, Any],
        media_type: str,
        adapter: SpecificationAdapter,
    ) -> OpenApiBody:
        return cls(
            definition=definition,
            is_required=True,
            media_type=media_type,
            resource_name=None,
            adapter=adapter,
        )

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


def _bundle_parameter(parameter: Mapping, resolver: RefResolver, bundler: Bundler) -> dict:
    """Bundle a parameter definition to make it self-contained."""
    _, definition = maybe_resolve(parameter, resolver, "")
    schema = definition.get("schema")
    if schema is not None:
        definition = {k: v for k, v in definition.items() if k != "schema"}
        try:
            definition["schema"] = bundler.bundle(schema, resolver, inline_recursive=True)
        except BundleError as exc:
            location = parameter.get("in", "")
            name = parameter.get("name", "<UNKNOWN>")
            raise InvalidSchema.from_bundle_error(exc, location, name) from exc
    return cast(dict, definition)


OPENAPI_20_DEFAULT_BODY_MEDIA_TYPE = "application/json"
OPENAPI_20_DEFAULT_FORM_MEDIA_TYPE = "multipart/form-data"


def iter_parameters_v2(
    definition: Mapping[str, Any],
    shared_parameters: Sequence[Mapping[str, Any]],
    default_media_types: list[str],
    resolver: RefResolver,
    adapter: SpecificationAdapter,
) -> Iterator[OperationParameter]:
    media_types = definition.get("consumes", default_media_types)
    # For `in=body` parameters, we imply `application/json` as the default media type because it is the most common.
    body_media_types = media_types or (OPENAPI_20_DEFAULT_BODY_MEDIA_TYPE,)
    # If an API operation has parameters with `in=formData`, Schemathesis should know how to serialize it.
    # We can't be 100% sure what media type is expected by the server and chose `multipart/form-data` as
    # the default because it is broader since it allows us to upload files.
    form_data_media_types = media_types or (OPENAPI_20_DEFAULT_FORM_MEDIA_TYPE,)

    form_parameters = []
    bundler = Bundler()
    for parameter in chain(definition.get("parameters", []), shared_parameters):
        parameter = _bundle_parameter(parameter, resolver, bundler)
        if parameter["in"] in HEADER_LOCATIONS:
            check_header_name(parameter["name"])

        if parameter["in"] == "formData":
            # We need to gather form parameters first before creating a composite parameter for them
            form_parameters.append(parameter)
        elif parameter["in"] == ParameterLocation.BODY:
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
                    resource_name=resource_name,
                    adapter=adapter,
                )
        else:
            yield OpenApiParameter.from_definition(definition=parameter, adapter=adapter)

    if form_parameters:
        form_data = form_data_to_json_schema(form_parameters)
        for media_type in form_data_media_types:
            # Individual `formData` parameters are joined into a single "composite" one.
            yield OpenApiBody.from_form_parameters(definition=form_data, media_type=media_type, adapter=adapter)


def iter_parameters_v3(
    definition: Mapping[str, Any],
    shared_parameters: Sequence[Mapping[str, Any]],
    default_media_types: list[str],
    resolver: RefResolver,
    adapter: SpecificationAdapter,
) -> Iterator[OperationParameter]:
    # Open API 3.0 has the `requestBody` keyword, which may contain multiple different payload variants.
    # TODO: Typing
    operation = definition

    bundler = Bundler()
    for parameter in chain(definition.get("parameters", []), shared_parameters):
        parameter = _bundle_parameter(parameter, resolver, bundler)
        if parameter["in"] in HEADER_LOCATIONS:
            check_header_name(parameter["name"])

        yield OpenApiParameter.from_definition(definition=parameter, adapter=adapter)

    request_body_or_ref = operation.get("requestBody")
    if request_body_or_ref is not None:
        scope, request_body_or_ref = maybe_resolve(request_body_or_ref, resolver, "")
        # It could be an object inside `requestBodies`, which could be a reference itself
        _, request_body = maybe_resolve(request_body_or_ref, resolver, scope)

        required = request_body.get("required", False)
        for media_type, content in request_body["content"].items():
            resource_name = None
            schema = content.get("schema")
            if isinstance(schema, dict):
                content = dict(content)
                if "$ref" in schema:
                    resource_name = resource_name_from_ref(schema["$ref"])
                try:
                    to_bundle = cast(dict[str, Any], schema)
                    bundled = bundler.bundle(to_bundle, resolver, inline_recursive=True)
                    content["schema"] = bundled
                except BundleError as exc:
                    raise InvalidSchema.from_bundle_error(exc, "body") from exc
            yield OpenApiBody.from_definition(
                definition=content,
                is_required=required,
                media_type=media_type,
                resource_name=resource_name,
                adapter=adapter,
            )


def resource_name_from_ref(reference: str) -> str:
    return reference.rsplit("/", maxsplit=1)[1]


def build_path_parameter_v2(kwargs: Mapping[str, Any]) -> OpenApiParameter:
    from schemathesis.specs.openapi.adapter import v2

    return OpenApiParameter.from_definition(
        definition={"in": ParameterLocation.PATH.value, "required": True, "type": "string", "minLength": 1, **kwargs},
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
        adapter=v3_1,
    )


@dataclass
class OpenApiParameterSet(ParameterSet):
    items: list[OpenApiParameter]

    __slots__ = ("items", "_schema")

    def __init__(self, items: list[OpenApiParameter] | None = None) -> None:
        self.items = items or []
        self._schema: dict | NotSet = NOT_SET

    @property
    def schema(self) -> dict[str, Any]:
        if self._schema is NOT_SET:
            self._schema = parameters_to_json_schema(self.items)
        assert not isinstance(self._schema, NotSet)
        return self._schema


COMBINED_FORM_DATA_MARKER = "x-schemathesis-form-parameter"


def form_data_to_json_schema(parameters: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Convert raw form parameter definitions to a JSON Schema."""
    parameter_data = (
        (param["name"], extract_parameter_schema_v2(param), param.get("required", False)) for param in parameters
    )

    merged = _merge_parameters_to_object_schema(parameter_data)

    return {"schema": merged, COMBINED_FORM_DATA_MARKER: True}


def parameters_to_json_schema(parameters: Iterable[OpenApiParameter]) -> dict[str, Any]:
    """Convert multiple Open API parameters to a JSON Schema."""
    parameter_data = ((param.name, param.optimized_schema, param.is_required) for param in parameters)

    return _merge_parameters_to_object_schema(parameter_data)


def _merge_parameters_to_object_schema(parameters: Iterable[tuple[str, Any, bool]]) -> dict[str, Any]:
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

        properties[name] = subschema

        # Avoid duplicate entries in required
        if is_required and name not in required:
            required.append(name)

    merged = {
        "properties": properties,
        "additionalProperties": False,
        "type": "object",
        "required": required,
    }
    if bundled:
        merged[BUNDLE_STORAGE_KEY] = bundled

    return merged
