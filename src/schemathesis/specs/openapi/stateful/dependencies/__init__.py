"""Dependency detection between API operations for stateful testing.

Infers which operations must run before others by tracking resource creation and consumption across API operations.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from schemathesis.core.compat import RefResolutionError
from schemathesis.core.result import Ok
from schemathesis.specs.openapi.stateful.dependencies.inputs import extract_inputs, update_input_field_bindings
from schemathesis.specs.openapi.stateful.dependencies.models import (
    CanonicalizationCache,
    Cardinality,
    DefinitionSource,
    DependencyGraph,
    InputSlot,
    OperationMap,
    OperationNode,
    OutputSlot,
    ResourceDefinition,
    ResourceMap,
)
from schemathesis.specs.openapi.stateful.dependencies.outputs import extract_outputs

if TYPE_CHECKING:
    from schemathesis.specs.openapi.schemas import BaseOpenAPISchema

__all__ = [
    "analyze",
    "DependencyGraph",
    "InputSlot",
    "OutputSlot",
    "Cardinality",
    "ResourceDefinition",
    "DefinitionSource",
]


def analyze(schema: BaseOpenAPISchema) -> DependencyGraph:
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

    return DependencyGraph(operations=operations, resources=resources)
