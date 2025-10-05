from __future__ import annotations

from typing import TYPE_CHECKING, Iterator

from schemathesis.core.parameters import ParameterLocation
from schemathesis.specs.openapi.stateful.dependencies import naming
from schemathesis.specs.openapi.stateful.dependencies.models import (
    CanonicalizationCache,
    DefinitionSource,
    InputSlot,
    OperationMap,
    ResourceDefinition,
    ResourceMap,
)
from schemathesis.specs.openapi.stateful.dependencies.resources import extract_resources_from_responses

if TYPE_CHECKING:
    from schemathesis.core.compat import RefResolver
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
    # Note: Currently limited to path parameters. Query / header / body will be supported in future releases.
    for param in operation.path_parameters:
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
            yield input_slot


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
            if input_slot.resource.name != resource_name:
                continue

            # Re-match parameter to upgraded resource fields
            new_field = naming.find_matching_field(
                parameter=input_slot.parameter_name,
                resource=resource_name,
                fields=input_slot.resource.fields,
            )
            if new_field is not None:
                input_slot.resource_field = new_field
