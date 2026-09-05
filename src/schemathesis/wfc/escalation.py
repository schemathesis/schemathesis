"""Per-operation identity selection for WFC documents that list several users."""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from schemathesis.auths import AuthContext, AuthProvider
    from schemathesis.core.spec import SchemaMetadata
    from schemathesis.generation.case import Case

# 403 says the identity lacks the role. 401 belongs to `CachingAuthProvider`, which refreshes the
# token and replays; counting it here would double-handle it.
DENIED = 403


def _is_admitted(status_code: int) -> bool:
    """Whether the response shows the request got past authorization.

    400 counts as neither: many stacks validate the payload before authorizing, so it says
    nothing about the identity.
    """
    return status_code not in (400, 401, DENIED)


class EscalatingAuthProvider:
    """Try each identity in document order, moving on from the ones an operation refuses."""

    __slots__ = ("providers", "names", "_assigned", "_settled", "_lock")

    def __init__(self, providers: list[AuthProvider], names: list[str]) -> None:
        self.providers = providers
        self.names = names
        self._assigned: dict[str, int] = {}
        # Operations that have been admitted keep their identity for the rest of the run.
        self._settled: set[str] = set()
        self._lock = threading.Lock()

    def index_for(self, label: str) -> int:
        return self._assigned.get(label, 0)

    def get(self, case: Case, context: AuthContext) -> Any:
        return self.providers[self.index_for(context.operation.label)].get(case, context)

    def set(self, case: Case, data: Any, context: AuthContext) -> None:
        index = self.index_for(context.operation.label)
        case._auth_identity = self.names[index]
        self.providers[index].set(case, data, context)

    def record(self, label: str, status_code: int) -> None:
        """Fold one response into the assignment for `label`."""
        with self._lock:
            if label in self._settled:
                return
            if _is_admitted(status_code):
                self._settled.add(label)
                return
            if status_code != DENIED:
                return
            # One denial is enough: an operation may only get a couple of requests, and a
            # threshold above its budget could never flip.
            current = self._assigned.get(label, 0)
            if current + 1 < len(self.providers):
                self._assigned[label] = current + 1

    def snapshot(self) -> dict[str, str]:
        """Identity per operation, including the ones that never had to escalate."""
        with self._lock:
            labels = set(self._assigned) | self._settled
            return {label: self.names[self._assigned.get(label, 0)] for label in labels}

    def restore(self, assignments: dict[str, str]) -> None:
        """Start from where a previous run settled; escalation still corrects it if the API changed."""
        index_by_name = {name: index for index, name in enumerate(self.names)}
        with self._lock:
            for label, name in assignments.items():
                index = index_by_name.get(name)
                if index is not None:
                    self._assigned[label] = index


def _escalating_provider(schema: SchemaMetadata) -> EscalatingAuthProvider | None:
    """`[auth.wfc]` registers at most one, so there is nothing to merge across providers."""
    return next((p for p in schema.auth.providers if isinstance(p, EscalatingAuthProvider)), None)


def identity_assignments(schema: SchemaMetadata) -> dict[str, str]:
    """Per-operation identities settled on so far, for persistence."""
    provider = _escalating_provider(schema)
    return provider.snapshot() if provider is not None else {}


def restore_identity_assignments(schema: SchemaMetadata, assignments: dict[str, str]) -> None:
    provider = _escalating_provider(schema)
    if provider is not None:
        provider.restore(assignments)


def record_auth_outcome(case: Case, status_code: int) -> None:
    """Let an escalating provider learn from this response."""
    provider = _escalating_provider(case.operation.schema)
    if provider is not None:
        provider.record(case.operation.label, status_code)
