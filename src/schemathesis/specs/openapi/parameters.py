from typing import Any, Dict, Generator

import attr

from ...parameters import Example, Parameter
from .converter import to_json_schema


@attr.s(slots=True)
class OpenAPIParameter(Parameter):
    """A single operation parameter.

    Open API 2.0: https://github.com/OAI/OpenAPI-Specification/blob/master/versions/2.0.md#operationObject
    Open API 3.0: https://github.com/OAI/OpenAPI-Specification/blob/master/versions/3.0.3.md#parameter-object
    """

    example_field: str
    examples_field: str

    def prepare_schema(self, schema: Dict[str, Any]) -> Dict[str, Any]:
        """Return an updated underlying parameter definition.

        It might be needed to improve data generation performance.
        """
        raise NotImplementedError

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
        schema = self._as_json_schema(self.definition)
        return self.prepare_schema(schema)

    def _as_json_schema(self, definition: Dict[str, Any]) -> Dict[str, Any]:
        return {
            key: value
            for key, value in definition.items()
            # Do not include keys not supported by JSON schema
            if not (key == "required" and not isinstance(value, list))
        }


class OpenAPI20Parameter(OpenAPIParameter):
    example_field = "x-example"
    examples_field = "x-examples"

    def prepare_schema(self, schema: Dict[str, Any]) -> Dict[str, Any]:
        definition = to_json_schema(schema, "x-nullable")
        if self.location == "header":
            definition.setdefault("type", "string")
            prepare_headers_schema(definition)
        if self.media_type in ("multipart/form-data", "application/x-www-form-urlencoded"):
            definition.setdefault("type", "object")
        return definition

    def _as_json_schema(self, definition: Dict[str, Any]) -> Dict[str, Any]:
        if self.raw_location == "body":
            definition = get_schema_from_parameter(definition)
        return super()._as_json_schema(definition)


class OpenAPI30Parameter(OpenAPIParameter):
    example_field = "example"
    examples_field = "examples"

    def prepare_schema(self, schema: Dict[str, Any]) -> Dict[str, Any]:
        definition = to_json_schema(schema, "nullable")
        if self.media_type in ("multipart/form-data", "application/x-www-form-urlencoded"):
            definition.setdefault("type", "object")
        if self.location in ("header", "cookie"):
            definition.setdefault("type", "string")
        return definition

    def _as_json_schema(self, definition: Dict[str, Any]) -> Dict[str, Any]:
        definition = get_schema_from_parameter(definition)
        return super()._as_json_schema(definition)


class OpenAPI30Body(OpenAPI30Parameter):
    @property
    def location(self) -> str:
        return "body"

    @property
    def name(self) -> str:
        return "attributes"


def prepare_headers_schema(value: Dict[str, Any]) -> Dict[str, Any]:
    """Improve schemas for headers.

    Headers are strings, but it is not always explicitly defined in the schema. By preparing them properly we
    can achieve significant performance improvements for such cases.
    For reference (my machine) - running a single test with 100 examples with the resulting strategy:
      - without: 4.37 s
      - with: 294 ms

    It also reduces the number of cases when the "filter_too_much" health check fails during testing.
    """
    # TODO. not needed?
    for schema in value.get("properties", {}).values():
        schema.setdefault("type", "string")
    return value


def get_schema_from_parameter(data: Dict[str, Any]) -> Dict[str, Any]:
    # In Open API 3.0 there could be "schema" or "content" field. They are mutually exclusive.
    # TODO. check when it can be applied
    if "schema" in data:
        return data["schema"]
    options = iter(data["content"].values())
    return next(options)["schema"]


# TODO. handle body
# - Make body parameter more specific. Each has a media-type
# - then endpoint.body is a list of possible
# - How can we let the user modify "case"? save body serialization function in "case" for later?
