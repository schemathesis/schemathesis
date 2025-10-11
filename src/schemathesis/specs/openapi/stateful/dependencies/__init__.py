from __future__ import annotations

from typing import TYPE_CHECKING

from schemathesis.core.compat import RefResolutionError
from schemathesis.core.result import Ok
from schemathesis.specs.openapi.stateful.dependencies.inputs import extract_inputs, propagate_resource_changes
from schemathesis.specs.openapi.stateful.dependencies.models import (
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
    """Analyze the schema and build a dependency graph from it."""
    operations: OperationMap = {}
    resources: ResourceMap = {}
    # A set of resources updated with a better definition source
    # Their set of fields has changed and depending
    updated_resources: set[str] = set()

    # The algorithm is one-pass and goes over all valid API operations
    # It parses responses & input parameters and builds resources from them
    # Each resource represent a possible edge between API operations, and the
    # main goal is to cluster and deduplicate such inferred resources.
    # In some cases inputs only have partial resources, so we need to find the most complete version
    # that may include merging multiple resources
    for result in schema.get_all_operations():
        if isinstance(result, Ok):
            operation = result.ok()
            try:
                # Extract what this operation needs (from parameters & request body)
                inputs = extract_inputs(
                    operation=operation,
                    resources=resources,
                    updated_resources=updated_resources,
                    resolver=schema.resolver,
                )
                # Extract what this operation produces (from responses)
                outputs = extract_outputs(
                    operation=operation,
                    resources=resources,
                    updated_resources=updated_resources,
                    resolver=schema.resolver,
                )
                operations[operation.label] = OperationNode(
                    method=operation.method,
                    path=operation.path,
                    inputs=list(inputs),
                    outputs=list(outputs),
                )
            except RefResolutionError:
                continue

    for resource in updated_resources:
        propagate_resource_changes(resource, operations)

    return DependencyGraph(operations=operations, resources=resources)
