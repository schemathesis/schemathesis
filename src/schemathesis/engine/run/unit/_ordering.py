"""Operation ordering strategies for unit test phases."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from typing import TYPE_CHECKING

from schemathesis.core.transport import restful_method_priority

if TYPE_CHECKING:
    from schemathesis.schemas import APIOperation
    from schemathesis.specs.openapi.schemas import OpenApiSchema


def compute_operation_layers(schema: OpenApiSchema, operations: list[APIOperation]) -> list[list[APIOperation]]:
    """Compute operation layers.

    Args:
        schema: OpenAPI schema with analysis data
        operations: List of operations to order

    Returns:
        List of operation layers. Each layer is a list of operations that can execute in parallel.

    """
    dependency_layers = schema.analysis.dependency_layers

    if dependency_layers is not None:
        # Build layers using dependency graph ordering
        operations_by_label = {op.label: op for op in operations}
        return [[operations_by_label[label] for label in labels] for labels in dependency_layers if labels]

    # Fallback to RESTful heuristic
    return _compute_restful_layers(operations)


def _compute_restful_layers(operations: Iterable[APIOperation]) -> list[list[APIOperation]]:
    """Compute layers using RESTful heuristics based on HTTP methods.

    The heuristic is:
    - Layer 0: POST, PUT (populate resources)
    - Layer 1: GET, PATCH, HEAD, OPTIONS, QUERY (read/update - test against created data)
    - Layer 2: DELETE (cleanup - remove resources last)

    This ordering provides better test coverage even without explicit dependencies,
    as operations that create resources run before operations that read them.

    Args:
        operations: Iterable of API operations

    Returns:
        List of three layers, each containing operations for that layer's methods.
        Empty layers are omitted.

    """
    by_priority: dict[int, list[APIOperation]] = defaultdict(list)
    for op in operations:
        by_priority[restful_method_priority(op.method)].append(op)

    result: list[list[APIOperation]] = []
    # Sort each layer for so the execution order is deterministic
    for priority in sorted(by_priority):
        layer = by_priority[priority]
        layer.sort(key=lambda op: (op.path, op.method))
        result.append(layer)
    return result
