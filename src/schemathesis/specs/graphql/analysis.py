"""Aggregated derived data for a GraphQL schema, computed once per schema instance."""

from __future__ import annotations

from typing import TYPE_CHECKING

from schemathesis.specs.graphql.handles import Handle, SchemaIndex
from schemathesis.specs.graphql.stateful._discovery import discover_handles
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

    __slots__ = (
        "schema",
        "_handles",
        "_schema_index",
        "_summaries",
        "_transitions",
        "_transition_count",
    )

    def __init__(self, schema: GraphQLSchema) -> None:
        self.schema = schema
        self._handles: set[Handle] | None = None
        self._schema_index: SchemaIndex | None = None
        self._summaries: list[_OperationSummary] | None = None
        self._transitions: GraphQLTransitions | None = None
        self._transition_count: int | None = None

    @property
    def handles(self) -> set[Handle]:
        """Producer handles that drive bundle creation."""
        if self._handles is None:
            self._handles = discover_handles(self.schema.client_schema, self.schema_index)
        return self._handles

    @property
    def schema_index(self) -> SchemaIndex:
        if self._schema_index is None:
            self._schema_index = SchemaIndex(self.schema.client_schema)
        return self._schema_index

    @property
    def summaries(self) -> list[_OperationSummary]:
        """Operation summaries for selected (non-filtered) Query/Mutation fields."""
        if self._summaries is None:
            self._summaries = list(iter_operation_summaries(self.schema, self.handles, self.schema_index))
        return self._summaries

    @property
    def transitions(self) -> GraphQLTransitions:
        """Producer-to-consumer transition graph used by the state machine controller."""
        if self._transitions is None:
            self._transitions = build_transitions(self.summaries, self.handles, self.schema_index)
        return self._transitions

    @property
    def transition_count(self) -> int:
        """Number of inferred transitions between selected operations."""
        if self._transition_count is None:
            self._transition_count = count_inferred_transitions(self.summaries, self.handles, self.schema_index)
        return self._transition_count
