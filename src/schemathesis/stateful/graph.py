from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Iterator

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

    def find_parent(self, case: Case) -> Case | None:
        node = self._nodes.get(case.id)
        if node and node.parent_id:
            parent_node = self._nodes.get(node.parent_id)
            return parent_node.case if parent_node else None
        return None

    def find_ancestors_and_their_children(self, case: Case) -> Iterator[Case]:
        """Returns all ancestors and their children for a given case."""
        current_id = case.id
        seen = {current_id}

        while True:
            node = self._nodes.get(current_id)
            if not node or not node.parent_id:
                break

            # Get all children of the parent (siblings of the current case)
            parent_id = node.parent_id
            for maybe_child in self._nodes.values():
                if maybe_child.parent_id == parent_id and maybe_child.case.id not in seen:
                    seen.add(maybe_child.case.id)
                    yield maybe_child.case

            # Move up to the parent
            current_id = parent_id
            if current_id not in seen:
                seen.add(current_id)
                parent_node = self._nodes.get(current_id)
                if parent_node:
                    yield parent_node.case
