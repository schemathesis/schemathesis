from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

from schemathesis.core.errors import InfiniteRecursiveReference
from schemathesis.core.jsonschema.bundler import BundleError
from schemathesis.core.jsonschema.types import get_type
from schemathesis.specs.openapi.adapter.parameters import resource_name_from_ref
from schemathesis.specs.openapi.adapter.references import maybe_resolve
from schemathesis.specs.openapi.stateful.dependencies import naming
from schemathesis.specs.openapi.stateful.dependencies.models import (
    CanonicalizationCache,
    Cardinality,
    DefinitionSource,
    OperationMap,
    ResourceDefinition,
    ResourceMap,
    extend_pointer,
)
from schemathesis.specs.openapi.stateful.dependencies.naming import from_path
from schemathesis.specs.openapi.stateful.dependencies.schemas import (
    ROOT_POINTER,
    canonicalize,
    try_unwrap_composition,
    unwrap_schema,
)

if TYPE_CHECKING:
    from schemathesis.core.compat import RefResolver
    from schemathesis.schemas import APIOperation
    from schemathesis.specs.openapi.adapter.responses import OpenApiResponse


@dataclass
class ExtractedResource:
    """How a resource was extracted from a response."""

    resource: ResourceDefinition
    # Where in response body (JSON pointer)
    pointer: str
    # Is this a single resource or an array?
    cardinality: Cardinality

    __slots__ = ("resource", "pointer", "cardinality")


def extract_resources_from_responses(
    *,
    operation: APIOperation,
    resources: ResourceMap,
    updated_resources: set[str],
    resolver: RefResolver,
    canonicalization_cache: CanonicalizationCache,
) -> Iterator[tuple[OpenApiResponse, ExtractedResource]]:
    """Extract resource definitions from operation's successful responses.

    Processes each 2xx response, unwrapping pagination wrappers,
    handling `allOf` / `oneOf` / `anyOf` composition, and determining cardinality.
    Updates the global resource registry as resources are discovered.
    """
    for response in operation.responses.iter_successful_responses():
        for extracted in iter_resources_from_response(
            path=operation.path,
            response=response,
            resources=resources,
            updated_resources=updated_resources,
            resolver=resolver,
            canonicalization_cache=canonicalization_cache,
        ):
            yield response, extracted


def iter_resources_from_response(
    *,
    path: str,
    response: OpenApiResponse,
    resources: ResourceMap,
    updated_resources: set[str],
    resolver: RefResolver,
    canonicalization_cache: CanonicalizationCache,
) -> Iterator[ExtractedResource]:
    schema = response.get_raw_schema()

    if isinstance(schema, bool):
        boolean_resource = _resource_from_boolean_schema(path=path, resources=resources)
        if boolean_resource is not None:
            yield boolean_resource
        return None
    elif not isinstance(schema, dict):
        # Ignore invalid schemas
        return None

    # Push the response's scope so all nested $refs are resolved relative to the response's location
    resolver.push_scope(response.scope)
    try:
        parent_ref = schema.get("$ref")
        _, resolved = maybe_resolve(schema, resolver, "")

        # Sometimes data is wrapped in a single wrapper field
        # Common patterns: {data: {...}}, {result: {...}}, {response: {...}}
        pointer = None
        properties = resolved.get("properties", {})
        if properties and len(properties) == 1:
            wrapper_field = list(properties)[0]
            # Check if it's a known wrapper field name
            common_wrappers = {"data", "result", "response", "payload"}
            if wrapper_field.lower() in common_wrappers:
                pointer = f"/{wrapper_field}"
                resolved = properties[wrapper_field]

        resolved = try_unwrap_composition(resolved, resolver)

        if "allOf" in resolved:
            if parent_ref is not None and parent_ref in canonicalization_cache:
                canonicalized = canonicalization_cache[parent_ref]
            else:
                try:
                    canonicalized = canonicalize(cast(dict, resolved), resolver)
                except (InfiniteRecursiveReference, BundleError):
                    canonicalized = resolved
                if parent_ref is not None:
                    canonicalization_cache[parent_ref] = canonicalized
        else:
            canonicalized = resolved

        # Detect wrapper pattern and navigate to data
        unwrapped = unwrap_schema(schema=canonicalized, path=path, parent_ref=parent_ref, resolver=resolver)

        # Recover $ref lost during allOf canonicalization
        recovered_ref = None
        if unwrapped.pointer != ROOT_POINTER and "allOf" in resolved:
            recovered_ref = _recover_ref_from_allof(
                branches=resolved["allOf"],
                pointer=unwrapped.pointer,
                resolver=resolver,
            )

        # Extract resource and determine cardinality
        result = _extract_resource_and_cardinality(
            schema=unwrapped.schema,
            path=path,
            resources=resources,
            updated_resources=updated_resources,
            resolver=resolver,
            parent_ref=recovered_ref or unwrapped.ref or parent_ref,
        )

        if result is not None:
            resource, cardinality = result
            if pointer:
                if unwrapped.pointer != ROOT_POINTER:
                    pointer += unwrapped.pointer
            else:
                pointer = unwrapped.pointer
            yield ExtractedResource(resource=resource, cardinality=cardinality, pointer=pointer)
            # Look for sub-resources
            properties = unwrapped.schema.get("properties")
            if isinstance(properties, dict):
                for field, subschema in properties.items():
                    if isinstance(subschema, dict):
                        reference = subschema.get("$ref")
                        if isinstance(reference, str):
                            result = _extract_resource_and_cardinality(
                                schema=subschema,
                                path=path,
                                resources=resources,
                                updated_resources=updated_resources,
                                resolver=resolver,
                                parent_ref=reference,
                            )
                            if result is not None:
                                subresource, cardinality = result
                                subresource_pointer = extend_pointer(pointer, field, cardinality=cardinality)
                                yield ExtractedResource(
                                    resource=subresource, cardinality=cardinality, pointer=subresource_pointer
                                )
    finally:
        resolver.pop_scope()


