from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Generator

from hypothesis.errors import Unsatisfiable
from hypothesis.reporting import with_reporter

from schemathesis.config import OutputConfig
from schemathesis.core.jsonschema.types import JsonSchema
from schemathesis.core.output import truncate_json
from schemathesis.core.parameters import ParameterLocation
from schemathesis.generation.hypothesis.examples import generate_one

if TYPE_CHECKING:
    from schemathesis.schemas import APIOperation


def ignore(_: str) -> None:
    pass


@contextmanager
def ignore_hypothesis_output() -> Generator:
    with with_reporter(ignore):  # type: ignore
        yield


UNSATISFIABILITY_CAUSE = """  - Type mismatch (e.g., enum with strings but type: integer)
  - Contradictory constraints (e.g., minimum > maximum)
  - Regex that's too complex to generate values for"""

GENERIC_UNSATISFIABLE_MESSAGE = f"""Cannot generate test data for this operation

Unable to identify the specific parameter. Common causes:
{UNSATISFIABILITY_CAUSE}"""


@dataclass
class UnsatisfiableParameter:
    location: ParameterLocation
    name: str
    schema: JsonSchema

    __slots__ = ("location", "name", "schema")

    def get_error_message(self, config: OutputConfig) -> str:
        formatted_schema = truncate_json(self.schema, config=config)

        if self.location == ParameterLocation.BODY:
            # For body, name is the media type
            location = f"request body ({self.name})"
        else:
            location = f"{self.location.value} parameter '{self.name}'"

        return f"""Cannot generate test data for {location}
Schema:

{formatted_schema}

This usually means:
{UNSATISFIABILITY_CAUSE}"""


def find_unsatisfiable_parameter(operation: APIOperation) -> UnsatisfiableParameter | None:
    from hypothesis_jsonschema import from_schema

    for location, container in (
        (ParameterLocation.QUERY, operation.query),
        (ParameterLocation.PATH, operation.path_parameters),
        (ParameterLocation.HEADER, operation.headers),
        (ParameterLocation.COOKIE, operation.cookies),
        (ParameterLocation.BODY, operation.body),
    ):
        for parameter in container:
            try:
                generate_one(from_schema(parameter.optimized_schema))
            except Unsatisfiable:
                if location == ParameterLocation.BODY:
                    name = parameter.media_type
                else:
                    name = parameter.name
                schema = unbundle_schema_refs(parameter.optimized_schema, parameter.name_to_uri)
                return UnsatisfiableParameter(location=location, name=name, schema=schema)
    return None


def unbundle_schema_refs(schema: JsonSchema | list[JsonSchema], name_to_uri: dict[str, str]) -> JsonSchema:
    if isinstance(schema, dict):
        result: dict[str, Any] = {}
        for key, value in schema.items():
            if key == "$ref" and isinstance(value, str) and value.startswith("#/x-bundled/"):
                # Extract bundled name (e.g., "schema1" from "#/x-bundled/schema1")
                bundled_name = value.split("/")[-1]
                if bundled_name in name_to_uri:
                    original_uri = name_to_uri[bundled_name]
                    # Extract fragment after # (e.g., "#/components/schemas/ObjectType")
                    if "#" in original_uri:
                        result[key] = "#" + original_uri.split("#", 1)[1]
                    else:
                        # Fallback if no fragment
                        result[key] = value
                else:
                    result[key] = value
            elif key == "x-bundled" and isinstance(value, dict):
                # Replace x-bundled with proper components/schemas structure
                components: dict[str, dict[str, Any]] = {"schemas": {}}
                for bundled_name, bundled_schema in value.items():
                    if bundled_name in name_to_uri:
                        original_uri = name_to_uri[bundled_name]
                        # Extract schema name (e.g., "ObjectType" from "...#/components/schemas/ObjectType")
                        if "#/components/schemas/" in original_uri:
                            schema_name = original_uri.split("#/components/schemas/")[1]
                            components["schemas"][schema_name] = unbundle_schema_refs(bundled_schema, name_to_uri)
                        else:
                            # Fallback: keep bundled name if URI doesn't match expected pattern
                            components["schemas"][bundled_name] = unbundle_schema_refs(bundled_schema, name_to_uri)
                    else:
                        components["schemas"][bundled_name] = unbundle_schema_refs(bundled_schema, name_to_uri)
                result["components"] = components
            elif isinstance(value, (dict, list)):
                # Recursively process all other values
                result[key] = unbundle_schema_refs(value, name_to_uri)
            else:
                result[key] = value
        return result
    elif isinstance(schema, list):
        return [unbundle_schema_refs(item, name_to_uri) for item in schema]  # type: ignore
    return schema


def build_unsatisfiable_error(operation: APIOperation, *, with_tip: bool) -> Unsatisfiable:
    __tracebackhide__ = True
    unsatisfiable = find_unsatisfiable_parameter(operation)

    if unsatisfiable is not None:
        message = unsatisfiable.get_error_message(operation.schema.config.output)
    else:
        message = GENERIC_UNSATISFIABLE_MESSAGE

    if with_tip:
        message += "\n\nTip: Review all parameters and request body schemas for conflicting constraints"

    return Unsatisfiable(message)
