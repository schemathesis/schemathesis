from __future__ import annotations

from collections.abc import Iterator
from typing import TYPE_CHECKING, Any

from schemathesis.core import media_types
from schemathesis.core.errors import MalformedMediaType
from schemathesis.core.jsonschema.bundler import BUNDLE_STORAGE_KEY
from schemathesis.core.jsonschema.types import get_type
from schemathesis.core.parameters import ParameterLocation
from schemathesis.specs.openapi.adapter.parameters import resource_name_from_ref
from schemathesis.specs.openapi.stateful.dependencies import naming
from schemathesis.specs.openapi.stateful.dependencies.models import (
    CanonicalizationCache,
    DefinitionSource,
    InputSlot,
    OperationMap,
    OutputSlot,
    ResourceDefinition,
    ResourceMap,
)
from schemathesis.specs.openapi.stateful.dependencies.resources import extract_resources_from_responses

if TYPE_CHECKING:
    from schemathesis.core.compat import RefResolver
    from schemathesis.specs.openapi.adapter.parameters import OpenApiBody
    from schemathesis.specs.openapi.schemas import APIOperation


def extract_inputs(
    *,
    operation: APIOperation,
    resources: ResourceMap,
    updated_resources: set[str],
    resolver: RefResolver,
    canonicalization_cache: CanonicalizationCache,
) -> Iterator[InputSlot]:
    """Extract resource dependencies for an API operation from its input parameters.

    Connects each parameter (e.g., `userId`) to its resource definition (`User`),
    creating placeholder resources if not yet discovered from their schemas.
    """
    known_dependencies = set()
    for param in operation.iter_parameters():
        input_slot = _resolve_parameter_dependency(
            parameter_name=param.name,
            parameter_location=param.location,
            operation=operation,
            resources=resources,
            updated_resources=updated_resources,
            resolver=resolver,
            canonicalization_cache=canonicalization_cache,
        )
        if input_slot is not None:
            if input_slot.resource.source >= DefinitionSource.SCHEMA_WITH_PROPERTIES:
                known_dependencies.add(input_slot.resource.name)
            yield input_slot

    for body in operation.body:
        try:
            if media_types.is_json(body.media_type):
                yield from _resolve_body_dependencies(
                    body=body, operation=operation, resources=resources, known_dependencies=known_dependencies
                )
        except MalformedMediaType:
            continue


def _resolve_parameter_dependency(
    *,
    parameter_name: str,
    parameter_location: ParameterLocation,
    operation: APIOperation,
    resources: ResourceMap,
    updated_resources: set[str],
    resolver: RefResolver,
    canonicalization_cache: CanonicalizationCache,
) -> InputSlot | None:
    """Connect a parameter to its resource definition, creating placeholder if needed.

    Strategy:
    1. Infer resource name from parameter (`userId` -> `User`)
    2. Use existing resource if high-quality definition exists
    3. Try discovering from operation's response schemas
    4. Fall back to creating placeholder with a single field
    """
    resource_name = naming.from_parameter(parameter=parameter_name, path=operation.path)

    if resource_name is None:
        return None

    resource = resources.get(resource_name)

    # Upgrade low-quality resource definitions (e.g., from parameter inference)
    # by searching this operation's responses for actual schema
    if resource is None or resource.source < DefinitionSource.SCHEMA_WITH_PROPERTIES:
        resource = _find_resource_in_responses(
            operation=operation,
            resource_name=resource_name,
            resources=resources,
            updated_resources=updated_resources,
            resolver=resolver,
            canonicalization_cache=canonicalization_cache,
        )
        if resource is not None:
            resources[resource_name] = resource

    # Determine resource and its field
    if resource is None:
        # No schema found - create placeholder resource with inferred field
        #
        # Example: `DELETE /users/{userId}` with no response body -> `User` resource with "userId" field
        #
        # Later operations with schemas will upgrade this placeholder
        if resource_name in resources:
            # Resource exists but was empty - update with parameter field
            resources[resource_name].fields = [parameter_name]
            resources[resource_name].source = DefinitionSource.PARAMETER_INFERENCE
            updated_resources.add(resource_name)
            resource = resources[resource_name]
        else:
            resource = ResourceDefinition.inferred_from_parameter(
                name=resource_name,
                parameter_name=parameter_name,
            )
            resources[resource_name] = resource
        field = parameter_name
    else:
        # Match parameter to resource field (`userId` → `id`, `Id` → `ChannelId`, etc.)
        field = (
            naming.find_matching_field(
                parameter=parameter_name,
                resource=resource_name,
                fields=resource.fields,
            )
            or "id"
        )

    return InputSlot(
        resource=resource,
        resource_field=field,
        parameter_name=parameter_name,
        parameter_location=parameter_location,
    )


