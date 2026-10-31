"""GraphQL state machine."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar

from hypothesis.stateful import Bundle

from schemathesis.core.errors import NoProducers
from schemathesis.generation.hypothesis import add_database_key
from schemathesis.generation.stateful.state_machine import BundleStateMachine
from schemathesis.specs.graphql.handles import Handle, bundle_name, deleted_bundle_name
from schemathesis.specs.graphql.stateful._rules import generate_rules_for

if TYPE_CHECKING:
    from schemathesis.specs.graphql.schemas import GraphQLSchema


class GraphQLStateMachine(BundleStateMachine):
    """State machine for GraphQL stateful testing built around producer/consumer chains."""

    schema: GraphQLSchema  # set on the dynamically-built subclass
    # Names of `@rule` methods that populate a bundle. Empty -> stateful testing has nothing to do.
    _producer_rule_names: ClassVar[frozenset[str]] = frozenset()

    def __init__(self) -> None:
        if not type(self)._producer_rule_names:
            raise NoProducers(
                "GraphQL stateful testing requires at least one producer mutation "
                "(a mutation returning an Object type with an `id` field)."
            )
        self._id_origins: dict[tuple[Handle, str], str] = {}
        self._deleted_id_origins: dict[tuple[Handle, str], str] = {}
        super().__init__()


def create_state_machine(schema: GraphQLSchema) -> type[GraphQLStateMachine]:
    """Build a `GraphQLStateMachine` subclass with one bundle per id-typed object type and per-operation rules."""
    analysis = schema.analysis
    attrs: dict[str, Any] = {"schema": schema, "_transitions": analysis.transitions}
    for handle in analysis.handles:
        attrs[bundle_name(handle)] = Bundle(bundle_name(handle))
        if handle.field_name == "id":
            attrs[deleted_bundle_name(handle)] = Bundle(deleted_bundle_name(handle))
    rule_set = generate_rules_for(analysis.summaries, attrs, analysis.handles, analysis.schema_index)
    for name, decorated in rule_set.rules:
        attrs[name] = decorated
    attrs["_producer_rule_names"] = rule_set.producer_names
    attrs["_root_labels"] = rule_set.root_labels
    machine = type("GraphQLStateMachine", (GraphQLStateMachine,), attrs)
    add_database_key(machine, "\n".join(attrs))
    return machine


__all__ = ["GraphQLStateMachine", "create_state_machine"]
