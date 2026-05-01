"""Layered operation ordering for GraphQL fuzzing.

Returns a list of layers, where every operation in layer N must dispatch
before any operation in layer N+1. Within a layer, ordering is alphabetical
by `operation.label` for determinism.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from typing import TYPE_CHECKING, cast

from schemathesis.specs.graphql.inference import OperationRole, classify_operation

if TYPE_CHECKING:
    from schemathesis.schemas import APIOperation
    from schemathesis.specs.graphql.schemas import GraphQLOperationDefinition


def compute_graphql_layers(operations: Iterable[APIOperation]) -> list[list[APIOperation]]:
    """Partition GraphQL root-field operations into role-ordered layers.

    Layers are returned in execution order: producers, readers, mutators,
    cleanup. Empty layers are omitted. Within each layer, operations are
    sorted alphabetically by `label` so dispatch order is reproducible.
    """
    by_role: dict[OperationRole, list[APIOperation]] = defaultdict(list)
    for op in operations:
        definition = cast("GraphQLOperationDefinition", op.definition)
        role = classify_operation(field_name=definition.field_name, root_type=definition.root_type)
        by_role[role].append(op)

    return [sorted(by_role[role], key=lambda op: op.label) for role in sorted(by_role)]
