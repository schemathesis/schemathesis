from __future__ import annotations
from dataclasses import dataclass
from typing import Any, ClassVar, Iterable, Mapping

from ...exceptions import OperationSchemaError
from ...models import APIOperation
from ...parameters import Parameter


@dataclass(eq=False)
class OpenAPIParameter(Parameter):
    """A single Open API operation parameter."""

    required: bool
    location: Literal["query", "header", "path", "cookie", "body"]
    # JSON Schema to generate this parameter
    schema: dict[str, Any]

    example_field: ClassVar[str]
    examples_field: ClassVar[str]
    nullable_field: ClassVar[str]
    supported_jsonschema_keywords: ClassVar[tuple[str, ...]]

    @classmethod
    def clean_schema(cls, schema: Mapping[str, Any]) -> dict[str, Any]:
        return {
            key: value
            for key, value in schema.items()
            # Allow only supported keywords or vendor extensions
            if key in cls.supported_jsonschema_keywords or key.startswith("x-") or key == cls.nullable_field
        }


@dataclass(eq=False)
class OpenAPI20Parameter(OpenAPIParameter):
    """Open API 2.0 parameter.

    https://github.com/OAI/OpenAPI-Specification/blob/master/versions/2.0.md#parameterObject
    """

    example_field = "x-example"
    examples_field = "x-examples"
    nullable_field = "x-nullable"
    # https://github.com/OAI/OpenAPI-Specification/blob/master/versions/2.0.md#parameterObject
    # Excluding informative keywords - `title`, `description`, `default`.
    # `required` is not included because it has a different meaning here. It determines whether or not this parameter
    # is required, which is not relevant because these parameters are later constructed
    # into an "object" schema, and the value of this keyword is used there.
    # The following keywords are relevant only for non-body parameters.
    supported_jsonschema_keywords: ClassVar[tuple[str, ...]] = (
        "$ref",
        "type",  # only as a string
        "format",
        "items",
        "maximum",
        "exclusiveMaximum",
        "minimum",
        "exclusiveMinimum",
        "maxLength",
        "minLength",
        "pattern",
        "maxItems",
        "minItems",
        "uniqueItems",
        "enum",
        "multipleOf",
    )

    @property
    def is_header(self) -> bool:
        return self.location == "header"


@dataclass(eq=False)
class OpenAPI30Parameter(OpenAPIParameter):
    """Open API 3.0 parameter.

    https://github.com/OAI/OpenAPI-Specification/blob/master/versions/3.0.3.md#parameter-object
    """

    example_field = "example"
    examples_field = "examples"
    nullable_field = "nullable"
    # https://github.com/OAI/OpenAPI-Specification/blob/master/versions/3.0.3.md#schema-object
    # Excluding informative keywords - `title`, `description`, `default`.
    # In contrast with Open API 2.0 non-body parameters, in Open API 3.0, all parameters have the `schema` keyword.
    supported_jsonschema_keywords = (
        "$ref",
        "multipleOf",
        "maximum",
        "exclusiveMaximum",
        "minimum",
        "exclusiveMinimum",
        "maxLength",
        "minLength",
        "pattern",
        "maxItems",
        "minItems",
        "uniqueItems",
        "maxProperties",
        "minProperties",
        "required",
        "enum",
        "type",
        "allOf",
        "oneOf",
        "anyOf",
        "not",
        "items",
        "properties",
        "additionalProperties",
        "format",
    )

    @property
    def is_header(self) -> bool:
        return self.location in ("header", "cookie")


@dataclass(eq=False)
class OpenAPIBody(OpenAPIParameter):
    media_type: str


@dataclass(eq=False)
class OpenAPI20Body(OpenAPIBody, OpenAPI20Parameter):
    """Open API 2.0 body variant."""

    # https://github.com/OAI/OpenAPI-Specification/blob/master/versions/2.0.md#schemaObject
    # The `body` parameter contains the `schema` keyword that represents the `Schema Object`.
    # It has slightly different keywords than other parameters. Informational keywords are excluded as well.
    supported_jsonschema_keywords = (
        "$ref",
        "format",
        "multipleOf",
        "maximum",
        "exclusiveMaximum",
        "minimum",
        "exclusiveMinimum",
        "maxLength",
        "minLength",
        "pattern",
        "maxItems",
        "minItems",
        "uniqueItems",
        "maxProperties",
        "minProperties",
        "enum",
        "type",
        "items",
        "allOf",
        "properties",
        "additionalProperties",
    )
    # NOTE. For Open API 2.0 bodies, we still give `x-example` precedence over the schema-level `example` field to keep
    # the precedence rules consistent.


