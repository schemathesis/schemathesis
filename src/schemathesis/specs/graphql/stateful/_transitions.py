"""Bipartite producer-to-consumer transition graph for GraphQL stateful testing."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from schemathesis.specs.graphql.handles import bundle_name, deleted_bundle_name
from schemathesis.specs.graphql.stateful._rules import producers_by_handle

if TYPE_CHECKING:
    from collections.abc import Iterator

    from schemathesis.specs.graphql.handles import Handle, SchemaIndex
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


def build_transitions(
    summaries: list[_OperationSummary], handles: set[Handle], index: SchemaIndex
) -> GraphQLTransitions:
    """Build the producer-to-consumer transition graph from precomputed operation summaries."""
    operations: dict[str, _OperationTransitions] = {summary.label: _OperationTransitions() for summary in summaries}

    producers = producers_by_handle(summaries, handles, index)

    bundle_sources: dict[str, list[str]] = {}
    for handle, producer_labels in producers.items():
        bundle_sources[bundle_name(handle)] = list(producer_labels)
        if handle.field_name == "id":
            bundle_sources[deleted_bundle_name(handle)] = list(producer_labels)

    for summary in summaries:
        for consumed_handle in set(summary.consumed.values()):
            for producer_label in producers.get(consumed_handle, ()):
                if producer_label == summary.label:
                    continue
                edge = _Edge(source=_Endpoint(label=producer_label), target=_Endpoint(label=summary.label))
                operations[producer_label].outgoing.append(edge)
                operations[summary.label].incoming.append(edge)

    return GraphQLTransitions(operations=operations, bundle_sources=bundle_sources)


def count_inferred_transitions(summaries: list[_OperationSummary], handles: set[Handle], index: SchemaIndex) -> int:
    """Count producer-to-consumer transitions inferable from the given summaries."""
    producers = producers_by_handle(summaries, handles, index)
    edges = 0
    for summary in summaries:
        for consumed_handle in set(summary.consumed.values()):
            for producer_label in producers.get(consumed_handle, ()):
                if producer_label != summary.label:
                    edges += 1
    return edges
