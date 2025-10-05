from __future__ import annotations

from typing import TYPE_CHECKING, Iterator

from schemathesis.core.parameters import ParameterLocation
from schemathesis.specs.openapi.stateful.dependencies import naming
from schemathesis.specs.openapi.stateful.dependencies.models import (
    DefinitionSource,
    InputSlot,
    OperationMap,
    ResourceDefinition,
    ResourceMap,
)
from schemathesis.specs.openapi.stateful.dependencies.outputs import _resource_from_response

if TYPE_CHECKING:
    from schemathesis.core.compat import RefResolver
    from schemathesis.specs.openapi.schemas import APIOperation


def extract_inputs(
    *,
    operation: APIOperation,
    resources: ResourceMap,
    updated_resources: set[str],
    resolver: RefResolver,
) -> Iterator[InputSlot]:
    """Extract resource dependencies from operation parameters & request body."""
    for param in operation.path_parameters:
        input_slot = _match_parameter_to_resource(
            parameter_name=param.name,
            param_location=param.location,
            operation=operation,
            resources=resources,
            updated_resources=updated_resources,
            resolver=resolver,
        )
        if input_slot is not None:
            yield input_slot
    # TODO: Check other parameters & request body (can the come from the same resource?)


def _match_parameter_to_resource(
    *,
    parameter_name: str,
    param_location: ParameterLocation,
    operation: APIOperation,
    resources: ResourceMap,
    updated_resources: set[str],
    resolver: RefResolver,
) -> InputSlot | None:
    """Try to match a parameter to a known resource."""
    resource_name = naming.from_parameter(parameter=parameter_name, path=operation.path)

    if resource_name is None:
        return None

    resource = resources.get(resource_name)

    # If not found, try to discover from operation responses
    if resource is None or resource.source < DefinitionSource.SCHEMA_WITH_PROPERTIES:
        resource = _try_create_resource_from_response(
            operation=operation,
            resource_name=resource_name,
            resources=resources,
            updated_resources=updated_resources,
            resolver=resolver,
        )
        if resource is not None:
            resources[resource_name] = resource

    if resource is None:
        # Create incomplete resource with only known field
        # If there will be no actual resource created later on, this is the best approximation
        field = parameter_name
        if resource_name in resources:
            resources[resource_name].fields = [parameter_name]
            resources[resource_name].source = DefinitionSource.PARAMETER_INFERENCE
            resource = resources[resource_name]
        else:
            resource = ResourceDefinition.inferred_from_parameter(name=resource_name, parameter_name=parameter_name)
            resources[resource_name] = resource
    else:
        field = (
            naming.find_matching_field(parameter=parameter_name, resource=resource_name, fields=resource.fields) or "id"
        )

    return InputSlot(
        resource=resource,
        resource_field=field,
        parameter_name=parameter_name,
        parameter_location=param_location,
    )


def _try_create_resource_from_response(
    *,
    operation: APIOperation,
    resource_name: str,
    resources: ResourceMap,
    updated_resources: set[str],
    resolver: RefResolver,
) -> ResourceDefinition | None:
    for response in operation.responses.iter_successful_responses():
        result = _resource_from_response(
            path=operation.path,
            response=response,
            resources=resources,
            updated_resources=updated_resources,
            resolver=resolver,
        )
        if result is None:
            continue
        resource = result.resource
        # Check if discovered resource matches what we're looking for
        if resource is not None and resource.name == resource_name:
            return resource

    return None


def propagate_resource_changes(resource: str, operations: OperationMap) -> None:
    # Resources are build incrementally, when a more reliable data source is found
    # In some cases, the set of available fields changes. It happens when the resource is
    # inferred from path parameters first, and then its proper schema is discovered later on
    # In such cases, we need to update the fields used to extract input parameters from resources
    for operation in operations.values():
        for input in operation.inputs:
            if input.resource.name == resource:
                resource_field = naming.find_matching_field(
                    parameter=input.parameter_name,
                    resource=resource,
                    fields=input.resource.fields,
                )
                if resource_field is not None:
                    input.resource_field = resource_field
