from __future__ import annotations

from contextlib import suppress
from dataclasses import dataclass
from functools import lru_cache
from itertools import chain, cycle, islice
from typing import TYPE_CHECKING, Any, Generator, Iterator, Union, cast

import requests
from hypothesis_jsonschema import from_schema

from schemathesis.config import GenerationConfig
from schemathesis.core.transforms import deepclone
from schemathesis.core.transport import DEFAULT_RESPONSE_TIMEOUT
from schemathesis.generation.case import Case
from schemathesis.generation.hypothesis import examples
from schemathesis.generation.meta import TestPhase
from schemathesis.schemas import APIOperation
from schemathesis.specs.openapi.serialization import get_serializers_for_operation

from ._hypothesis import get_default_format_strategies, openapi_cases
from .constants import LOCATION_TO_CONTAINER
from .formats import STRING_FORMATS
from .parameters import OpenAPIBody, OpenAPIParameter

if TYPE_CHECKING:
    from hypothesis.strategies import SearchStrategy


@dataclass
class ParameterExample:
    """A single example for a named parameter."""

    container: str
    name: str
    value: Any


@dataclass
class BodyExample:
    """A single example for a body."""

    value: Any
    media_type: str


Example = Union[ParameterExample, BodyExample]


def get_strategies_from_examples(
    operation: APIOperation[OpenAPIParameter], **kwargs: Any
) -> list[SearchStrategy[Case]]:
    """Build a set of strategies that generate test cases based on explicit examples in the schema."""
    maps = get_serializers_for_operation(operation)

    def serialize_components(case: Case) -> Case:
        """Applies special serialization rules for case components.

        For example, here, query parameters will be rendered in the `deepObject` style if needed.
        """
        for container, map_func in maps.items():
            value = getattr(case, container)
            setattr(case, container, map_func(value))
        return case

    # Extract all top-level examples from the `examples` & `example` fields (`x-` prefixed versions in Open API 2)
    examples = list(extract_top_level(operation))
    # Add examples from parameter's schemas
    examples.extend(extract_from_schemas(operation))
    return [
        openapi_cases(operation=operation, **{**parameters, **kwargs, "phase": TestPhase.EXAMPLES}).map(
            serialize_components
        )
        for parameters in produce_combinations(examples)
    ]


def extract_top_level(operation: APIOperation[OpenAPIParameter]) -> Generator[Example, None, None]:
    """Extract top-level parameter examples from `examples` & `example` fields."""
    responses = find_in_responses(operation)
    for parameter in operation.iter_parameters():
        if "schema" in parameter.definition:
            definitions = [parameter.definition, *_expand_subschemas(parameter.definition["schema"])]
        else:
            definitions = [parameter.definition]
        for definition in definitions:
            # Open API 2 also supports `example`
            for example_field in {"example", parameter.example_field}:
                if isinstance(definition, dict) and example_field in definition:
                    yield ParameterExample(
                        container=LOCATION_TO_CONTAINER[parameter.location],
                        name=parameter.name,
                        value=definition[example_field],
                    )
        if parameter.examples_field in parameter.definition:
            unresolved_definition = _find_parameter_examples_definition(
                operation, parameter.name, parameter.examples_field
            )
            for value in extract_inner_examples(parameter.definition[parameter.examples_field], unresolved_definition):
                yield ParameterExample(
                    container=LOCATION_TO_CONTAINER[parameter.location], name=parameter.name, value=value
                )
        if "schema" in parameter.definition:
            for schema in _expand_subschemas(parameter.definition["schema"]):
                if isinstance(schema, dict) and parameter.examples_field in schema:
                    for value in schema[parameter.examples_field]:
                        yield ParameterExample(
                            container=LOCATION_TO_CONTAINER[parameter.location], name=parameter.name, value=value
                        )
        for value in find_matching_in_responses(responses, parameter.name):
            yield ParameterExample(
                container=LOCATION_TO_CONTAINER[parameter.location], name=parameter.name, value=value
            )
    for alternative in operation.body:
        alternative = cast(OpenAPIBody, alternative)
        if "schema" in alternative.definition:
            definitions = [alternative.definition, *_expand_subschemas(alternative.definition["schema"])]
        else:
            definitions = [alternative.definition]
        for definition in definitions:
            # Open API 2 also supports `example`
            for example_field in {"example", alternative.example_field}:
                if isinstance(definition, dict) and example_field in definition:
                    yield BodyExample(value=definition[example_field], media_type=alternative.media_type)
        if alternative.examples_field in alternative.definition:
            unresolved_definition = _find_request_body_examples_definition(operation, alternative)
            for value in extract_inner_examples(
                alternative.definition[alternative.examples_field], unresolved_definition
            ):
                yield BodyExample(value=value, media_type=alternative.media_type)
        if "schema" in alternative.definition:
            for schema in _expand_subschemas(alternative.definition["schema"]):
                if isinstance(schema, dict) and alternative.examples_field in schema:
                    for value in schema[alternative.examples_field]:
                        yield BodyExample(value=value, media_type=alternative.media_type)


