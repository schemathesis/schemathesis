from __future__ import annotations

from collections.abc import Generator, Iterator
from contextlib import suppress
from dataclasses import dataclass
from functools import lru_cache
from itertools import cycle, islice
from typing import TYPE_CHECKING, Any, cast, overload

import jsonschema_rs
import requests
from hypothesis_jsonschema import from_schema

from schemathesis.config import GenerationConfig
from schemathesis.core.compat import RefResolutionError, RefResolver
from schemathesis.core.errors import InfiniteRecursiveReference, UnresolvableReference
from schemathesis.core.jsonschema.bundler import BUNDLE_STORAGE_KEY
from schemathesis.core.parameters import ParameterLocation
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

    from schemathesis.specs.openapi.extra_data_source import OpenApiExtraDataSource
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


def _get_pool_combos(
    operation: APIOperation,
    extra_data_source: OpenApiExtraDataSource,
) -> list[dict[str, Any]]:
    """Return pool variants as parameter dicts, merging all locations into each slot."""
    per_location: list[list[dict[str, Any]]] = []
    for location in (
        ParameterLocation.PATH,
        ParameterLocation.QUERY,
        ParameterLocation.HEADER,
        ParameterLocation.COOKIE,
    ):
        schema = _build_location_schema(operation, location)
        if schema is None:
            continue
        variants = extra_data_source.get_captured_variants(
            operation=operation,
            location=location,
            schema=schema,
        )
        if variants:
            container = location.container_name
            per_location.append([{container: variant} for variant in variants])

    if not per_location:
        return []

    # Round-robin across locations: each slot gets pool values from all locations merged.
    n = max(len(loc) for loc in per_location)
    combos: list[dict[str, Any]] = []
    for i in range(n):
        merged: dict[str, Any] = {}
        for loc_variants in per_location:
            merged.update(loc_variants[i % len(loc_variants)])
        combos.append(merged)
    return combos


def _build_location_schema(
    operation: APIOperation,
    location: ParameterLocation,
) -> dict[str, Any] | None:
    """Build a minimal JSON schema covering all parameters at a given location."""
    properties = {
        p.name: (p.definition.get("schema") or {}) for p in operation.iter_parameters() if p.location == location
    }
    return {"type": "object", "properties": properties} if properties else None


def get_strategies_from_examples(
    operation: APIOperation[OpenApiParameter, OpenApiResponses, OpenApiSecurityParameters],
    extra_data_source: OpenApiExtraDataSource | None = None,
    fill_missing_from_pool: bool = False,
    **kwargs: Any,
) -> list[SearchStrategy[Case]]:
    """Build strategies from schema examples, augmented with pool values where available."""
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
    schema_examples = list(extract_top_level(operation))
    # Add examples from parameter's schemas
    schema_examples.extend(extract_from_schemas(operation))
    schema_combos = list(produce_combinations(schema_examples))

    pool_combos = _get_pool_combos(operation, extra_data_source) if extra_data_source is not None else []

    if schema_combos and pool_combos:
        # Round-robin merge: schema as base, pool wins for overlapping keys.
        n = max(len(schema_combos), len(pool_combos))
        pool_augmented = [
            merge_kwargs(
                {k: dict(v) if isinstance(v, dict) else v for k, v in schema_combos[i % len(schema_combos)].items()},
                pool_combos[i % len(pool_combos)],
            )
            for i in range(n)
        ]
        # Keep original schema combos; append pool-augmented ones that differ.
        schema_combo_keys = {jsonschema_rs.canonical.json.to_string(c) for c in schema_combos}
        all_combos = [
            *schema_combos,
            *(c for c in pool_augmented if jsonschema_rs.canonical.json.to_string(c) not in schema_combo_keys),
        ]
    elif schema_combos:
        all_combos = schema_combos
    elif pool_combos and fill_missing_from_pool:
        all_combos = [{k: dict(v) if isinstance(v, dict) else v for k, v in combo.items()} for combo in pool_combos]
    else:
        all_combos = []

    return [
        openapi_cases(operation=operation, phase=TestPhase.EXAMPLES, **merge_kwargs(combo, kwargs)).map(
            serialize_components
        )
        for combo in all_combos
    ]


