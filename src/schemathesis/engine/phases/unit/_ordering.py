"""Operation ordering strategies for unit test phases."""

from __future__ import annotations

from typing import TYPE_CHECKING, Iterable

from schemathesis.config import OperationOrdering

if TYPE_CHECKING:
    from schemathesis.schemas import APIOperation
    from schemathesis.specs.openapi.schemas import OpenApiSchema


def compute_operation_layers(
    schema: OpenApiSchema,
    operations: list[APIOperation],
    strategy: OperationOrdering,
) -> list[list[APIOperation]]:
    """Compute operation layers based on the specified ordering strategy.

    Args:
        schema: OpenAPI schema with analysis data
        operations: List of operations to order
        strategy: Ordering strategy enum

    Returns:
        List of operation layers. Each layer is a list of operations that can
        execute in parallel. With NONE strategy, returns a single layer.

    """
    if strategy == OperationOrdering.NONE:
        # No ordering - all operations in one layer
        return [operations]

    # OperationOrdering.AUTO - try dependency graph first, fallback to RESTful heuristic
    return _compute_auto_layers(schema, operations)


def _compute_auto_layers(
    schema: OpenApiSchema,
    operations: list[APIOperation],
) -> list[list[APIOperation]]:
    """Auto strategy: Try dependency graph, fallback to RESTful heuristic.

    Args:
        schema: OpenAPI schema with analysis data
        operations: List of operations to order

    Returns:
        List of operation layers

    """
    # Try dependency-based ordering
    dependency_layers = schema.analysis.dependency_layers

    if dependency_layers is not None:
        # Map operation labels to operations
        op_by_label = {op.label: op for op in operations}

        # Build layers using dependency graph ordering
        result_layers: list[list[APIOperation]] = []
        for layer_labels in dependency_layers:
            layer_ops = []
            for label in layer_labels:
                if label in op_by_label:
                    layer_ops.append(op_by_label[label])
            if layer_ops:
                result_layers.append(layer_ops)

        # Add any operations not in dependency graph
        covered = {label for layer in dependency_layers for label in layer if label in op_by_label}
        uncovered = [op for op in operations if op.label not in covered]
        if uncovered:
            # Append uncovered operations as final layer
            uncovered.sort(key=lambda op: (op.path, op.method))
            result_layers.append(uncovered)

        if result_layers:
            return result_layers

    # Fallback to RESTful heuristic
    return _compute_restful_layers(operations)


def _compute_restful_layers(operations: Iterable[APIOperation]) -> list[list[APIOperation]]:
    """Compute layers using RESTful heuristics based on HTTP methods.

    The heuristic is:
    - Layer 0: POST, PUT (create/replace resources - populate database)
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
    layer_2: list[APIOperation] = []  # DELETE
    other: list[APIOperation] = []  # Any other methods

    for op in operations:
        method_upper = op.method.upper()
        if method_upper in ("POST", "PUT"):
            layer_0.append(op)
        elif method_upper in ("GET", "PATCH", "HEAD", "OPTIONS"):
            layer_1.append(op)
        elif method_upper == "DELETE":
            layer_2.append(op)
        else:
            # Unknown methods go to layer 1 (read-like)
            other.append(op)

    # Sort each layer for determinism (alphabetically by path, then method)
    for layer in [layer_0, layer_1, layer_2, other]:
        layer.sort(key=lambda op: (op.path, op.method))

    # Build result, omitting empty layers
    result: list[list[APIOperation]] = []
    if layer_0:
        result.append(layer_0)
    if layer_1 or other:
        # Combine layer_1 and other methods
        result.append(layer_1 + other)
    if layer_2:
        result.append(layer_2)

    # If no operations, return single empty layer
    return result if result else [[]]
