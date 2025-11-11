"""Compute dependency-based layering of API operations for ordered execution."""

from __future__ import annotations

from collections import defaultdict, deque
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .models import DependencyGraph


def compute_dependency_layers(graph: DependencyGraph) -> list[list[str]] | None:
    """Compute operation layers based on resource dependencies.

    Returns operations grouped into layers where each layer can execute in parallel,
    but layers must execute sequentially. Operations in layer N depend only on
    operations in layers < N. Operations within each layer are dispatched among
    multiple workers for parallel execution.

    Uses Kahn's algorithm for topological sort with level tracking.
    https://en.wikipedia.org/wiki/Topological_sorting#Kahn's_algorithm

    Args:
        graph: Dependency graph with operation nodes and their inputs/outputs

    Returns:
        List of layers (each layer is a list of operation labels), or None if
        the graph is empty or has no useful ordering information.

    Example:
        Layer 0: [POST /users, POST /products]  # No dependencies
        Layer 1: [GET /users/{id}, POST /orders]  # Depend on layer 0
        Layer 2: [GET /orders/{id}]  # Depends on layer 1

    """
    # Build dependency mapping: operation -> set of operations it depends on
    dependencies: dict[str, set[str]] = defaultdict(set)
    # Track which operations produce which resources
    producers: dict[int, set[str]] = defaultdict(set)

    # Index producers by resource ID
    for label, node in graph.operations.items():
        for output_slot in node.outputs:
            resource_id = id(output_slot.resource)
            producers[resource_id].add(label)

    # Build dependency edges
    for label, node in graph.operations.items():
        for input_slot in node.inputs:
            resource_id = id(input_slot.resource)
            # This operation depends on all operations that produce this resource
            for producer_label in producers[resource_id]:
                # Don't create self-dependencies
                if producer_label != label:
                    dependencies[label].add(producer_label)

    # If no dependencies exist, return None (no useful ordering)
    if not any(dependencies.values()):
        return None

    # Compute in-degree for each operation
    in_degree: dict[str, int] = dict.fromkeys(graph.operations, 0)
    for label, deps in dependencies.items():
        in_degree[label] = len(deps)

    # Initialize queue with operations that have no dependencies
    queue: deque[str] = deque()
    for label, degree in in_degree.items():
        if degree == 0:
            queue.append(label)

    # If no starting operations, we have cycles - use cycle-aware approach
    if not queue:
        return _compute_layers_with_cycles(graph, dependencies)

    # Process operations layer by layer
    layers: list[list[str]] = []
    processed: set[str] = set()

    while queue:
        # All operations in current queue belong to the same layer
        current_layer: list[str] = []
        layer_size = len(queue)

        for _ in range(layer_size):
            label = queue.popleft()
            current_layer.append(label)
            processed.add(label)

            # Reduce in-degree for operations that depend on this one
            for other_label, other_deps in dependencies.items():
                if label in other_deps:
                    in_degree[other_label] -= 1
                    if in_degree[other_label] == 0:
                        queue.append(other_label)

        if current_layer:
            current_layer.sort()
            layers.append(current_layer)

    # Check if all operations were processed (no cycles)
    if len(processed) < len(graph.operations):
        # Some operations not processed due to cycles
        # Add remaining operations to final layer
        remaining = sorted(set(graph.operations.keys()) - processed)
        if remaining:
            layers.append(remaining)

    return layers if layers else None


def _compute_layers_with_cycles(
    graph: DependencyGraph,
    dependencies: dict[str, set[str]],
) -> list[list[str]] | None:
    """Compute layers when graph contains cycles.

    Uses Tarjan's algorithm to find strongly connected components (SCCs),
    then treats each SCC as a single unit for layering.
    https://en.wikipedia.org/wiki/Tarjan%27s_strongly_connected_components_algorithm

    Args:
        graph: Dependency graph
        dependencies: Mapping of operation to its dependencies

    Returns:
        List of layers with cycle-containing operations placed conservatively

    """
    # Find strongly connected components (cycles)
    sccs = _find_sccs(graph, dependencies)

    # Map each operation to its SCC index
    op_to_scc: dict[str, int] = {}
    for scc_idx, scc_ops in enumerate(sccs):
        for op_label in scc_ops:
            op_to_scc[op_label] = scc_idx

    # Build SCC-level dependencies
    scc_dependencies: dict[int, set[int]] = defaultdict(set)
    for op_label, op_deps in dependencies.items():
        scc_idx = op_to_scc[op_label]
        for dep_label in op_deps:
            dep_scc_idx = op_to_scc[dep_label]
            if scc_idx != dep_scc_idx:  # Only cross-SCC dependencies
                scc_dependencies[scc_idx].add(dep_scc_idx)

    # Compute in-degree for each SCC
    scc_in_degree: dict[int, int] = dict.fromkeys(range(len(sccs)), 0)
    for scc_idx, deps in scc_dependencies.items():
        scc_in_degree[scc_idx] = len(deps)

    # Topological sort of SCCs
    queue: deque[int] = deque()
    for scc_idx, degree in scc_in_degree.items():
        if degree == 0:
            queue.append(scc_idx)

    layers: list[list[str]] = []
    processed_sccs: set[int] = set()

    while queue:
        current_layer: list[str] = []
        layer_size = len(queue)

        for _ in range(layer_size):
            scc_idx = queue.popleft()
            # Add all operations from this SCC to the current layer
            current_layer.extend(sccs[scc_idx])
            processed_sccs.add(scc_idx)

            # Reduce in-degree for dependent SCCs
            for other_scc_idx, other_deps in scc_dependencies.items():
                if scc_idx in other_deps:
                    scc_in_degree[other_scc_idx] -= 1
                    if scc_in_degree[other_scc_idx] == 0:
                        queue.append(other_scc_idx)

        if current_layer:
            # Sort for determinism
            current_layer.sort()
            layers.append(current_layer)

    return layers if layers else None


def _find_sccs(
    graph: DependencyGraph,
    dependencies: dict[str, set[str]],
) -> list[list[str]]:
    """Find strongly connected components using Tarjan's algorithm.

    Args:
        graph: Dependency graph
        dependencies: Mapping of operation to its dependencies

    Returns:
        List of SCCs, where each SCC is a list of operation labels

    """
    # Tarjan's algorithm state
    index_counter = [0]
    stack: list[str] = []
    lowlinks: dict[str, int] = {}
    index: dict[str, int] = {}
    on_stack: set[str] = set()
    sccs: list[list[str]] = []

    def strongconnect(op_label: str) -> None:
        # Set the depth index for this operation
        index[op_label] = index_counter[0]
        lowlinks[op_label] = index_counter[0]
        index_counter[0] += 1
        stack.append(op_label)
        on_stack.add(op_label)

        # Consider successors (operations that depend on this one)
        for other_label, other_deps in dependencies.items():
            if op_label in other_deps:
                if other_label not in index:
                    # Successor not yet visited
                    strongconnect(other_label)
                    lowlinks[op_label] = min(lowlinks[op_label], lowlinks[other_label])
                elif other_label in on_stack:
                    # Successor is in stack and hence in current SCC
                    lowlinks[op_label] = min(lowlinks[op_label], index[other_label])

        # If this is a root node, pop the stack to get SCC
        if lowlinks[op_label] == index[op_label]:
            scc: list[str] = []
            while True:
                w = stack.pop()
                on_stack.remove(w)
                scc.append(w)
                if w == op_label:
                    break
            sccs.append(scc)

    # Process all operations
    for op_label in graph.operations:
        if op_label not in index:
            strongconnect(op_label)

    return sccs