def _recover_ref_from_allof(*, branches: list[dict], pointer: str, resolver: RefResolver) -> str | None:
    """Recover original $ref from allOf branches after canonicalization.

    Canonicalization inlines all $refs, losing resource name information.
    This searches original allOf branches to find which one defined the
    property at the given pointer.
    """
    # Parse pointer segments (e.g., "/data" -> ["data"])
    segments = [s for s in pointer.strip("/").split("/") if s]

    # Search each branch for the property
    for branch in branches:
        _, resolved_branch = maybe_resolve(branch, resolver, "")
        properties = resolved_branch.get("properties", {})

        # Check if this branch defines the target property
        if segments[-1] in properties:
            # Navigate to property in original (unresolved) branch
            original_properties = branch.get("properties", {})
            if segments[-1] in original_properties:
                prop_schema = original_properties[segments[-1]]
                # Extract $ref from property or its items
                return prop_schema.get("$ref") or prop_schema.get("items", {}).get("$ref")

    return None


def _resource_from_boolean_schema(*, path: str, resources: ResourceMap) -> ExtractedResource | None:
    name = from_path(path)
    if name is None:
        return None
    resource = resources.get(name)
    if resource is None:
        resource = ResourceDefinition.without_properties(name)
        resources[name] = resource
    # Do not update existing resource as if it is inferred, it will have at least one field
    return ExtractedResource(resource=resource, cardinality=Cardinality.ONE, pointer=ROOT_POINTER)


def _extract_resource_and_cardinality(
    *,
    schema: Mapping[str, Any],
    path: str,
    resources: ResourceMap,
    updated_resources: set[str],
    resolver: RefResolver,
    parent_ref: str | None = None,
) -> tuple[ResourceDefinition, Cardinality] | None:
    """Extract resource from schema and determine cardinality."""
    # Check if it's an array
    if schema.get("type") == "array" or "items" in schema:
        items = schema.get("items")
        if not isinstance(items, dict):
            return None

        # Resolve items if it's a $ref
        _, resolved_items = maybe_resolve(items, resolver, "")

        # Extract resource from items
        resource = _extract_resource_from_schema(
            schema=resolved_items,
            path=path,
            resources=resources,
            updated_resources=updated_resources,
            resolver=resolver,
            # Prefer items $ref for name
            parent_ref=items.get("$ref") or parent_ref,
        )

        if resource is None:
            return None

        return resource, Cardinality.MANY

    # Single object
    resource = _extract_resource_from_schema(
        schema=schema,
        path=path,
        resources=resources,
        updated_resources=updated_resources,
        resolver=resolver,
        parent_ref=parent_ref,
    )

    if resource is None:
        return None

    return resource, Cardinality.ONE


def _extract_resource_from_schema(
    *,
    schema: Mapping[str, Any],
    path: str,
    resources: ResourceMap,
    updated_resources: set[str],
    resolver: RefResolver,
    parent_ref: str | None = None,
) -> ResourceDefinition | None:
    """Extract resource definition from a schema."""
    resource_name: str | None = None

    ref = schema.get("$ref")
    if ref is not None:
        resource_name = resource_name_from_ref(ref)
    elif parent_ref is not None:
        resource_name = resource_name_from_ref(parent_ref)
    else:
        resource_name = naming.from_path(path)

    if resource_name is None:
        return None

    resource = resources.get(resource_name)

    if resource is None or resource.source < DefinitionSource.SCHEMA_WITH_PROPERTIES:
        _, resolved = maybe_resolve(schema, resolver, "")

        if "type" in resolved and resolved["type"] != "object" and "properties" not in resolved:
            # Skip strings, etc
            return None

        properties = resolved.get("properties")
        if properties:
            fields = sorted(properties)
            types = {}
            for field, subschema in properties.items():
                if isinstance(subschema, dict):
                    _, resolved_subschema = maybe_resolve(subschema, resolver, "")
                else:
                    resolved_subschema = subschema
                types[field] = set(get_type(cast(dict, resolved_subschema)))
            source = DefinitionSource.SCHEMA_WITH_PROPERTIES
        else:
            fields = []
            types = {}
            source = DefinitionSource.SCHEMA_WITHOUT_PROPERTIES
        if resource is not None:
            if resource.source < source:
                resource.source = source
                resource.fields = fields
                resource.types = types
                updated_resources.add(resource_name)
        else:
            resource = ResourceDefinition(name=resource_name, fields=fields, types=types, source=source)
            resources[resource_name] = resource

    return resource


def remove_unused_resources(operations: OperationMap, resources: ResourceMap) -> None:
    """Remove resources that aren't referenced by any operation."""
    # Collect all resource names currently in use
    used_resources = set()
    for operation in operations.values():
        for input_slot in operation.inputs:
            used_resources.add(input_slot.resource.name)
        for output_slot in operation.outputs:
            used_resources.add(output_slot.resource.name)

    unused = set(resources.keys()) - used_resources
    for resource_name in unused:
        del resources[resource_name]
