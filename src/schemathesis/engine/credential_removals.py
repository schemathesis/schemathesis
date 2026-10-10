from __future__ import annotations

import threading

REJECTIONS = frozenset({401, 403})


class CredentialRemovals:
    """Operations whose only input is a credential and that rejected a request without it.

    That request is the same every time, so generation offers it again only at doubling intervals, enough to notice a
    server that stops rejecting it. Strategies only see snapshots taken between Hypothesis runs: a run whose strategy
    changed under it would fail with inconsistent data generation.
    """

    __slots__ = ("_runs", "_frozen", "_fresh", "_lock")

    def __init__(self) -> None:
        # Runs started since the first rejection, per operation.
        self._runs: dict[str, int] = {}
        self._frozen: frozenset[str] = frozenset()
        self._fresh = False
        self._lock = threading.Lock()

    def record(self, label: str, status_code: int) -> None:
        with self._lock:
            if status_code not in REJECTIONS:
                return
            self._runs.setdefault(label, 0)
            if label not in self._frozen:
                self._fresh = True

    def _skips_removal(self, label: str) -> bool:
        runs = self._runs.get(label)
        if runs is None:
            return False
        runs += 1
        self._runs[label] = runs
        # Offer the removal again on the 1st, 2nd, 4th, 8th... run after the rejection.
        return runs & (runs - 1) != 0

    def for_run(self, label: str) -> frozenset[str]:
        """Operations whose removal a run of `label` that starts now leaves out."""
        with self._lock:
            return frozenset({label}) if self._skips_removal(label) else frozenset()

    def has_new_answers(self) -> bool:
        """Whether an operation offered the removal rejected it after the snapshot that membership checks see."""
        with self._lock:
            return self._fresh

    def begin_iteration(self) -> None:
        """Refresh what membership checks see; only call it while no Hypothesis run reads them."""
        with self._lock:
            self._frozen = frozenset(label for label in list(self._runs) if self._skips_removal(label))
            self._fresh = False

    def __contains__(self, label: object) -> bool:
        return label in self._frozen
