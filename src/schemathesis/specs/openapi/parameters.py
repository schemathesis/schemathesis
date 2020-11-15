from typing import Any, ClassVar, Dict, Generator, List, Tuple

import attr

from ...parameters import Example, Parameter
from .converter import to_json_schema_recursive


@attr.s(slots=True)
class OpenAPIParameter(Parameter):
    """A single Open API operation parameter."""

    example_field: ClassVar[str]
    examples_field: ClassVar[str]
    nullable_field: ClassVar[str]
    supported_jsonschema_keywords: ClassVar[Tuple[str, ...]]

    def iter_examples(self) -> Generator[Example, None, None]:
        """Iterate over all examples defined for the parameter."""
        if self.example:
            yield Example(None, self.example)
        elif self.named_examples:
            for name, value in self.named_examples.items():
                yield Example(name, value)
        elif self.schema_example:
            # It is processed only if there is no `example` / `examples` in the root, overridden otherwise
            # https://github.com/OAI/OpenAPI-Specification/blob/master/versions/3.0.3.md#fixed-fields-10
            yield Example(None, self.schema_example)

    @property
    def location(self) -> str:
        """Where this parameter is located.

        E.g. "query".
        """
        return {"formData": "body"}.get(self.raw_location, self.raw_location)

    @property
    def raw_location(self) -> str:
        """Open API specific location name."""
        return self.definition["in"]

    @property
    def name(self) -> str:
        """Parameter name."""
        return self.definition["name"]

    @property
    def is_required(self) -> bool:
        return self.definition.get("required", False)

    @property
    def is_header(self) -> bool:
        raise NotImplementedError

    @property
    def example(self) -> Any:
        """A not-named example, defined in the parameter root.

        {
            "in": "query",
            "name": "key",
            "type": "string"
            "example": "foo",   # This one
        }
        """
        return self.definition.get(self.example_field)

    @property
    def named_examples(self) -> Dict[str, Any]:
        """Named examples, defined in the parameter root."""
        return self.definition.get(self.examples_field, {})

    @property
    def schema_example(self) -> Any:
        """Example defined on the schema-level.

        Open API 3.0:

        {
            "in": "query",  (only "body" is possible for Open API 2.0)
            "name": "key",
            "schema": {
                "type": "string",
                "example": "foo",   # This one
            }
        }
        """
        return self.definition.get("schema", {}).get("example")

    def as_json_schema(self) -> Dict[str, Any]:
        """Convert parameter's definition to JSON Schema."""
        schema = self.from_open_api_to_json_schema(self.definition)
        return self.transform_keywords(schema)

    def transform_keywords(self, schema: Dict[str, Any]) -> Dict[str, Any]:
        """Transform Open API specific keywords into JSON Schema compatible form."""
        definition = to_json_schema_recursive(schema, self.nullable_field)
        # Headers are strings, but it is not always explicitly defined in the schema. By preparing them properly we
        # can achieve significant performance improvements for such cases.
        # For reference (my machine) - running a single test with 100 examples with the resulting strategy:
        #   - without: 4.37 s
        #   - with: 294 ms
        #
        # It also reduces the number of cases when the "filter_too_much" health check fails during testing.
        if self.is_header:
            definition.setdefault("type", "string")
        return definition

    def from_open_api_to_json_schema(self, open_api_schema: Dict[str, Any]) -> Dict[str, Any]:
        """Convert Open API's `Schema` to JSON Schema."""
        return {
            key: value
            for key, value in open_api_schema.items()
            # Allow only supported keywords or vendor extensions
            if key in self.supported_jsonschema_keywords or key.startswith("x-")
        }