def _find_resource_in_responses(
    *,
    operation: APIOperation,
    resource_name: str,
    resources: ResourceMap,
    updated_resources: set[str],
    resolver: RefResolver,
    canonicalization_cache: CanonicalizationCache,
) -> ResourceDefinition | None:
    """Search operation's successful responses for a specific resource definition.

    Used when a parameter references a resource not yet discovered. Scans this
    operation's response schemas hoping to find the resource definition.
    """
    for _, extracted in extract_resources_from_responses(
        operation=operation,
        resources=resources,
        updated_resources=updated_resources,
        resolver=resolver,
        canonicalization_cache=canonicalization_cache,
    ):
        if extracted.resource.name == resource_name:
            return extracted.resource

    return None


GENERIC_FIELD_NAMES = frozenset(
    {
        "body",
        "text",
        "content",
        "message",
        "description",
    }
)


def _maybe_resolve_bundled(root: dict[str, Any], schema: dict[str, Any]) -> dict[str, Any]:
    # Right now, the body schema comes bundled to dependency analysis
    if BUNDLE_STORAGE_KEY in root and "$ref" in schema:
        key = schema["$ref"].split("/")[-1]
        return root[BUNDLE_STORAGE_KEY][key]
    return schema


def _resolve_body_dependencies(
    *,
    body: OpenApiBody,
    operation: APIOperation,
    resources: ResourceMap,
    known_dependencies: set[str],
) -> Iterator[InputSlot]:
    schema = body.raw_schema
    if not isinstance(schema, dict):
        return

    resolved = _maybe_resolve_bundled(schema, schema)

    # For `items`, we'll inject an array with extracted resource
    items = resolved.get("items", {})
    if items is not None:
        resource_name = naming.from_path(operation.path)

        if "$ref" in items:
            schema_key = items["$ref"].split("/")[-1]
            original_ref = body.name_to_uri[schema_key]
            resource_name = resource_name_from_ref(original_ref)
            resource = resources.get(resource_name)
            if resource is None:
                resource = ResourceDefinition.inferred_from_parameter(name=resource_name, parameter_name=None)
                resources[resource_name] = resource
                field = None
            else:
                field = None
            yield InputSlot(
                resource=resource,
                resource_field=field,
                parameter_name=0,
                parameter_location=ParameterLocation.BODY,
            )

    # Inspect each property that could be a part of some other resource
    properties = resolved.get("properties", {})
    required = resolved.get("required", [])
    path = operation.path
    for property_name, subschema in properties.items():
        resource_name = naming.from_parameter(property_name, path)
        if resource_name is not None:
            resource = resources.get(resource_name)
            if resource is None:
                resource = ResourceDefinition.inferred_from_parameter(
                    name=resource_name,
                    parameter_name=property_name,
                )
                resources[resource_name] = resource
                field = property_name
            else:
                field = (
                    naming.find_matching_field(
                        parameter=property_name,
                        resource=resource_name,
                        fields=resource.fields,
                    )
                    or "id"
                )
            yield InputSlot(
                resource=resource,
                resource_field=field,
                parameter_name=property_name,
                parameter_location=ParameterLocation.BODY,
            )
            continue

        # Skip generic property names & optional fields (at least for now)
        if property_name in GENERIC_FIELD_NAMES or property_name not in required:
            continue

        # Find candidate resources among known dependencies that actually have this field
        candidates = [
            resources[dep] for dep in known_dependencies if dep in resources and property_name in resources[dep].fields
        ]

        # Skip ambiguous cases when multiple resources have same field name
        if len(candidates) != 1:
            continue

        resource = candidates[0]
        # Ensure the target field supports the same type
        if not resource.types[property_name] & set(get_type(subschema)):
            continue

        yield InputSlot(
            resource=resource,
            resource_field=property_name,
            parameter_name=property_name,
            parameter_location=ParameterLocation.BODY,
        )