def extract_top_level(
    operation: APIOperation[OpenApiParameter, OpenApiResponses, OpenApiSecurityParameters],
) -> Generator[Example, None, None]:
    """Extract top-level parameter examples from `examples` & `example` fields."""
    from .schemas import OpenApiSchema

    assert isinstance(operation.schema, OpenApiSchema)

    merge_ref_siblings = operation.schema.adapter.ref_siblings
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
                        schema=schema,
                        resolver=resolver,
                        reference_path=reference_path,
                        merge_ref_siblings=merge_ref_siblings,
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
                schema=schema,
                resolver=resolver,
                reference_path=reference_path,
                merge_ref_siblings=merge_ref_siblings,
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
                        schema=schema,
                        resolver=resolver,
                        reference_path=reference_path,
                        merge_ref_siblings=merge_ref_siblings,
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
                schema=schema,
                resolver=resolver,
                reference_path=reference_path,
                merge_ref_siblings=merge_ref_siblings,
            ):
                if isinstance(expanded_schema, dict) and body.adapter.examples_container_keyword in expanded_schema:
                    for value in expanded_schema[body.adapter.examples_container_keyword]:
                        yield BodyExample(value=value, media_type=body.media_type)


@overload
def _resolve_bundled(
    schema: dict[str, Any], resolver: RefResolver, reference_path: tuple[str, ...], *, merge_ref_siblings: bool
) -> tuple[dict[str, Any], tuple[str, ...]]: ...


@overload
def _resolve_bundled(
    schema: bool, resolver: RefResolver, reference_path: tuple[str, ...], *, merge_ref_siblings: bool
) -> tuple[bool, tuple[str, ...]]: ...


def _resolve_bundled(
    schema: dict[str, Any] | bool,
    resolver: RefResolver,
    reference_path: tuple[str, ...],
    *,
    merge_ref_siblings: bool,
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
                cycle_path = list(reference_path[reference_path.index(reference) :])
                raise InfiniteRecursiveReference(reference, cycle_path)

            new_path = reference_path + (reference,)

            try:
                _, resolved_schema = resolver.resolve(reference)
            except RefResolutionError as exc:
                raise UnresolvableReference(reference) from exc

            # In OAS 3.1 (JSON Schema draft 2020-12), sibling keywords alongside $ref
            # are valid and apply independently. Merge them into the resolved schema so
            # constraints like minLength or explicit examples are not silently dropped.
            if merge_ref_siblings and isinstance(resolved_schema, dict):
                siblings = {k: v for k, v in schema.items() if k != "$ref"}
                if siblings:
                    resolved_schema = {**resolved_schema, **siblings}

            return resolved_schema, new_path

    return schema, reference_path


def _expand_subschemas(
    *,
    schema: dict[str, Any] | bool,
    resolver: RefResolver,
    reference_path: tuple[str, ...],
    merge_ref_siblings: bool,
) -> Generator[tuple[dict[str, Any] | bool, tuple[str, ...]], None, None]:
    """Expand schema and all its subschemas."""
    try:
        schema, current_path = _resolve_bundled(schema, resolver, reference_path, merge_ref_siblings=merge_ref_siblings)
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
                subschema, expanded_path = _resolve_bundled(
                    subschema, resolver, current_path, merge_ref_siblings=merge_ref_siblings
                )
            except InfiniteRecursiveReference:
                return
            # Clone after resolving to avoid mutating the original schema when merging
            if isinstance(subschema, dict):
                subschema = deepclone(subschema)

            for sub in schema["allOf"][1:]:
                if isinstance(sub, dict):
                    try:
                        sub, _ = _resolve_bundled(sub, resolver, current_path, merge_ref_siblings=merge_ref_siblings)
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


def _unpack_example_object(example: dict[str, Any], schema: OpenApiSchema) -> Generator[Any, None, None]:
    """Extract the value from a single OAS3 Example Object."""
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


def extract_inner_examples(examples: dict[str, Any] | list, schema: OpenApiSchema) -> Generator[Any, None, None]:
    """Extract exact examples values from the `examples` dictionary."""
    if isinstance(examples, dict):
        for example in examples.values():
            if isinstance(example, dict):
                yield from _unpack_example_object(example, schema)
    elif isinstance(examples, list):
        for example in examples:
            if isinstance(example, dict):
                yield from _unpack_example_object(example, schema)
            else:
                yield example


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
    from .schemas import OpenApiSchema

    assert isinstance(operation.schema, OpenApiSchema)
    merge_ref_siblings = operation.schema.adapter.ref_siblings
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
            merge_ref_siblings=merge_ref_siblings,
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
                merge_ref_siblings=merge_ref_siblings,
            ):
                yield BodyExample(value=value, media_type=body.media_type)