class OpenAPI20Parameter(OpenAPIParameter):
    """Open API 2.0 parameter.

    https://github.com/OAI/OpenAPI-Specification/blob/master/versions/2.0.md#parameterObject
    """

    example_field = "x-example"
    examples_field = "x-examples"
    nullable_field = "x-nullable"
    # https://github.com/OAI/OpenAPI-Specification/blob/master/versions/2.0.md#schemaObject
    # Excluding informative keywords - `title`, `description`, `default`
    # And `required`, because it has a different meaning here. It determines whether ot not this parameter is required
    # or optional, which is not relevant because these parameters are later constructed into an "object" schema,
    # and the value of this keyword is used there.
    supported_jsonschema_keywords = (
        "$ref",
        "format",
        "multipleOf",
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

    @property
    def is_header(self) -> bool:
        return self.location == "header"


class OpenAPI30Parameter(OpenAPIParameter):
    """Open API 3.0 parameter.

    https://github.com/OAI/OpenAPI-Specification/blob/master/versions/3.0.3.md#parameter-object
    """

    example_field = "example"
    examples_field = "examples"
    nullable_field = "nullable"
    # https://github.com/OAI/OpenAPI-Specification/blob/master/versions/3.0.3.md#schema-object
    # Excluding informative keywords - `title`, `description`, `default`
    supported_jsonschema_keywords = (
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

    def from_open_api_to_json_schema(self, open_api_schema: Dict[str, Any]) -> Dict[str, Any]:
        open_api_schema = get_parameter_schema(open_api_schema)
        return super().from_open_api_to_json_schema(open_api_schema)


@attr.s(slots=True)
class OpenAPIBody(OpenAPIParameter):
    media_type: str = attr.ib()

    @property
    def location(self) -> str:
        return "body"

    @property
    def name(self) -> str:
        # The name doesn't matter, but is here for the interface completeness.
        return "body"


@attr.s(slots=True)
class OpenAPI20Body(OpenAPIBody, OpenAPI20Parameter):
    """Open API 2.0 body variant."""

    def as_json_schema(self) -> Dict[str, Any]:
        """Convert body definition to JSON Schema."""
        # `schema` is required in Open API 2.0 bodies.
        schema = self.definition["schema"]
        return self.transform_keywords(schema)


@attr.s(slots=True)
class OpenAPI30Body(OpenAPIBody, OpenAPI30Parameter):
    """Open API 3.0 body variant.

    We consider each media type defined in the schema as a separate variant, that can be chosen for generation.
    The value of the `definition` field is essentially the Open API 3.0 `MediaType`.
    """

    # `required` keyword is located above the schema for concrete media-type; therefore it is passed here explicitly
    required: bool = attr.ib(default=False)

    def as_json_schema(self) -> Dict[str, Any]:
        """Convert body definition to JSON Schema."""
        schema = get_media_type_schema(self.definition)
        return self.transform_keywords(schema)

    def transform_keywords(self, schema: Dict[str, Any]) -> Dict[str, Any]:
        definition = super().transform_keywords(schema)
        if self.is_form:
            definition.setdefault("type", "object")
        return definition

    @property
    def is_form(self) -> bool:
        """Whether this payload represent a form."""
        return self.media_type in ("multipart/form-data", "application/x-www-form-urlencoded")

    @property
    def is_required(self) -> bool:
        return self.required


@attr.s(slots=True)
class OpenAPI20CompositeBody(OpenAPIBody, OpenAPI20Parameter):
    @classmethod
    def from_parameters(cls, *parameters: Dict[str, Any], media_type: str) -> "OpenAPI20CompositeBody":
        return cls(
            definition=[OpenAPI20Parameter(parameter) for parameter in parameters],
            media_type=media_type,
        )

    def as_json_schema(self) -> Dict[str, Any]:
        """Composite body is transformed into an "object" JSON Schema."""
        return parameters_to_json_schema(self.definition)


def parameters_to_json_schema(parameters: List[OpenAPIParameter]) -> Dict[str, Any]:
    """Create an "object" JSON schema from a list of Open API parameters.

    :param List[OpenAPIParameter] parameters: A list of Open API parameters, related to the same location. All of
        them are expected to have the same "in" value.

    For each input parameter there will be a property in the output schema.

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

    We need this transformation for locations that imply multiple components with unique name within the same location.
    For example, "query" - first, we generate an object, that contains all defined parameters and then serialize it
    to the proper format.
    """
    properties = {}
    required = []
    for parameter in parameters:
        name = parameter.name
        properties[name] = parameter.as_json_schema()
        if parameter.is_required:
            required.append(name)
    return {"properties": properties, "additionalProperties": False, "type": "object", "required": required}


def get_parameter_schema(data: Dict[str, Any]) -> Dict[str, Any]:
    """Extract `schema` from Open API 3.0 `Parameter`."""
    # In Open API 3.0 there could be "schema" or "content" field. They are mutually exclusive.
    if "schema" in data:
        return data["schema"]
    # https://github.com/OAI/OpenAPI-Specification/blob/master/versions/3.0.3.md#fixed-fields-10
    # > The map MUST only contain one entry.
    options = iter(data["content"].values())
    media_type_object = next(options)
    return get_media_type_schema(media_type_object)


def get_media_type_schema(definition: Dict[str, Any]) -> Dict[str, Any]:
    """Extract `schema` from Open API 3.0 `MediaType`."""
    # The `schema` keyword is optional and we treat it as the payload could be any value of the specified media type
    # Note, the main reason to have this function is to have an explicit name for the action we're doing.
    return definition.get("schema", {})