def update_input_field_bindings(resource_name: str, operations: OperationMap) -> None:
    """Update input slots field bindings after resource definition was upgraded.

    When a resource's fields change (e.g., `User` upgraded from `["userId"]` to `["id", "email"]`),
    existing input slots may reference stale field names. This re-evaluates field matching
    for all operations using this resource.

    Example:
        `DELETE /users/{userId}` created `InputSlot(resource_field="userId")`
        `POST /users` revealed actual fields `["id", "email"]`
        This updates DELETE's `InputSlot` to use `resource_field="id"`

    """
    # Re-evaluate field matching for all operations referencing this resource
    for operation in operations.values():
        for input_slot in operation.inputs:
            # Skip inputs not using this resource
            if input_slot.resource.name != resource_name or isinstance(input_slot.parameter_name, int):
                continue

            # Re-match parameter to upgraded resource fields
            new_field = naming.find_matching_field(
                parameter=input_slot.parameter_name,
                resource=resource_name,
                fields=input_slot.resource.fields,
            )
            if new_field is not None:
                input_slot.resource_field = new_field


def merge_related_resources(operations: OperationMap, resources: ResourceMap) -> None:
    """Merge parameter-inferred resources with schema-defined resources from related operations."""
    candidates = find_producer_consumer_candidates(operations)

    for producer_name, consumer_name in candidates:
        producer = operations[producer_name]
        consumer = operations[consumer_name]

        # Try to upgrade each input slot
        for input_slot in consumer.inputs:
            result = try_merge_input_resource(input_slot, producer.outputs, resources)

            if result is not None:
                new_resource_name, new_field_name = result
                # Update input slot to use the better resource definition
                input_slot.resource = resources[new_resource_name]
                input_slot.resource_field = new_field_name


def try_merge_input_resource(
    input_slot: InputSlot,
    producer_outputs: list[OutputSlot],
    resources: ResourceMap,
) -> tuple[str, str] | None:
    """Try to upgrade an input's resource to a producer's resource."""
    consumer_resource = input_slot.resource

    # Only upgrade parameter-inferred resources (low confidence)
    if consumer_resource.source != DefinitionSource.PARAMETER_INFERENCE:
        return None

    # Try each producer output
    for output in producer_outputs:
        producer_resource = resources[output.resource.name]

        # Only merge to schema-defined resources (high confidence)
        if producer_resource.source != DefinitionSource.SCHEMA_WITH_PROPERTIES:
            continue

        # Try to match the input parameter to producer's fields
        param_name = input_slot.parameter_name
        if not isinstance(param_name, str):
            continue

        for resource_name in (input_slot.resource.name, producer_resource.name):
            matched_field = naming.find_matching_field(
                parameter=param_name,
                resource=resource_name,
                fields=producer_resource.fields,
            )

            if matched_field is not None:
                return (producer_resource.name, matched_field)

    return None


def find_producer_consumer_candidates(operations: OperationMap) -> list[tuple[str, str]]:
    """Find operation pairs that might produce/consume the same resource via REST patterns."""
    candidates = []

    # Group by base path to reduce comparisons
    paths: dict[str, list[str]] = {}
    for name, node in operations.items():
        base = _extract_base_path(node.path)
        paths.setdefault(base, []).append(name)

    # Within each path group, find POST/PUT → GET/DELETE/PATCH patterns
    for names in paths.values():
        for producer_name in names:
            producer = operations[producer_name]
            # Producer must create/update and return data
            if producer.method not in ("post", "put") or not producer.outputs:
                continue

            for consumer_name in names:
                consumer = operations[consumer_name]
                # Consumer must have path parameters
                if not consumer.inputs:
                    continue
                # Paths must be related (collection + item pattern)
                if _is_collection_item_pattern(producer.path, consumer.path):
                    candidates.append((producer_name, consumer_name))

    return candidates


def _extract_base_path(path: str) -> str:
    """Extract collection path: /blog/posts/{id} -> /blog/posts."""
    parts = [p for p in path.split("/") if not p.startswith("{")]
    return "/".join(parts).rstrip("/")


def _is_collection_item_pattern(collection_path: str, item_path: str) -> bool:
    """Check if paths follow REST collection/item pattern."""
    # /blog/posts + /blog/posts/{postId}
    normalized_collection = collection_path.rstrip("/")
    normalized_item = item_path.rstrip("/")

    # Must start with collection path
    if not normalized_item.startswith(normalized_collection + "/"):
        return False

    # Extract the segment after collection path
    remainder = normalized_item[len(normalized_collection) + 1 :]

    # Must be a single path parameter: {paramName} with no slashes
    return (
        remainder.startswith("{")
        and remainder.endswith("}")
        and len(remainder) > 2  # Not empty {}
        and "/" not in remainder
    )
