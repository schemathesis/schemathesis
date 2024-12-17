from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from schemathesis.core.transport import Response

if TYPE_CHECKING:
    from schemathesis.models import Case


@dataclass
class TransitionId:
    name: str
    status_code: str

    __slots__ = ("name", "status_code")


@dataclass
class ExecutionMetadata:
    """Metadata about test case execution."""

    response: Response
    overrides_all_parameters: bool
    transition_id: TransitionId | None

    __slots__ = ("response", "overrides_all_parameters", "transition_id")


@dataclass
class CaseNode:
    case: Case
    parent_id: str | None
    metadata: ExecutionMetadata

    __slots__ = ("case", "parent_id", "metadata")


@dataclass
class ExecutionGraph:
    __slots__ = ("_nodes",)

    def __init__(self) -> None:
        self._nodes: dict[str, CaseNode] = {}

    def add_node(self, *, case: Case, parent_id: str | None = None, metadata: ExecutionMetadata) -> None:
        self._nodes[case.id] = CaseNode(case=case, parent_id=parent_id, metadata=metadata)

    def get_parent(self, case: Case) -> Case | None:
        node = self._nodes.get(case.id)
        if node and node.parent_id:
            parent_node = self._nodes.get(node.parent_id)
            return parent_node.case if parent_node else None
        return None
