from __future__ import annotations

from collections.abc import Generator, Iterator
from contextlib import suppress
from dataclasses import dataclass
from functools import lru_cache
from itertools import cycle, islice
from typing import TYPE_CHECKING, Any, cast, overload

import requests
from hypothesis_jsonschema import from_schema

from schemathesis.config import GenerationConfig
from schemathesis.core.compat import RefResolutionError, RefResolver
from schemathesis.core.errors import InfiniteRecursiveReference, UnresolvableReference
from schemathesis.core.jsonschema.bundler import BUNDLE_STORAGE_KEY
from schemathesis.core.transforms import deepclone
from schemathesis.core.transport import DEFAULT_RESPONSE_TIMEOUT
from schemathesis.generation.case import Case
from schemathesis.generation.hypothesis import examples
from schemathesis.generation.meta import TestPhase
from schemathesis.schemas import APIOperation
from schemathesis.specs.openapi.adapter import OpenApiResponses
from schemathesis.specs.openapi.adapter.parameters import OpenApiBody, OpenApiParameter
from schemathesis.specs.openapi.adapter.security import OpenApiSecurityParameters
from schemathesis.specs.openapi.serialization import get_serializers_for_operation

from ._hypothesis import get_default_format_strategies, openapi_cases
from .formats import STRING_FORMATS

if TYPE_CHECKING:
    from hypothesis.strategies import SearchStrategy

    from schemathesis.specs.openapi.schemas import OpenApiSchema


@dataclass
class ParameterExample:
    """A single example for a named parameter."""

    container: str
    name: str
    value: Any

    __slots__ = ("container", "name", "value")


@dataclass
class BodyExample:
    """A single example for a body."""

    value: Any
    media_type: str

    __slots__ = ("value", "media_type")


Example = ParameterExample | BodyExample


