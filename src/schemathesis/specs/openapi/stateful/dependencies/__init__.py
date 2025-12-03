"""Dependency detection between API operations for stateful testing.

Infers which operations must run before others by tracking resource creation and consumption across API operations.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Mapping

from schemathesis.core import NOT_SET
from schemathesis.core.compat import RefResolutionError
from schemathesis.core.errors import InvalidSchema
from schemathesis.core.result import Ok
from schemathesis.specs.openapi.adapter.references import maybe_resolve
from schemathesis.specs.openapi.stateful.dependencies.inputs import (
    extract_inputs,
    merge_related_resources,
    update_input_field_bindings,
)
from schemathesis.specs.openapi.stateful.dependencies.models import (
    CanonicalizationCache,
    Cardinality,
    DefinitionSource,
    DependencyGraph,
    InputSlot,
    NormalizedLink,
    OperationMap,
    OperationNode,
    OutputSlot,
    ResourceDefinition,
    ResourceMap,
)
from schemathesis.specs.openapi.stateful.dependencies.outputs import extract_outputs
from schemathesis.specs.openapi.stateful.dependencies.resources import remove_unused_resources

if TYPE_CHECKING:
    from schemathesis.schemas import APIOperation
    from schemathesis.specs.openapi.schemas import OpenApiSchema

__all__ = [
    "analyze",
    "inject_links",
    "DependencyGraph",
    "InputSlot",
    "OutputSlot",
    "Cardinality",
    "ResourceDefinition",
    "DefinitionSource",
]


def analyze(schema: OpenApiSchema) -> DependencyGraph:
    """Build a dependency graph by inferring resource producers and consumers from API operations."""
    operations: OperationMap = {}
    resources: ResourceMap = {}
    # Track resources that got upgraded (e.g., from parameter inference to schema definition)
    # to propagate better field information to existing input slots
    updated_resources: set[str] = set()
    # Cache for expensive canonicalize() calls - same schemas are often processed multiple times
    canonicalization_cache: CanonicalizationCache = {}

    for result in schema.get_all_operations():
        if isinstance(result, Ok):
            operation = result.ok()
            try:
                inputs = extract_inputs(
                    operation=operation,
                    resources=resources,
                    updated_resources=updated_resources,
                    resolver=schema.resolver,
                    canonicalization_cache=canonicalization_cache,
                )
                outputs = extract_outputs(
                    operation=operation,
                    resources=resources,
                    updated_resources=updated_resources,
                    resolver=schema.resolver,
                    canonicalization_cache=canonicalization_cache,
                )
                operations[operation.label] = OperationNode(
                    method=operation.method,
                    path=operation.path,
                    inputs=list(inputs),
                    outputs=list(outputs),
                )
            except RefResolutionError:
                # Skip operations with unresolvable $refs (e.g., unavailable external references or references with typos)
                # These won't participate in dependency detection
                continue

    # Update input slots with improved resource definitions discovered during extraction
    #
    # Example:
    #   - `DELETE /users/{userId}` initially inferred `User.fields=["userId"]`
    #   - then `POST /users` response revealed `User.fields=["id", "email"]`
    for resource in updated_resources:
        update_input_field_bindings(resource, operations)

    # Merge parameter-inferred resources with schema-defined ones
    merge_related_resources(operations, resources)

    # Clean up orphaned resources
    remove_unused_resources(operations, resources)

    return DependencyGraph(operations=operations, resources=resources)


def inject_links(schema: OpenApiSchema) -> int:
    injected = 0
    for response_links in schema.analysis.dependency_graph.iter_links():
        operation = schema.find_operation_by_reference(response_links.producer_operation_ref)
        response = operation.responses.get(response_links.status_code)
        links = response.definition.setdefault(schema.adapter.links_keyword, {})

        # Normalize existing links once
        if links:
            normalized_existing = [_normalize_link(link, schema) for link in links.values()]
        else:
            normalized_existing = []

        for link_name, definition in response_links.links.items():
            inferred_link = definition.to_openapi()

            # Check if duplicate / subsets exists
            if normalized_existing:
                normalized = _normalize_link(inferred_link, schema)
                if any(_is_subset_link(normalized, existing) for existing in normalized_existing):
                    continue

            # Find unique name if collision exists
            final_name = _resolve_link_name_collision(link_name, links)
            links[final_name] = inferred_link
            injected += 1
    return injected


def _normalize_link(link: Mapping[str, Any], schema: OpenApiSchema) -> NormalizedLink:
    """Normalize a link definition for comparison."""
    _, link = maybe_resolve(link, schema.resolver, "")
    operation = _resolve_link_operation(link, schema)

    normalized_params = _normalize_parameter_keys(link.get("parameters", {}), operation)

    return NormalizedLink(
        path=operation.path,
        method=operation.method,
        parameters=normalized_params,
        request_body=link.get("requestBody", {}),
    )


def _normalize_parameter_keys(parameters: dict, operation: APIOperation) -> set[str]:
    """Normalize parameter keys to location.name format."""
    normalized = set()

    for parameter_name in parameters.keys():
        # If already has location prefix, use as-is
        if "." in parameter_name:
            normalized.add(parameter_name)
            continue

        # Find the parameter and prepend location
        for parameter in operation.iter_parameters():
            if parameter.name == parameter_name:
                normalized.add(f"{parameter.location.value}.{parameter_name}")
                break

    return normalized


def _resolve_link_operation(link: Mapping[str, Any], schema: OpenApiSchema) -> APIOperation:
    """Resolve link to operation."""
    if "operationRef" in link:
        return schema.find_operation_by_reference(link["operationRef"])
    if "operationId" in link:
        return schema.find_operation_by_id(link["operationId"])
    raise InvalidSchema(
        "Link definition is missing both 'operationRef' and 'operationId'. "
        "At least one of these fields must be present to identify the target operation."
    )


def _resolve_link_name_collision(proposed_name: str, existing_links: dict[str, Any]) -> str:
    """Find unique link name if collision exists."""
    if proposed_name not in existing_links:
        return proposed_name

    suffix = 0
    while True:
        candidate = f"{proposed_name}_{suffix}"
        if candidate not in existing_links:
            return candidate
        suffix += 1


def _is_subset_link(inferred: NormalizedLink, existing: NormalizedLink) -> bool:
    """Check if inferred link is a subset of existing link."""
    # Must target the same operation
    if inferred.path != existing.path or inferred.method != existing.method:
        return False

    # Inferred parameters must be subset of existing parameters
    if not inferred.parameters.issubset(existing.parameters):
        return False

    # Inferred request body must be subset of existing body
    return _is_request_body_subset(inferred.request_body, existing.request_body)


def _is_request_body_subset(inferred_body: Any, existing_body: Any) -> bool:
    """Check if inferred body is a subset of existing body."""
    # Empty inferred body is always a subset
    if not inferred_body:
        return True

    # If existing is empty but inferred isn't, not a subset
    if not existing_body:
        return False

    # Both must be dicts for subset comparison, otherwise check for equality
    if not isinstance(inferred_body, dict) or not isinstance(existing_body, dict):
        return inferred_body == existing_body

    # Check if all inferred fields exist in existing with same values
    for key, value in inferred_body.items():
        if existing_body.get(key, NOT_SET) != value:
            return False

    return True
