"""Aggregated derived data for a GraphQL schema, computed once per schema instance."""

from __future__ import annotations

from typing import TYPE_CHECKING

from schemathesis.specs.graphql.stateful._bundles import collect_id_typed_object_types
from schemathesis.specs.graphql.stateful._rules import iter_operation_summaries
from schemathesis.specs.graphql.stateful._transitions import (
    GraphQLTransitions,
    build_transitions,
    count_inferred_transitions,
)

if TYPE_CHECKING:
    from schemathesis.specs.graphql.schemas import GraphQLSchema
    from schemathesis.specs.graphql.stateful._rules import _OperationSummary


class GraphQLAnalysis:
    """Lazy, per-schema cache of inference results used across stateful and statistic paths."""

    __slots__ = ("schema", "_bundle_types", "_summaries", "_transitions", "_transition_count")

    def __init__(self, schema: GraphQLSchema) -> None:
        self.schema = schema
        self._bundle_types: set[str] | None = None
        self._summaries: list[_OperationSummary] | None = None
        self._transitions: GraphQLTransitions | None = None
        self._transition_count: int | None = None

    @property
    def bundle_types(self) -> set[str]:
        """Object types whose `id` field is exposed (drives bundle creation)."""
        if self._bundle_types is None:
            self._bundle_types = collect_id_typed_object_types(self.schema.client_schema)
        return self._bundle_types

    @property
    def summaries(self) -> list[_OperationSummary]:
        """Operation summaries for selected (non-filtered) Query/Mutation fields."""
        if self._summaries is None:
            self._summaries = list(iter_operation_summaries(self.schema, self.bundle_types))
        return self._summaries

    @property
    def transitions(self) -> GraphQLTransitions:
        """Producer-to-consumer transition graph used by the state machine controller."""
        if self._transitions is None:
            self._transitions = build_transitions(self.summaries)
        return self._transitions

    @property
    def transition_count(self) -> int:
        """Number of inferred transitions between selected operations."""
        if self._transition_count is None:
            self._transition_count = count_inferred_transitions(self.summaries)
        return self._transition_count
