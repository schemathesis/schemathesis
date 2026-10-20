from __future__ import annotations

import threading
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from schemathesis.core.error_feedback import ErrorFeedbackStore
    from schemathesis.python._constants.pool import ConstantsPool
    from schemathesis.resources import ExtraDataSource


class AuthHeaderLog:
    """Declared headers that configured auth writes on every positive case, per operation."""

    __slots__ = ("_written", "_kept", "_lock")

    def __init__(self) -> None:
        self._written: dict[str, set[str]] = {}
        self._kept: dict[str, set[str]] = {}
        self._lock = threading.Lock()

    def record(self, label: str, *, written: set[str], kept: set[str]) -> None:
        with self._lock:
            self._written.setdefault(label, set()).update(written)
            self._kept.setdefault(label, set()).update(kept)

    def get(self, label: str) -> frozenset[str]:
        with self._lock:
            return frozenset(self._written.get(label, ())) - self._kept.get(label, set())


@dataclass(frozen=True, slots=True)
class FeedbackSources:
    """Runtime learning channels that steer generation for one operation.

    A typed carrier for the feedback signals the engine feeds into strategy
    building, replacing loose string keys in the `as_strategy` kwargs bag.
    """

    extra_data_source: ExtraDataSource | None = None
    error_feedback: ErrorFeedbackStore | None = None
    constants_value_source: ConstantsPool | None = None
    auth_header_log: AuthHeaderLog | None = None
    # Fixed for the whole Hypothesis run; a strategy that changes under a run fails it as flaky.
    auth_headers: frozenset[str] = frozenset()

    def for_examples(self) -> FeedbackSources:
        # The examples phase never draws from extracted constants.
        return replace(self, constants_value_source=None)

    def for_negative(self) -> FeedbackSources:
        # Negative values never take captured values or extracted constants.
        return replace(self, extra_data_source=None, constants_value_source=None)


NO_FEEDBACK = FeedbackSources()
