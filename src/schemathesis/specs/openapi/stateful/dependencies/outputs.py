from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Iterator, Mapping, cast

from schemathesis.core.errors import InfiniteRecursiveReference
from schemathesis.specs.openapi.adapter.parameters import resource_name_from_ref
from schemathesis.specs.openapi.adapter.references import maybe_resolve
from schemathesis.specs.openapi.stateful.dependencies import naming
from schemathesis.specs.openapi.stateful.dependencies.models import (
    Cardinality,
    DefinitionSource,
    OutputSlot,
    ResourceDefinition,
    ResourceMap,
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
    from schemathesis.specs.openapi.adapter.responses import OpenApiResponse
    from schemathesis.specs.openapi.schemas import APIOperation


def extract_outputs(
    *,
    operation: APIOperation,
    resources: ResourceMap,
    updated_resources: set[str],
    resolver: RefResolver,
) -> Iterator[OutputSlot]:
    """Extract resources from API operation's responses."""
    # Check all successful responses (2xx)
    for response in operation.responses.iter_successful_responses():
        # Get resource from response
        data = _resource_from_response(
            path=operation.path,
            response=response,
            resources=resources,
            updated_resources=updated_resources,
            resolver=resolver,
        )
        if data is None:
            continue

        yield OutputSlot(
            resource=data.resource,
            pointer=data.pointer,
            cardinality=data.cardinality,
            status_code=response.status_code,
        )


@dataclass
class ExtractedResource:
    """How a resource was extracted from a response."""

    resource: ResourceDefinition
    cardinality: Cardinality
    pointer: str

    __slots__ = ("resource", "cardinality", "pointer")


def _resource_from_response(
    *,
    path: str,
    response: OpenApiResponse,
    resources: ResourceMap,
    updated_resources: set[str],
    resolver: RefResolver,
) -> ExtractedResource | None:
    # TODO: It should iterate over possible resources instead
    schema = response.get_raw_schema()

    if isinstance(schema, bool):
        return _resource_from_boolean_schema(path=path, resources=resources)
    elif not isinstance(schema, dict):
        # Ignore invalid schemas
        return None

    parent_ref = schema.get("$ref")
    _, resolved = maybe_resolve(schema, resolver, "")

    resolved = try_unwrap_composition(resolved, resolver)

    if "allOf" in resolved:
        try:
            canonicalized = canonicalize(cast(dict, resolved), resolver)
        except InfiniteRecursiveReference:
            canonicalized = resolved
    else:
        canonicalized = resolved

    # Detect wrapper pattern and navigate to data
    unwrapped = unwrap_schema(schema=canonicalized, path=path, resolver=resolver)

    # The presence of `allOf` removes original references, so we need to lookup the name
    if unwrapped.pointer != ROOT_POINTER and "allOf" in resolved:
        keys = unwrapped.pointer.split("/")[:0:-1]
        for item in resolved["allOf"]:
            _, resolved_item = maybe_resolve(item, resolver, "")
            properties = resolved_item.get("properties", {})
            if keys[-1] in properties:
                key = keys.pop()
                subschema = properties[key]
                unwrapped.ref = subschema.get("$ref") or subschema.get("items", {}).get("$ref") or unwrapped.ref
                break

    # Extract resource and determine cardinality
    result = _extract_resource_and_cardinality(
        schema=unwrapped.schema,
        path=path,
        resources=resources,
        updated_resources=updated_resources,
        resolver=resolver,
        parent_ref=unwrapped.ref or parent_ref,
    )

    if result is None:
        return None

    resource, cardinality = result
    return ExtractedResource(resource=resource, cardinality=cardinality, pointer=unwrapped.pointer)


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


# TODO: Move to `resources.py?`
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

        properties = resolved.get("properties")
        if properties:
            fields = list(properties)
            source = DefinitionSource.SCHEMA_WITH_PROPERTIES
        else:
            fields = []
            source = DefinitionSource.SCHEMA_WITHOUT_PROPERTIES
        if resource is not None:
            if resource.source < source:
                resource.source = source
                resource.fields = fields
                updated_resources.add(resource_name)
        else:
            resource = ResourceDefinition(name=resource_name, fields=fields, source=source)
            resources[resource_name] = resource

    return resource
