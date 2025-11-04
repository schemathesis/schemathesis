"""Build resource descriptors from OpenAPI dependency graphs."""

from __future__ import annotations

from typing import TYPE_CHECKING, Sequence

from schemathesis.resources import ResourceDescriptor

if TYPE_CHECKING:
    from schemathesis.specs.openapi.schemas import OpenApiSchema


def build_descriptors(schema: OpenApiSchema) -> Sequence[ResourceDescriptor]:
    """Build resource descriptors from dependency graph outputs."""
    return tuple(
        ResourceDescriptor(
            resource_name=output.resource.name,
            operation=label,
            status_code=output.status_code,
            pointer=output.pointer,
            cardinality=output.cardinality,
        )
        for label, operation in schema.analysis.dependency_graph.operations.items()
        for output in operation.outputs
    )
