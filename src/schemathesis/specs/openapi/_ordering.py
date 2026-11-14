"""OpenAPI-aware operation ordering for unit test phases."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from typing import TYPE_CHECKING

from schemathesis.core.transport import restful_method_priority

if TYPE_CHECKING:
    from schemathesis.schemas import APIOperation
    from schemathesis.specs.openapi.schemas import OpenApiSchema


def compute_operation_layers(schema: OpenApiSchema, operations: list[APIOperation]) -> list[list[APIOperation]]:
    """Group operations into layers; operations within one layer can run in parallel."""
    dependency_layers = schema.analysis.dependency_layers

    if dependency_layers is not None:
        operations_by_label = {op.label: op for op in operations}
        # The graph covers the whole schema; a caller may pass a subset, so labels it left out are skipped.
        layers = [
            [operations_by_label[label] for label in labels if label in operations_by_label]
            for labels in dependency_layers
            if labels
        ]
        # Operations the dependency graph could not analyze (e.g. unresolvable `$ref`s) run by RESTful priority.
        analyzed = {label for labels in dependency_layers for label in labels}
        for operation in operations:
            if operation.label not in analyzed:
                layers[min(restful_method_priority(operation.method), len(layers) - 1)].append(operation)
        return layers

    return _compute_restful_layers(operations)


def _compute_restful_layers(operations: Iterable[APIOperation]) -> list[list[APIOperation]]:
    """Layer by method: POST/PUT, then GET/PATCH/HEAD/OPTIONS/QUERY, then DELETE, so creators run before readers."""
    by_priority: dict[int, list[APIOperation]] = defaultdict(list)
    for op in operations:
        by_priority[restful_method_priority(op.method)].append(op)

    result: list[list[APIOperation]] = []
    for priority in sorted(by_priority):
        layer = by_priority[priority]
        layer.sort(key=lambda op: (op.path, op.method))
        result.append(layer)
    return result