def _yield_examples_from_properties(
    *,
    operation: APIOperation,
    properties: dict[str, Any],
    example_keyword: str,
    examples_container_keyword: str,
    resolver: RefResolver,
    current_path: tuple[str, ...],
    bundle_storage: dict[str, Any] | None,
    merge_ref_siblings: bool,
) -> Generator[Any, None, None]:
    variants: dict[str, list[Any]] = {}
    to_generate: dict[str, Any] = {}

    for name, subschema in properties.items():
        values: list[Any] = []
        for expanded_schema, expanded_path in _expand_subschemas(
            schema=subschema,
            resolver=resolver,
            reference_path=current_path,
            merge_ref_siblings=merge_ref_siblings,
        ):
            if isinstance(expanded_schema, bool):
                to_generate[name] = expanded_schema
                continue

            if example_keyword in expanded_schema:
                values.append(expanded_schema[example_keyword])

            if examples_container_keyword in expanded_schema and isinstance(
                expanded_schema[examples_container_keyword], list
            ):
                values.extend(expanded_schema[examples_container_keyword])

            values.extend(
                extract_from_schema(
                    operation=operation,
                    schema=expanded_schema,
                    example_keyword=example_keyword,
                    examples_container_keyword=examples_container_keyword,
                    resolver=resolver,
                    reference_path=expanded_path,
                    bundle_storage=bundle_storage,
                    merge_ref_siblings=merge_ref_siblings,
                )
            )

            if not values:
                to_generate[name] = expanded_schema
                continue

            variants[name] = values

    if variants:
        config = operation.schema.config.generation_for(operation=operation, phase="examples")
        for name, subschema in to_generate.items():
            if name in variants:
                continue
            if bundle_storage is not None:
                subschema = dict(subschema)
                subschema[BUNDLE_STORAGE_KEY] = bundle_storage
            generated = _generate_single_example(subschema, config)
            variants[name] = [generated]

        total_combos = max(len(v) for v in variants.values())
        for idx in range(total_combos):
            yield {
                name: next(islice(cycle(property_variants), idx, None)) for name, property_variants in variants.items()
            }


