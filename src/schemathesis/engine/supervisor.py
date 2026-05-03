from __future__ import annotations

import threading
from collections import deque
from dataclasses import dataclass, field
from enum import Enum

from schemathesis.core.warnings import SchemathesisWarning

# Sliding-window 405 rate rule: if `METHOD_NOT_ALLOWED_RATE` of the last
# `METHOD_NOT_ALLOWED_WINDOW` responses are undocumented 405s, the operation
# is treated as not implemented. Tolerates intermittent body-validation 4xx
# responses (operations where the path matches but the method doesn't).
METHOD_NOT_ALLOWED_WINDOW = 10
METHOD_NOT_ALLOWED_RATE = 0.8


class SchedulingDirective(str, Enum):
    """Per-operation instruction the supervisor issues to the scheduler."""

    RUN = "run"
    SKIP = "skip"


@dataclass(slots=True, frozen=True)
class Verdict:
    """Outcome of consulting the supervisor for one operation."""

    directive: SchedulingDirective
    reason: str | None = None
    warning: SchemathesisWarning | None = None


_DEFAULT_VERDICT = Verdict(directive=SchedulingDirective.RUN)


@dataclass(slots=True)
class _OperationRecord:
    window: deque[bool] = field(default_factory=lambda: deque(maxlen=METHOD_NOT_ALLOWED_WINDOW))
    verdict: Verdict = _DEFAULT_VERDICT


class Supervisor:
    """Per-operation runtime supervisor consulted by the scheduler.

    `record_response` is called for every response and may flip an operation's
    verdict once a rule fires. `verdict` is the read path the scheduler
    consults before queueing each scenario.
    """

    __slots__ = ("_records", "_lock")

    def __init__(self) -> None:
        self._records: dict[str, _OperationRecord] = {}
        self._lock = threading.Lock()

    def record_response(self, *, operation_label: str, status_code: int, is_documented_status: bool = False) -> None:
        with self._lock:
            record = self._records.get(operation_label)
            if record is None:
                record = _OperationRecord()
                self._records[operation_label] = record
            elif record.verdict.directive is not SchedulingDirective.RUN:
                # Verdict already issued — further signal can only confirm it.
                return
            # Documented 405s mean the spec contract anticipates them; respect that and
            # don't treat them as evidence of an unimplemented method.
            is_undocumented_405 = status_code == 405 and not is_documented_status
            record.window.append(is_undocumented_405)
            if len(record.window) < METHOD_NOT_ALLOWED_WINDOW:
                return
            hits = sum(record.window)
            if hits / METHOD_NOT_ALLOWED_WINDOW >= METHOD_NOT_ALLOWED_RATE:
                record.verdict = Verdict(
                    directive=SchedulingDirective.SKIP,
                    reason=(
                        f"Skipped after {hits} of last {METHOD_NOT_ALLOWED_WINDOW} responses "
                        f"were `405 Method Not Allowed`"
                    ),
                    warning=SchemathesisWarning.METHOD_NOT_ALLOWED,
                )

    def verdict(self, operation_label: str) -> Verdict:
        record = self._records.get(operation_label)
        if record is None:
            return _DEFAULT_VERDICT
        return record.verdict
