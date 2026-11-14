"""Compute dependency-based layering of API operations for ordered execution."""

from __future__ import annotations

from collections import defaultdict, deque
from typing import TYPE_CHECKING

from schemathesis.core.transport import restful_method_priority

if TYPE_CHECKING:
    from .models import DependencyGraph


def _restful_order_key(label: str) -> tuple[int, str]:
    method = label.split(maxsplit=1)[0]
    return restful_method_priority(method), label


def compute_dependency_layers(graph: DependencyGraph) -> list[list[str]] | None:
    """Group operations into layers that depend only on earlier layers (Kahn's algorithm).

    None when no operation depends on another.
    """
    dependencies: dict[str, set[str]] = defaultdict(set)
    producers: dict[int, set[str]] = defaultdict(set)

    for label, node in graph.operations.items():
        for output_slot in node.outputs:
            resource_id = id(output_slot.resource)
            producers[resource_id].add(label)

    for label, node in graph.operations.items():
        for input_slot in node.inputs:
            resource_id = id(input_slot.resource)
            for producer_label in producers[resource_id]:
                if producer_label != label:
                    dependencies[label].add(producer_label)

    if not any(dependencies.values()):
        return None

    # Reverse edges, so in-degree updates visit only dependents instead of every operation
    dependents: dict[str, set[str]] = defaultdict(set)
    for label, deps in dependencies.items():
        for dependency in deps:
            dependents[dependency].add(label)

    in_degree: dict[str, int] = dict.fromkeys(graph.operations, 0)
    for label, deps in dependencies.items():
        in_degree[label] = len(deps)

    queue: deque[str] = deque()
    for label, degree in in_degree.items():
        if degree == 0:
            queue.append(label)

    # Every operation has a dependency, so the graph has cycles
    if not queue:
        return _compute_layers_with_cycles(graph, dependencies, dependents)

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

            for other_label in dependents[label]:
                in_degree[other_label] -= 1
                if in_degree[other_label] == 0:
                    queue.append(other_label)

        if current_layer:
            current_layer.sort(key=_restful_order_key)
            layers.append(current_layer)

    if len(processed) < len(graph.operations):
        # Operations on cycles go to a final layer
        remaining = sorted(set(graph.operations.keys()) - processed, key=_restful_order_key)
        if remaining:
            layers.append(remaining)

    return layers or None


def _compute_layers_with_cycles(
    graph: DependencyGraph,
    dependencies: dict[str, set[str]],
    dependents: dict[str, set[str]],
) -> list[list[str]] | None:
    """Layer strongly connected components as single units, so cycles do not block layering."""
    sccs = _find_sccs(graph, dependents)

    op_to_scc: dict[str, int] = {}
    for scc_idx, scc_ops in enumerate(sccs):
        for op_label in scc_ops:
            op_to_scc[op_label] = scc_idx

    scc_dependencies: dict[int, set[int]] = defaultdict(set)
    for op_label, op_deps in dependencies.items():
        scc_idx = op_to_scc[op_label]
        for dep_label in op_deps:
            dep_scc_idx = op_to_scc[dep_label]
            if scc_idx != dep_scc_idx:
                scc_dependencies[scc_idx].add(dep_scc_idx)

    scc_dependents: dict[int, set[int]] = defaultdict(set)
    for scc_idx, deps in scc_dependencies.items():
        for dep_scc_idx in deps:
            scc_dependents[dep_scc_idx].add(scc_idx)

    scc_in_degree: dict[int, int] = dict.fromkeys(range(len(sccs)), 0)
    for scc_idx, deps in scc_dependencies.items():
        scc_in_degree[scc_idx] = len(deps)

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
            current_layer.extend(sccs[scc_idx])
            processed_sccs.add(scc_idx)

            for other_scc_idx in scc_dependents[scc_idx]:
                scc_in_degree[other_scc_idx] -= 1
                if scc_in_degree[other_scc_idx] == 0:
                    queue.append(other_scc_idx)

        if current_layer:
            # Sort for determinism
            current_layer.sort(key=_restful_order_key)
            layers.append(current_layer)

    return layers or None


def _find_sccs(
    graph: DependencyGraph,
    dependents: dict[str, set[str]],
) -> list[list[str]]:
    """Find strongly connected components using Tarjan's algorithm."""
    index_counter = [0]
    stack: list[str] = []
    lowlinks: dict[str, int] = {}
    index: dict[str, int] = {}
    on_stack: set[str] = set()
    sccs: list[list[str]] = []

    def strongconnect(op_label: str) -> None:
        index[op_label] = index_counter[0]
        lowlinks[op_label] = index_counter[0]
        index_counter[0] += 1
        stack.append(op_label)
        on_stack.add(op_label)

        for other_label in dependents[op_label]:
            if other_label not in index:
                strongconnect(other_label)
                lowlinks[op_label] = min(lowlinks[op_label], lowlinks[other_label])
            elif other_label in on_stack:
                # On the stack, so part of the current SCC
                lowlinks[op_label] = min(lowlinks[op_label], index[other_label])

        # Root of an SCC: pop its members off the stack
        if lowlinks[op_label] == index[op_label]:
            scc: list[str] = []
            while True:
                w = stack.pop()
                on_stack.remove(w)
                scc.append(w)
                if w == op_label:
                    break
            sccs.append(scc)

    for op_label in graph.operations:
        if op_label not in index:
            strongconnect(op_label)

    return sccs