def merge_kwargs(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    mergeable_keys = {"path_parameters", "headers", "cookies", "query"}

    for key, value in right.items():
        if key in mergeable_keys and key in left:
            if isinstance(left[key], dict) and isinstance(value, dict):
                # kwargs takes precedence
                left[key] = {**left[key], **value}
                continue
        left[key] = value

    return left


def get_strategies_from_examples(
    operation: APIOperation[OpenApiParameter, OpenApiResponses, OpenApiSecurityParameters], **kwargs: Any
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
        openapi_cases(operation=operation, phase=TestPhase.EXAMPLES, **merge_kwargs(parameters, kwargs)).map(
            serialize_components
        )
        for parameters in produce_combinations(examples)
    ]


def extract_top_level(
    operation: APIOperation[OpenApiParameter, OpenApiResponses, OpenApiSecurityParameters],
) -> Generator[Example, None, None]:
    """Extract top-level parameter examples from `examples` & `example` fields."""
    from .schemas import OpenApiSchema

    assert isinstance(operation.schema, OpenApiSchema)

    responses = list(operation.responses.iter_examples())
    for parameter in operation.iter_parameters():
        if "schema" in parameter.definition:
            schema = parameter.definition["schema"]
            resolver = RefResolver.from_schema(schema)
            reference_path: tuple[str, ...] = ()
            definitions = [
                parameter.definition,
                *[
                    expanded_schema
                    for expanded_schema, _ in _expand_subschemas(
                        schema=schema, resolver=resolver, reference_path=reference_path
                    )
                ],
            ]
        else:
            definitions = [parameter.definition]
        for definition in definitions:
            # Open API 2 also supports `example`
            for example_keyword in {"example", parameter.adapter.example_keyword}:
                if isinstance(definition, dict) and example_keyword in definition:
                    yield ParameterExample(
                        container=parameter.location.container_name,
                        name=parameter.name,
                        value=definition[example_keyword],
                    )
        if parameter.adapter.examples_container_keyword in parameter.definition:
            for value in extract_inner_examples(
                parameter.definition[parameter.adapter.examples_container_keyword], operation.schema
            ):
                yield ParameterExample(container=parameter.location.container_name, name=parameter.name, value=value)
        if "schema" in parameter.definition:
            schema = parameter.definition["schema"]
            resolver = RefResolver.from_schema(schema)
            reference_path = ()
            for expanded_schema, _ in _expand_subschemas(
                schema=schema, resolver=resolver, reference_path=reference_path
            ):
                if (
                    isinstance(expanded_schema, dict)
                    and parameter.adapter.examples_container_keyword in expanded_schema
                ):
                    for value in expanded_schema[parameter.adapter.examples_container_keyword]:
                        yield ParameterExample(
                            container=parameter.location.container_name, name=parameter.name, value=value
                        )
        for value in find_matching_in_responses(responses, parameter.name):
            yield ParameterExample(container=parameter.location.container_name, name=parameter.name, value=value)
    for alternative in operation.body:
        body = cast(OpenApiBody, alternative)
        if "schema" in body.definition:
            schema = body.definition["schema"]
            resolver = RefResolver.from_schema(schema)
            reference_path = ()
            definitions = [
                body.definition,
                *[
                    expanded_schema
                    for expanded_schema, _ in _expand_subschemas(
                        schema=schema, resolver=resolver, reference_path=reference_path
                    )
                ],
            ]
        else:
            definitions = [body.definition]
        for definition in definitions:
            # Open API 2 also supports `example`
            for example_keyword in {"example", body.adapter.example_keyword}:
                if isinstance(definition, dict) and example_keyword in definition:
                    yield BodyExample(value=definition[example_keyword], media_type=body.media_type)
        if body.adapter.examples_container_keyword in body.definition:
            for value in extract_inner_examples(
                body.definition[body.adapter.examples_container_keyword], operation.schema
            ):
                yield BodyExample(value=value, media_type=body.media_type)
        if "schema" in body.definition:
            schema = body.definition["schema"]
            resolver = RefResolver.from_schema(schema)
            reference_path = ()
            for expanded_schema, _ in _expand_subschemas(
                schema=schema, resolver=resolver, reference_path=reference_path
            ):
                if isinstance(expanded_schema, dict) and body.adapter.examples_container_keyword in expanded_schema:
                    for value in expanded_schema[body.adapter.examples_container_keyword]:
                        yield BodyExample(value=value, media_type=body.media_type)


@overload
def _resolve_bundled(
    schema: dict[str, Any], resolver: RefResolver, reference_path: tuple[str, ...]
) -> tuple[dict[str, Any], tuple[str, ...]]: ...


@overload
def _resolve_bundled(
    schema: bool, resolver: RefResolver, reference_path: tuple[str, ...]
) -> tuple[bool, tuple[str, ...]]: ...


def _resolve_bundled(
    schema: dict[str, Any] | bool, resolver: RefResolver, reference_path: tuple[str, ...]
) -> tuple[dict[str, Any] | bool, tuple[str, ...]]:
    """Resolve $ref if present."""
    if isinstance(schema, dict):
        reference = schema.get("$ref")
        if isinstance(reference, str):
            # Check if this reference is already in the current path
            if reference in reference_path:
                # Real infinite recursive references are caught at the bundling stage.
                # This recursion happens because of how the example phase generates data - it explores everything,
                # so it is the easiest way to break such cycles
                cycle = list(reference_path[reference_path.index(reference) :])
                raise InfiniteRecursiveReference(reference, cycle)

            new_path = reference_path + (reference,)

            try:
                _, resolved_schema = resolver.resolve(reference)
            except RefResolutionError as exc:
                raise UnresolvableReference(reference) from exc

            return resolved_schema, new_path

    return schema, reference_path


def _expand_subschemas(
    *, schema: dict[str, Any] | bool, resolver: RefResolver, reference_path: tuple[str, ...]
) -> Generator[tuple[dict[str, Any] | bool, tuple[str, ...]], None, None]:
    """Expand schema and all its subschemas."""
    try:
        schema, current_path = _resolve_bundled(schema, resolver, reference_path)
    except InfiniteRecursiveReference:
        return

    yield schema, current_path

    if isinstance(schema, dict):
        # For anyOf/oneOf, yield each alternative with the same path
        for key in ("anyOf", "oneOf"):
            if key in schema:
                for subschema in schema[key]:
                    # Each alternative starts with the current path
                    yield subschema, current_path

        # For allOf, merge all alternatives
        if schema.get("allOf"):
            subschema = deepclone(schema["allOf"][0])
            try:
                subschema, expanded_path = _resolve_bundled(subschema, resolver, current_path)
            except InfiniteRecursiveReference:
                return

            for sub in schema["allOf"][1:]:
                if isinstance(sub, dict):
                    try:
                        sub, _ = _resolve_bundled(sub, resolver, current_path)
                    except InfiniteRecursiveReference:
                        return
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

            # Merge parent schema's fields with the merged allOf result
            # Parent's fields take precedence as they are more specific
            parent_has_example = "example" in schema or "examples" in schema

            # If parent has examples, remove examples from merged allOf to avoid duplicates
            # The parent's examples were already yielded from the parent schema itself
            if parent_has_example:
                subschema.pop("example", None)
                subschema.pop("examples", None)

            for key, value in schema.items():
                if key in ("allOf", "example", "examples", BUNDLE_STORAGE_KEY):
                    # Skip the allOf itself, we already processed it
                    # Skip parent's examples - they were already yielded
                    # Skip bundled schemas too to avoid infinite recursion
                    continue
                elif key == "properties":
                    # Merge parent properties (parent overrides allOf)
                    subschema.setdefault("properties", {}).update(value)
                elif key == "required":
                    # Extend required list
                    subschema.setdefault("required", []).extend(value)
                else:
                    # For other fields, parent value overrides
                    subschema[key] = value

            yield subschema, expanded_path


def extract_inner_examples(examples: dict[str, Any] | list, schema: OpenApiSchema) -> Generator[Any, None, None]:
    """Extract exact examples values from the `examples` dictionary."""
    if isinstance(examples, dict):
        for example in examples.values():
            if isinstance(example, dict):
                if "$ref" in example:
                    _, example = schema.resolver.resolve(example["$ref"])
                if "value" in example:
                    yield example["value"]
                elif "externalValue" in example:
                    with suppress(requests.RequestException):
                        # Report a warning if not available?
                        yield load_external_example(example["externalValue"])
                elif example:
                    yield example
    elif isinstance(examples, list):
        yield from examples


@lru_cache
def load_external_example(url: str) -> bytes:
    """Load examples the `externalValue` keyword."""
    response = requests.get(url, timeout=DEFAULT_RESPONSE_TIMEOUT)
    response.raise_for_status()
    return response.content


def extract_from_schemas(
    operation: APIOperation[OpenApiParameter, OpenApiResponses, OpenApiSecurityParameters],
) -> Generator[Example, None, None]:
    """Extract examples from parameters' schema definitions."""
    for parameter in operation.iter_parameters():
        try:
            schema = parameter.optimized_schema
        except TypeError:
            # Invalid schema (e.g., non-string pattern value)
            continue
        if isinstance(schema, bool):
            continue
        resolver = RefResolver.from_schema(schema)
        reference_path: tuple[str, ...] = ()
        bundle_storage = schema.get(BUNDLE_STORAGE_KEY)
        for value in extract_from_schema(
            operation=operation,
            schema=schema,
            example_keyword=parameter.adapter.example_keyword,
            examples_container_keyword=parameter.adapter.examples_container_keyword,
            resolver=resolver,
            reference_path=reference_path,
            bundle_storage=bundle_storage,
        ):
            yield ParameterExample(container=parameter.location.container_name, name=parameter.name, value=value)
    for alternative in operation.body:
        body = cast(OpenApiBody, alternative)
        try:
            schema = body.optimized_schema
        except TypeError:
            # Invalid schema (e.g., non-string pattern value)
            continue
        if isinstance(schema, bool):
            continue
        resolver = RefResolver.from_schema(schema)
        bundle_storage = schema.get(BUNDLE_STORAGE_KEY)
        for example_keyword, examples_container_keyword in (("example", "examples"), ("x-example", "x-examples")):
            reference_path = ()
            for value in extract_from_schema(
                operation=operation,
                schema=schema,
                example_keyword=example_keyword,
                examples_container_keyword=examples_container_keyword,
                resolver=resolver,
                reference_path=reference_path,
                bundle_storage=bundle_storage,
            ):
                yield BodyExample(value=value, media_type=body.media_type)


def extract_from_schema(
    *,
    operation: APIOperation[OpenApiParameter, OpenApiResponses, OpenApiSecurityParameters],
    schema: dict[str, Any],
    example_keyword: str,
    examples_container_keyword: str,
    resolver: RefResolver,
    reference_path: tuple[str, ...],
    bundle_storage: dict[str, Any] | None,
) -> Generator[Any, None, None]:
    """Extract all examples from a single schema definition."""
    # This implementation supports only `properties` and `items`
    try:
        schema, current_path = _resolve_bundled(schema, resolver, reference_path)
    except InfiniteRecursiveReference:
        return

    # If schema has allOf, we need to get merged properties and required fields from allOf items
    # This handles cases where parent has properties alongside allOf
    properties_to_process = schema.get("properties", {})
    required = list(schema.get("required", []))

    # For anyOf/oneOf with required constraints, pick the first branch's required fields
    # This ensures at least one branch is satisfied (e.g., anyOf: [{required: [name]}, {required: [id]}])
    for key in ("anyOf", "oneOf"):
        sub_schemas = schema.get(key)
        if sub_schemas:
            for sub_schema in sub_schemas:
                if isinstance(sub_schema, dict) and "required" in sub_schema:
                    for field in sub_schema["required"]:
                        if field not in required and field in properties_to_process:
                            required.append(field)
                    break

    if "allOf" in schema and "properties" in schema:
        # Get the merged allOf schema which includes properties and required fields from all allOf items
        for expanded_schema, _ in _expand_subschemas(schema=schema, resolver=resolver, reference_path=current_path):
            if expanded_schema is not schema and isinstance(expanded_schema, dict):
                # This is the merged allOf result with combined properties and required fields
                if "properties" in expanded_schema:
                    properties_to_process = expanded_schema["properties"]
                if "required" in expanded_schema:
                    required = expanded_schema["required"]
                break

    if properties_to_process:
        variants = {}
        to_generate: dict[str, Any] = {}

        for name, subschema in list(properties_to_process.items()):
            values = []
            for expanded_schema, expanded_path in _expand_subschemas(
                schema=subschema, resolver=resolver, reference_path=current_path
            ):
                if isinstance(expanded_schema, bool):
                    to_generate[name] = expanded_schema
                    continue

                if example_keyword in expanded_schema:
                    values.append(expanded_schema[example_keyword])

                if examples_container_keyword in expanded_schema and isinstance(
                    expanded_schema[examples_container_keyword], list
                ):
                    # These are JSON Schema examples, which is an array of values
                    values.extend(expanded_schema[examples_container_keyword])

                # Check nested examples as well
                values.extend(
                    extract_from_schema(
                        operation=operation,
                        schema=expanded_schema,
                        example_keyword=example_keyword,
                        examples_container_keyword=examples_container_keyword,
                        resolver=resolver,
                        reference_path=expanded_path,
                        bundle_storage=bundle_storage,
                    )
                )

                if not values:
                    if name in required:
                        # Defer generation to only generate these variants if at least one property has examples
                        to_generate[name] = expanded_schema
                    continue

                variants[name] = values

        if variants:
            # Check if all required fields will be present in the generated examples
            all_required_covered = all(field in variants or field in to_generate for field in required)

            if all_required_covered:
                config = operation.schema.config.generation_for(operation=operation, phase="examples")
                for name, subschema in to_generate.items():
                    if name in variants:
                        # Generated by one of `anyOf` or similar sub-schemas
                        continue
                    if bundle_storage is not None:
                        subschema = dict(subschema)
                        subschema[BUNDLE_STORAGE_KEY] = bundle_storage
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
        for value in extract_from_schema(
            operation=operation,
            schema=schema["items"],
            example_keyword=example_keyword,
            examples_container_keyword=examples_container_keyword,
            resolver=resolver,
            reference_path=current_path,
            bundle_storage=bundle_storage,
        ):
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


NOT_FOUND = object()


def find_matching_in_responses(examples: list[tuple[str, object]], param: str) -> Iterator[Any]:
    """Find matching parameter examples."""
    normalized = param.lower()
    is_id_param = normalized.endswith("id")
    # Extract values from response examples that match input parameters.
    # E.g., for `GET /orders/{id}/`, use "id" or "orderId" from `Order` response
    # as examples for the "id" path parameter.
    for schema_name, example in examples:
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