def _yield_examples_per_branch(
    *,
    operation: APIOperation,
    parent_properties: dict[str, Any],
    branches: list[dict[str, Any]],
    example_keyword: str,
    examples_container_keyword: str,
    resolver: RefResolver,
    current_path: tuple[str, ...],
    bundle_storage: dict[str, Any] | None,
    merge_ref_siblings: bool,
) -> Generator[Any, None, None]:
    # Identify which properties are claimed by at least one branch
    branch_prop_sets: list[set[str]] = []
    for branch in branches:
        props = set(branch.get("properties", {}).keys())
        reqs = set(branch.get("required", []))
        branch_prop_sets.append(props | reqs)

    all_branch_props: set[str] = set().union(*branch_prop_sets)

    for branch_idx, branch in enumerate(branches):
        branch_claimed = branch_prop_sets[branch_idx]
        branch_own = branch.get("properties", {})

        # Active: parent properties shared (not claimed by any branch) OR claimed by this branch
        active: dict[str, Any] = {
            name: sub
            for name, sub in parent_properties.items()
            if name not in all_branch_props or name in branch_claimed
        }
        # Add branch-only properties (defined in the branch, not in parent)
        for name, sub in branch_own.items():
            if name not in parent_properties:
                active[name] = sub

        yield from _yield_examples_from_properties(
            operation=operation,
            properties=active,
            example_keyword=example_keyword,
            examples_container_keyword=examples_container_keyword,
            resolver=resolver,
            current_path=current_path,
            bundle_storage=bundle_storage,
            merge_ref_siblings=merge_ref_siblings,
        )


def extract_from_schema(
    *,
    operation: APIOperation[OpenApiParameter, OpenApiResponses, OpenApiSecurityParameters],
    schema: dict[str, Any],
    example_keyword: str,
    examples_container_keyword: str,
    resolver: RefResolver,
    reference_path: tuple[str, ...],
    bundle_storage: dict[str, Any] | None,
    merge_ref_siblings: bool,
) -> Generator[Any, None, None]:
    """Extract all examples from a single schema definition."""
    # This implementation supports only `properties` and `items`
    try:
        schema, current_path = _resolve_bundled(schema, resolver, reference_path, merge_ref_siblings=merge_ref_siblings)
    except InfiniteRecursiveReference:
        return

    # If schema has allOf, we need to get merged properties from allOf items
    # This handles cases where parent has properties alongside allOf
    properties_to_process = schema.get("properties", {})

    if "allOf" in schema and "properties" in schema:
        # Get the merged allOf schema which includes properties from all allOf items
        for expanded_schema, _ in _expand_subschemas(
            schema=schema, resolver=resolver, reference_path=current_path, merge_ref_siblings=merge_ref_siblings
        ):
            if expanded_schema is not schema and isinstance(expanded_schema, dict):
                # This is the merged allOf result with combined properties
                if "properties" in expanded_schema:
                    properties_to_process = expanded_schema["properties"]
                break

    if properties_to_process:
        # Detect top-level oneOf/anyOf branches for per-branch generation
        branches: list[dict[str, Any]] | None = None
        for keyword in ("oneOf", "anyOf"):
            raw = schema.get(keyword)
            if raw:
                branches = [b for b in raw if isinstance(b, dict)]
                break

        if branches:
            yield from _yield_examples_per_branch(
                operation=operation,
                parent_properties=properties_to_process,
                branches=branches,
                example_keyword=example_keyword,
                examples_container_keyword=examples_container_keyword,
                resolver=resolver,
                current_path=current_path,
                bundle_storage=bundle_storage,
                merge_ref_siblings=merge_ref_siblings,
            )
        else:
            yield from _yield_examples_from_properties(
                operation=operation,
                properties=properties_to_process,
                example_keyword=example_keyword,
                examples_container_keyword=examples_container_keyword,
                resolver=resolver,
                current_path=current_path,
                bundle_storage=bundle_storage,
                merge_ref_siblings=merge_ref_siblings,
            )

    elif "items" in schema and isinstance(schema["items"], dict):
        # Each inner value should be wrapped in an array, respecting minItems
        min_items = schema.get("minItems", 1)
        length = max(min_items, 1)
        for value in extract_from_schema(
            operation=operation,
            schema=schema["items"],
            example_keyword=example_keyword,
            examples_container_keyword=examples_container_keyword,
            resolver=resolver,
            reference_path=current_path,
            bundle_storage=bundle_storage,
            merge_ref_siblings=merge_ref_siblings,
        ):
            yield [value] * length


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