FORM_MEDIA_TYPES = ("multipart/form-data", "application/x-www-form-urlencoded")


@dataclass(eq=False)
class OpenAPI30Body(OpenAPIBody, OpenAPI30Parameter):
    """Open API 3.0 body variant.

    We consider each media type defined in the schema as a separate variant that can be chosen for data generation.
    The value of the `definition` field is essentially the Open API 3.0 `MediaType`.
    """


@dataclass(eq=False)
class OpenAPI20CompositeBody(OpenAPIBody, OpenAPI20Parameter):
    """A special container to abstract over multiple `formData` parameters."""


def parameters_to_json_schema(parameters: Iterable[OpenAPIParameter]) -> dict[str, Any]:
    """Create an "object" JSON schema from a list of Open API parameters.

    :param List[OpenAPIParameter] parameters: A list of Open API parameters related to the same location. All of
        them are expected to have the same "in" value.

    For each input parameter, there will be a property in the output schema.

    This:

        [
            {
                "in": "query",
                "name": "id",
                "type": "string",
                "required": True
            }
        ]

    Will become:

        {
            "properties": {
                "id": {"type": "string"}
            },
            "additionalProperties": False,
            "type": "object",
            "required": ["id"]
        }

    We need this transformation for locations that imply multiple components with a unique name within
    the same location.

    For example, "query" - first, we generate an object that contains all defined parameters and then serialize it
    to the proper format.
    """
    properties = {}
    required = []
    for parameter in parameters:
        name = parameter.name
        properties[name] = parameter.schema
        # If parameter names are duplicated, we need to avoid duplicate entries in `required` anyway
        if parameter.required and name not in required:
            required.append(name)
    return {"properties": properties, "additionalProperties": False, "type": "object", "required": required}


MISSING_SCHEMA_OR_CONTENT_MESSAGE = (
    'Can not generate data for {location} parameter "{name}"! '
    "It should have either `schema` or `content` keywords defined"
)

INVALID_SCHEMA_MESSAGE = (
    'Can not generate data for {location} parameter "{name}"! ' "Its schema should be an object, got {schema}"
)


def get_parameter_schema(operation: APIOperation, data: dict[str, Any]) -> dict[str, Any]:
    """Extract `schema` from Open API 3.0 `Parameter`."""
    # In Open API 3.0, there could be "schema" or "content" field. They are mutually exclusive.
    if "schema" in data:
        schema = data["schema"]
        resolver = operation.definition.resolver
        while "$ref" in schema:
            resolved = resolver.lookup(schema["$ref"])
            resolver = resolved.resolver
            schema = resolved.contents
            if not isinstance(schema, dict):
                raise OperationSchemaError(
                    INVALID_SCHEMA_MESSAGE.format(
                        location=data.get("in", ""), name=data.get("name", "<UNKNOWN>"), schema=schema
                    ),
                    path=operation.path,
                    method=operation.method,
                    full_path=operation.full_path,
                )
        return schema
    # https://github.com/OAI/OpenAPI-Specification/blob/master/versions/3.0.3.md#fixed-fields-10
    # > The map MUST only contain one entry.
    try:
        content = data["content"]
    except KeyError as exc:
        raise OperationSchemaError(
            MISSING_SCHEMA_OR_CONTENT_MESSAGE.format(location=data.get("in", ""), name=data.get("name", "<UNKNOWN>")),
            path=operation.path,
            method=operation.method,
            full_path=operation.full_path,
        ) from exc
    options = iter(content.values())
    media_type_object = next(options)
    return get_media_type_schema(media_type_object)


def get_media_type_schema(definition: dict[str, Any]) -> dict[str, Any]:
    """Extract `schema` from Open API 3.0 `MediaType`."""
    # The `schema` keyword is optional, and we treat it as the payload could be any value of the specified media type
    # Note, the main reason to have this function is to have an explicit name for the action we're doing.
    return definition.get("schema", {})
