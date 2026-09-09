"""Per-operation identity selection for WFC documents that list several users."""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from schemathesis.auths import AuthContext, AuthProvider
    from schemathesis.core.spec import SchemaMetadata
    from schemathesis.generation.case import Case

# Both say the identity cannot do this operation. A 401 reaches this point only after
# `reauth_and_replay` has already refreshed the token and replayed, so a stale token is ruled out.
DENIED = frozenset({401, 403})


def _is_admitted(status_code: int) -> bool:
    """Whether the response proves the request got past authorization.

    Only a served response does. A rejected payload or a missing resource is decided before the
    role is looked at, so it says nothing about the identity and must not settle the assignment.
    """
    return 200 <= status_code < 400


ANONYMOUS = "<anonymous>"


class _AnonymousAuthProvider:
    """Send no credentials. Returning `None` leaves the request untouched."""

    def get(self, case: Case, context: AuthContext) -> Any:
        return None

    def set(self, case: Case, data: Any, context: AuthContext) -> None:  # pragma: no cover
        pass


# A 403 got past authentication into authorization; a 401 did not reach that far.
_DENIAL_RANK = {401: 0, 403: 1}


class EscalatingAuthProvider:
    """Try each identity in document order, moving on from the ones an operation refuses."""

    __slots__ = ("providers", "names", "_assigned", "_settled", "_best", "_lock")

    def __init__(self, providers: list[AuthProvider], names: list[str]) -> None:
        # Credentials an operation rejects are worse than none: a stack that refuses a bad
        # `Authorization` header serves the same request once it is absent. Second in line, so a
        # document whose credentials never work costs one request to find out rather than all of them.
        self.providers = [providers[0], _AnonymousAuthProvider(), *providers[1:]]
        self.names = [names[0], ANONYMOUS, *names[1:]]
        self._assigned: dict[str, int] = {}
        # Operations that have been admitted keep their identity for the rest of the run.
        self._settled: set[str] = set()
        # Best (rank, index) seen per operation, so an exhausted chain keeps its furthest rung.
        self._best: dict[str, tuple[int, int]] = {}
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
            if status_code not in DENIED:
                return
            current = self._assigned.get(label, 0)
            rank = _DENIAL_RANK[status_code]
            best = self._best.get(label)
            # Ties go to the later rung: among identities that got equally far, it is the one with
            # the most privilege, and so the one a later request has any chance with.
            if best is None or rank >= best[0]:
                self._best[label] = (rank, current)
            # One denial is enough: an operation may only get a couple of requests, and a
            # threshold above its budget could never flip.
            if current + 1 < len(self.providers):
                self._assigned[label] = current + 1
                return
            # Chain exhausted: keep whichever rung got furthest and stop moving.
            self._assigned[label] = self._best[label][1]
            self._settled.add(label)

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
