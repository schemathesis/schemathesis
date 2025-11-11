"""Operation ordering strategies for unit test phases."""

from __future__ import annotations

from typing import TYPE_CHECKING, Iterable

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
    - Layer 1: GET, PATCH, HEAD, OPTIONS (read/update - test against created data)
    - Layer 2: DELETE (cleanup - remove resources last)

    This ordering provides better test coverage even without explicit dependencies,
    as operations that create resources run before operations that read them.

    Args:
        operations: Iterable of API operations

    Returns:
        List of three layers, each containing operations for that layer's methods.
        Empty layers are omitted.

    """
    layer_0: list[APIOperation] = []  # POST, PUT
    layer_1: list[APIOperation] = []  # GET, PATCH, HEAD, OPTIONS
    layer_2: list[APIOperation] = []  # DELETE + others (TRACE?)

    for op in operations:
        method_upper = op.method.upper()
        if method_upper in ("POST", "PUT"):
            layer_0.append(op)
        elif method_upper in ("GET", "PATCH", "HEAD", "OPTIONS"):
            layer_1.append(op)
        else:
            layer_2.append(op)

    result: list[list[APIOperation]] = []
    # Sort each layer for so the execution order is deterministic
    for layer in [layer_0, layer_1, layer_2]:
        if layer:
            layer.sort(key=lambda op: (op.path, op.method))
            result.append(layer)
    return result