def _expand_subschemas(schema: dict[str, Any] | bool) -> Generator[dict[str, Any] | bool, None, None]:
    yield schema
    if isinstance(schema, dict):
        for key in ("anyOf", "oneOf"):
            if key in schema:
                for subschema in schema[key]:
                    yield subschema
        if "allOf" in schema:
            subschema = deepclone(schema["allOf"][0])
            for sub in schema["allOf"][1:]:
                if isinstance(sub, dict):
                    for key, value in sub.items():
                        if key == "properties":
                            subschema.setdefault("properties", {}).update(value)
                        elif key == "required":
                            subschema.setdefault("required", []).extend(value)
                        elif key == "examples":
                            subschema.setdefault("examples", []).extend(value)
                        elif key == "example":
                            subschema.setdefault("examples", []).append(value)
                        else:
                            subschema[key] = value
            yield subschema


def _find_parameter_examples_definition(
    operation: APIOperation[OpenAPIParameter], parameter_name: str, field_name: str
) -> dict[str, Any]:
    """Find the original, unresolved `examples` definition of a parameter."""
    from .schemas import BaseOpenAPISchema

    schema = cast(BaseOpenAPISchema, operation.schema)
    raw_schema = schema.raw_schema
    path_data = raw_schema["paths"][operation.path]
    parameters = chain(path_data[operation.method].get("parameters", []), path_data.get("parameters", []))
    for parameter in parameters:
        if "$ref" in parameter:
            _, parameter = schema.resolver.resolve(parameter["$ref"])
        if parameter["name"] == parameter_name:
            return parameter[field_name]
    raise RuntimeError("Example definition is not found. It should not happen")


def _find_request_body_examples_definition(
    operation: APIOperation[OpenAPIParameter], alternative: OpenAPIBody
) -> dict[str, Any]:
    """Find the original, unresolved `examples` definition of a request body variant."""
    from .schemas import BaseOpenAPISchema

    schema = cast(BaseOpenAPISchema, operation.schema)
    if schema.specification.version == "2.0":
        raw_schema = schema.raw_schema
        path_data = raw_schema["paths"][operation.path]
        parameters = chain(path_data[operation.method].get("parameters", []), path_data.get("parameters", []))
        for parameter in parameters:
            if "$ref" in parameter:
                _, parameter = schema.resolver.resolve(parameter["$ref"])
            if parameter["in"] == "body":
                return parameter[alternative.examples_field]
        raise RuntimeError("Example definition is not found. It should not happen")
    request_body = operation.definition.raw["requestBody"]
    while "$ref" in request_body:
        _, request_body = schema.resolver.resolve(request_body["$ref"])
    return request_body["content"][alternative.media_type][alternative.examples_field]


def extract_inner_examples(
    examples: dict[str, Any], unresolved_definition: dict[str, Any]
) -> Generator[Any, None, None]:
    """Extract exact examples values from the `examples` dictionary."""
    for name, example in examples.items():
        if "$ref" in unresolved_definition[name] and "value" not in example and "externalValue" not in example:
            # The example here is a resolved example and should be yielded as is
            yield example
        if isinstance(example, dict):
            if "value" in example:
                yield example["value"]
            elif "externalValue" in example:
                with suppress(requests.RequestException):
                    # Report a warning if not available?
                    yield load_external_example(example["externalValue"])


@lru_cache
def load_external_example(url: str) -> bytes:
    """Load examples the `externalValue` keyword."""
    response = requests.get(url, timeout=DEFAULT_RESPONSE_TIMEOUT)
    response.raise_for_status()
    return response.content


