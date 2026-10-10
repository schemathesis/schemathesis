from __future__ import annotations

import threading


class CredentialRemovals:
    """Operations whose only input is a credential and that already answered a request without it.

    That request is the same every time, so generation stops offering it once it has an answer. Strategies
    only see snapshots taken between Hypothesis runs: a run whose strategy changed under it would fail
    with inconsistent data generation.
    """

    __slots__ = ("_answered", "_frozen", "_lock")

    def __init__(self) -> None:
        self._answered: set[str] = set()
        self._frozen: frozenset[str] = frozenset()
        self._lock = threading.Lock()

    def record(self, label: str) -> None:
        with self._lock:
            self._answered.add(label)

    def snapshot(self) -> frozenset[str]:
        """Operations answered so far, for a run that starts now."""
        with self._lock:
            return frozenset(self._answered)

    def has_new_answers(self) -> bool:
        """Whether operations were answered after the snapshot that membership checks see."""
        with self._lock:
            return len(self._answered) != len(self._frozen)

    def begin_iteration(self) -> None:
        """Refresh what membership checks see; only call it while no Hypothesis run reads them."""
        self._frozen = self.snapshot()

    def __contains__(self, label: object) -> bool:
        return label in self._frozen
