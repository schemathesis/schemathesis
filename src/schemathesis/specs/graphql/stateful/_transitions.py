"""Bipartite producer-to-consumer transition graph for GraphQL stateful testing."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from schemathesis.specs.graphql.inference import OperationRole

if TYPE_CHECKING:
    from collections.abc import Iterator

    from schemathesis.specs.graphql.stateful._rules import _OperationSummary


@dataclass(slots=True)
class _Endpoint:
    label: str


@dataclass(slots=True)
class _Edge:
    source: _Endpoint
    target: _Endpoint


@dataclass(slots=True)
class _OperationTransitions:
    incoming: list[_Edge] = field(default_factory=list)
    outgoing: list[_Edge] = field(default_factory=list)


@dataclass(slots=True)
class GraphQLTransitions:
    operations: dict[str, _OperationTransitions]
    # Bundle name -> producer operation labels emitting into that bundle.
    bundle_sources: dict[str, list[str]]

    def producer_labels_for_bundle(self, bundle_name: str) -> Iterator[str]:
        yield from self.bundle_sources.get(bundle_name, ())


def build_transitions(summaries: list[_OperationSummary]) -> GraphQLTransitions:
    """Build the producer-to-consumer transition graph from precomputed operation summaries."""
    operations: dict[str, _OperationTransitions] = {summary.label: _OperationTransitions() for summary in summaries}

    producers_by_type: dict[str, list[str]] = {}
    for summary in summaries:
        if summary.role == OperationRole.PRODUCER and summary.return_type_name is not None:
            producers_by_type.setdefault(summary.return_type_name, []).append(summary.label)

    bundle_sources: dict[str, list[str]] = {}
    for type_name, producer_labels in producers_by_type.items():
        bundle_sources[f"{type_name}_ids"] = list(producer_labels)
        bundle_sources[f"deleted_{type_name}_ids"] = list(producer_labels)

    for summary in summaries:
        for consumed_type in set(summary.consumed.values()):
            for producer_label in producers_by_type.get(consumed_type, ()):
                if producer_label == summary.label:
                    continue
                edge = _Edge(source=_Endpoint(label=producer_label), target=_Endpoint(label=summary.label))
                operations[producer_label].outgoing.append(edge)
                operations[summary.label].incoming.append(edge)

    return GraphQLTransitions(operations=operations, bundle_sources=bundle_sources)


def count_inferred_transitions(summaries: list[_OperationSummary]) -> int:
    """Count producer-to-consumer transitions inferable from the given summaries."""
    producers_by_type: dict[str, list[str]] = {}
    for summary in summaries:
        if summary.role == OperationRole.PRODUCER and summary.return_type_name is not None:
            producers_by_type.setdefault(summary.return_type_name, []).append(summary.label)
    edges = 0
    for summary in summaries:
        for consumed_type in set(summary.consumed.values()):
            for producer_label in producers_by_type.get(consumed_type, ()):
                if producer_label != summary.label:
                    edges += 1
    return edges
