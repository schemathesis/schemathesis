from __future__ import annotations

from contextlib import suppress
from dataclasses import dataclass
from functools import lru_cache
from itertools import islice, cycle, chain
from typing import Any, Generator, Union, cast

import requests
import hypothesis
from hypothesis_jsonschema import from_schema
from hypothesis.strategies import SearchStrategy

from .parameters import OpenAPIParameter, OpenAPIBody
from ...constants import DEFAULT_RESPONSE_TIMEOUT
from ...models import APIOperation, Case
from ._hypothesis import get_case_strategy
from .constants import LOCATION_TO_CONTAINER


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
    operation: APIOperation[OpenAPIParameter, Case], examples_field: str = "examples"
) -> list[SearchStrategy[Case]]:
    """Build a set of strategies that generate test cases based on explicit examples in the schema."""
    maps = {}
    for location, container in LOCATION_TO_CONTAINER.items():
        serializer = operation.get_parameter_serializer(location)
        if serializer is not None:
            maps[container] = serializer

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
        get_case_strategy(operation=operation, **parameters).map(serialize_components)
        for parameters in produce_combinations(examples)
    ]


def extract_top_level(operation: APIOperation[OpenAPIParameter, Case]) -> Generator[Example, None, None]:
    """Extract top-level parameter examples from `examples` & `example` fields."""
    for parameter in operation.iter_parameters():
        if "schema" in parameter.definition:
            definitions = [parameter.definition, parameter.definition["schema"]]
        else:
            definitions = [parameter.definition]
        for definition in definitions:
            # Open API 2 also supports `example`
            for example_field in {"example", parameter.example_field}:
                if example_field in definition:
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
            schema = parameter.definition["schema"]
            if parameter.examples_field in schema:
                for value in schema[parameter.examples_field]:
                    yield ParameterExample(
                        container=LOCATION_TO_CONTAINER[parameter.location], name=parameter.name, value=value
                    )
    for alternative in operation.body:
        alternative = cast(OpenAPIBody, alternative)
        if "schema" in alternative.definition:
            definitions = [alternative.definition, alternative.definition["schema"]]
        else:
            definitions = [alternative.definition]
        for definition in definitions:
            # Open API 2 also supports `example`
            for example_field in {"example", alternative.example_field}:
                if example_field in definition:
                    yield BodyExample(value=definition[example_field], media_type=alternative.media_type)
        if alternative.examples_field in alternative.definition:
            unresolved_definition = _find_request_body_examples_definition(operation, alternative)
            for value in extract_inner_examples(
                alternative.definition[alternative.examples_field], unresolved_definition
            ):
                yield BodyExample(value=value, media_type=alternative.media_type)
        if "schema" in alternative.definition:
            schema = alternative.definition["schema"]
            if alternative.examples_field in schema:
                for value in schema[alternative.examples_field]:
                    yield BodyExample(value=value, media_type=alternative.media_type)


def _find_parameter_examples_definition(
    operation: APIOperation[OpenAPIParameter, Case], parameter_name: str, field_name: str
) -> dict[str, Any]:
    """Find the original, unresolved `examples` definition of a parameter."""
    from .schemas import BaseOpenAPISchema

    schema = cast(BaseOpenAPISchema, operation.schema)
    raw_schema = schema.raw_schema
    path_data = raw_schema["paths"][operation.path]
    parameters = chain(path_data.get("parameters", []), path_data[operation.method].get("parameters", []))
    for parameter in parameters:
        if "$ref" in parameter:
            _, parameter = schema.resolver.resolve(parameter["$ref"])
        if parameter["name"] == parameter_name:
            return parameter[field_name]
    raise RuntimeError("Example definition is not found. It should not happen")


def _find_request_body_examples_definition(
    operation: APIOperation[OpenAPIParameter, Case], alternative: OpenAPIBody
) -> dict[str, Any]:
    """Find the original, unresolved `examples` definition of a request body variant."""
    from .schemas import BaseOpenAPISchema

    schema = cast(BaseOpenAPISchema, operation.schema)
    if schema.spec_version == "2.0":
        raw_schema = schema.raw_schema
        path_data = raw_schema["paths"][operation.path]
        parameters = chain(path_data.get("parameters", []), path_data[operation.method].get("parameters", []))
        for parameter in parameters:
            if parameter["in"] == "body":
                return parameter[alternative.examples_field]
        raise RuntimeError("Example definition is not found. It should not happen")
    return operation.definition.raw["requestBody"]["content"][alternative.media_type][alternative.examples_field]


def extract_inner_examples(
    examples: dict[str, Any], unresolved_definition: dict[str, Any]
) -> Generator[Any, None, None]:
    """Extract exact examples values from the `examples` dictionary."""
    for name, example in examples.items():
        if "$ref" in unresolved_definition[name]:
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
    response = requests.get(url, timeout=DEFAULT_RESPONSE_TIMEOUT / 1000)
    response.raise_for_status()
    return response.content


def extract_from_schemas(operation: APIOperation[OpenAPIParameter, Case]) -> Generator[Example, None, None]:
    """Extract examples from parameters' schema definitions."""
    for parameter in operation.iter_parameters():
        schema = parameter.as_json_schema(operation)
        for value in extract_from_schema(schema, parameter.example_field, parameter.examples_field):
            yield ParameterExample(
                container=LOCATION_TO_CONTAINER[parameter.location], name=parameter.name, value=value
            )
    for alternative in operation.body:
        alternative = cast(OpenAPIBody, alternative)
        schema = alternative.as_json_schema(operation)
        for value in extract_from_schema(schema, alternative.example_field, alternative.examples_field):
            yield BodyExample(value=value, media_type=alternative.media_type)


def extract_from_schema(
    schema: dict[str, Any], example_field_name: str, examples_field_name: str
) -> Generator[Any, None, None]:
    """Extract all examples from a single schema definition."""
    # This implementation supports only `properties` and `items`
    if "properties" in schema:
        variants = {}
        required = schema.get("required", [])
        to_generate = {}
        for name, subschema in schema["properties"].items():
            values = []
            if example_field_name in subschema:
                values.append(subschema[example_field_name])
            if examples_field_name in subschema and isinstance(subschema[examples_field_name], list):
                # These are JSON Schema examples, which is an array of values
                values.extend(subschema[examples_field_name])
            if not values:
                if name in required:
                    # Defer generation to only generate these variants if at least one property has examples
                    to_generate[name] = subschema
                continue
            variants[name] = values
        if variants:
            for name, subschema in to_generate.items():
                generated = _generate_single_example(subschema)
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
        for value in extract_from_schema(schema["items"], example_field_name, examples_field_name):
            yield [value]


def _generate_single_example(schema: dict[str, Any]) -> Any:
    examples = []

    @hypothesis.given(from_schema(schema))  # type: ignore
    @hypothesis.settings(  # type: ignore
        database=None,
        max_examples=1,
        deadline=None,
        verbosity=hypothesis.Verbosity.quiet,
        phases=(hypothesis.Phase.generate,),
        suppress_health_check=list(hypothesis.HealthCheck),
    )
    def example_generating_inner_function(ex: Any) -> None:
        examples.append(ex)

    example_generating_inner_function()

    return examples[0]


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