def extract_from_schemas(operation: APIOperation[OpenAPIParameter]) -> Generator[Example, None, None]:
    """Extract examples from parameters' schema definitions."""
    for parameter in operation.iter_parameters():
        schema = parameter.as_json_schema(operation)
        for value in extract_from_schema(operation, schema, parameter.example_field, parameter.examples_field):
            yield ParameterExample(
                container=LOCATION_TO_CONTAINER[parameter.location], name=parameter.name, value=value
            )
    for alternative in operation.body:
        alternative = cast(OpenAPIBody, alternative)
        schema = alternative.as_json_schema(operation)
        for example_field, examples_field in (("example", "examples"), ("x-example", "x-examples")):
            for value in extract_from_schema(operation, schema, example_field, examples_field):
                yield BodyExample(value=value, media_type=alternative.media_type)


def extract_from_schema(
    operation: APIOperation[OpenAPIParameter],
    schema: dict[str, Any],
    example_field_name: str,
    examples_field_name: str,
) -> Generator[Any, None, None]:
    """Extract all examples from a single schema definition."""
    # This implementation supports only `properties` and `items`
    if "properties" in schema:
        variants = {}
        required = schema.get("required", [])
        to_generate: dict[str, Any] = {}
        for name, subschema in schema["properties"].items():
            values = []
            for subsubschema in _expand_subschemas(subschema):
                if isinstance(subsubschema, bool):
                    to_generate[name] = subsubschema
                    continue
                if example_field_name in subsubschema:
                    values.append(subsubschema[example_field_name])
                if examples_field_name in subsubschema and isinstance(subsubschema[examples_field_name], list):
                    # These are JSON Schema examples, which is an array of values
                    values.extend(subsubschema[examples_field_name])
                # Check nested examples as well
                values.extend(extract_from_schema(operation, subsubschema, example_field_name, examples_field_name))
                if not values:
                    if name in required:
                        # Defer generation to only generate these variants if at least one property has examples
                        to_generate[name] = subsubschema
                    continue
                variants[name] = values
        if variants:
            config = operation.schema.config.generation_for(operation=operation, phase="examples")
            for name, subschema in to_generate.items():
                if name in variants:
                    # Generated by one of `anyOf` or similar sub-schemas
                    continue
                subschema = operation.schema.prepare_schema(subschema)
                generated = _generate_single_example(subschema, config)
                variants[name] = [generated]
            # Calculate the maximum number of examples any property has
            total_combos = max(len(examples) for examples in variants.values())
            # Evenly distribute examples by cycling through them
            for idx in range(total_combos):
                yield {
                    name: next(islice(cycle(property_variants), idx, None))
                    for name, property_variants in variants.items()
                }
    elif "items" in schema and isinstance(schema["items"], dict):
        # Each inner value should be wrapped in an array
        for value in extract_from_schema(operation, schema["items"], example_field_name, examples_field_name):
            yield [value]


def _generate_single_example(
    schema: dict[str, Any],
    generation_config: GenerationConfig,
) -> Any:
    strategy = from_schema(
        schema,
        custom_formats={**get_default_format_strategies(), **STRING_FORMATS},
        allow_x00=generation_config.allow_x00,
        codec=generation_config.codec,
    )
    return examples.generate_one(strategy)


def produce_combinations(examples: list[Example]) -> Generator[dict[str, Any], None, None]:
    """Generate a minimal set of combinations for the given list of parameters."""
    # Split regular parameters & body variants first
    parameters: dict[str, dict[str, list]] = {}
    bodies: dict[str, list] = {}
    for example in examples:
        if isinstance(example, ParameterExample):
            container_examples = parameters.setdefault(example.container, {})
            parameter_examples = container_examples.setdefault(example.name, [])
            parameter_examples.append(example.value)
        else:
            values = bodies.setdefault(example.media_type, [])
            values.append(example.value)

    if bodies:
        if parameters:
            parameter_combos = list(_produce_parameter_combinations(parameters))
            body_combos = [
                {"media_type": media_type, "body": value} for media_type, values in bodies.items() for value in values
            ]
            total_combos = max(len(parameter_combos), len(body_combos))
            for idx in range(total_combos):
                yield {
                    **next(islice(cycle(body_combos), idx, None)),
                    **next(islice(cycle(parameter_combos), idx, None)),
                }
        else:
            for media_type, values in bodies.items():
                for body in values:
                    yield {"media_type": media_type, "body": body}
    elif parameters:
        yield from _produce_parameter_combinations(parameters)


