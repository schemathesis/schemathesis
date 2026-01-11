"""Build resource descriptors from OpenAPI dependency graphs."""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING

from schemathesis.core.parameters import ParameterLocation
from schemathesis.resources import ResourceDescriptor

if TYPE_CHECKING:
    from schemathesis.specs.openapi.schemas import OpenApiSchema


def build_descriptors(schema: OpenApiSchema) -> Sequence[ResourceDescriptor]:
    """Build resource descriptors from dependency graph outputs."""
    graph = schema.analysis.dependency_graph

    # Map (resource_name, base_path) -> identifier_field for path-based lookup
    identifier_by_path: dict[tuple[str, str], str] = {}
    identifier_fallback: dict[str, str] = {}

    for operation in graph.operations.values():
        for input_slot in operation.inputs:
            if input_slot.parameter_location == ParameterLocation.PATH and input_slot.resource_field is not None:
                resource_name = input_slot.resource.name
                base_path = operation.path.rsplit("/{", 1)[0] if "/{" in operation.path else operation.path
                key = (resource_name, base_path)
                if key not in identifier_by_path:
                    identifier_by_path[key] = input_slot.resource_field
                if resource_name not in identifier_fallback:
                    identifier_fallback[resource_name] = input_slot.resource_field

    def get_identifier_field(output_label: str, resource_name: str) -> str:
        producer_path = output_label.split(" ", 1)[1] if " " in output_label else output_label
        key = (resource_name, producer_path)
        if key in identifier_by_path:
            return identifier_by_path[key]
        return identifier_fallback.get(resource_name, "id")

    return tuple(
        ResourceDescriptor(
            resource_name=output.resource.name,
            operation=label,
            status_code=output.status_code,
            pointer=output.pointer,
            cardinality=output.cardinality,
            is_primitive_identifier=output.is_primitive_identifier,
            identifier_field=get_identifier_field(label, output.resource.name)
            if output.is_primitive_identifier
            else None,
        )
        for label, operation in graph.operations.items()
        for output in operation.outputs
    )
