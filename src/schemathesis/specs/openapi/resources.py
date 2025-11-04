"""OpenAPI-specific resource descriptor building.

Analyzes OpenAPI schemas using the dependency graph to identify resources
that can be captured from API responses and reused in subsequent test generation.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Iterable, Sequence

from schemathesis.resources import Cardinality, ResourceDescriptor

if TYPE_CHECKING:
    from schemathesis.specs.openapi.schemas import OpenApiSchema
    from schemathesis.specs.openapi.stateful.dependencies.models import OperationNode


def _iter_output_descriptors(operation: OperationNode, label: str) -> Iterable[ResourceDescriptor]:
    """Convert OpenAPI operation outputs to generic resource descriptors."""
    for output in operation.outputs:
        # Map OpenAPI Cardinality to generic Cardinality
        cardinality = Cardinality.MANY if output.cardinality.value == "MANY" else Cardinality.ONE

        yield ResourceDescriptor(
            resource_name=output.resource.name,
            operation_label=label,
            status_code=int(output.status_code),
            pointer=output.pointer,
            cardinality=cardinality,
            fields=tuple(output.resource.fields),
        )


def build_descriptors(schema: OpenApiSchema) -> Sequence[ResourceDescriptor]:
    """Build resource descriptors from an OpenAPI schema's dependency graph.

    Args:
        schema: OpenAPI schema with dependency analysis

    Returns:
        Sequence of resource descriptors identifying resources that can be captured

    """
    graph = schema.analysis.dependency_graph
    descriptors: list[ResourceDescriptor] = []

    for label, operation in graph.operations.items():
        descriptors.extend(_iter_output_descriptors(operation, label))

    return tuple(descriptors)