def _produce_parameter_combinations(parameters: dict[str, dict[str, list]]) -> Generator[dict[str, Any], None, None]:
    total_combos = max(
        len(variants) for container_variants in parameters.values() for variants in container_variants.values()
    )
    for idx in range(total_combos):
        yield {
            container: {
                name: next(islice(cycle(parameter_variants), idx, None))
                for name, parameter_variants in variants.items()
            }
            for container, variants in parameters.items()
        }


def find_in_responses(operation: APIOperation) -> dict[str, list[dict[str, Any]]]:
    """Find schema examples in responses."""
    output: dict[str, list[dict[str, Any]]] = {}
    for status_code, response in operation.definition.raw.get("responses", {}).items():
        if not str(status_code).startswith("2"):
            # Check only 2xx responses
            continue
        if isinstance(response, dict) and "$ref" in response:
            _, response = operation.schema.resolver.resolve_in_scope(response, operation.definition.scope)  # type:ignore[attr-defined]
        for media_type, definition in response.get("content", {}).items():
            schema_ref = definition.get("schema", {}).get("$ref")
            if schema_ref:
                name = schema_ref.split("/")[-1]
            else:
                name = f"{status_code}/{media_type}"
            for examples_field, example_field in (
                ("examples", "example"),
                ("x-examples", "x-example"),
            ):
                examples = definition.get(examples_field, {})
                for example in examples.values():
                    if "value" in example:
                        output.setdefault(name, []).append(example["value"])
                if example_field in definition:
                    output.setdefault(name, []).append(definition[example_field])
    return output


NOT_FOUND = object()


def find_matching_in_responses(examples: dict[str, list], param: str) -> Iterator[Any]:
    """Find matching parameter examples."""
    normalized = param.lower()
    is_id_param = normalized.endswith("id")
    # Extract values from response examples that match input parameters.
    # E.g., for `GET /orders/{id}/`, use "id" or "orderId" from `Order` response
    # as examples for the "id" path parameter.
    for schema_name, schema_examples in examples.items():
        for example in schema_examples:
            if not isinstance(example, dict):
                continue
            # Unwrapping example from `{"item": [{...}]}`
            if isinstance(example, dict):
                inner = next((value for key, value in example.items() if key.lower() == schema_name.lower()), None)
                if inner is not None:
                    if isinstance(inner, list):
                        for sub_example in inner:
                            if isinstance(sub_example, dict):
                                for found in _find_matching_in_responses(
                                    sub_example, schema_name, param, normalized, is_id_param
                                ):
                                    if found is not NOT_FOUND:
                                        yield found
                        continue
                    if isinstance(inner, dict):
                        example = inner
            for found in _find_matching_in_responses(example, schema_name, param, normalized, is_id_param):
                if found is not NOT_FOUND:
                    yield found


def _find_matching_in_responses(
    example: dict[str, Any], schema_name: str, param: str, normalized: str, is_id_param: bool
) -> Iterator[Any]:
    # Check for exact match
    if param in example:
        yield example[param]
        return
    if is_id_param and param[:-2] in example:
        value = example[param[:-2]]
        if isinstance(value, list):
            for sub_example in value:
                for found in _find_matching_in_responses(sub_example, schema_name, param, normalized, is_id_param):
                    if found is not NOT_FOUND:
                        yield found
            return
        else:
            yield value
            return

    # Check for case-insensitive match
    for key in example:
        if key.lower() == normalized:
            yield example[key]
            return
    else:
        # If no match found and it's an ID parameter, try additional checks
        if is_id_param:
            # Check for 'id' if parameter is '{something}Id'
            if "id" in example:
                yield example["id"]
                return
            # Check for '{schemaName}Id' or '{schemaName}_id'
            if normalized == "id" or normalized.startswith(schema_name.lower()):
                for key in (schema_name, schema_name.lower()):
                    for suffix in ("_id", "Id"):
                        with_suffix = f"{key}{suffix}"
                        if with_suffix in example:
                            yield example[with_suffix]
                            return
    yield NOT_FOUND
