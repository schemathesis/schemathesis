from __future__ import annotations

import threading
from dataclasses import dataclass, field
from enum import Enum

from schemathesis.core.warnings import SchemathesisWarning

# Fires only on operations that 405 from the first call; any non-405 cancels
# the streak permanently. High enough to ride out noisy early-response orderings.
METHOD_NOT_ALLOWED_THRESHOLD = 10


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
class _Counters:
    method_not_allowed: int = 0
    other: int = 0


@dataclass(slots=True)
class _OperationRecord:
    counters: _Counters = field(default_factory=_Counters)
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
            if status_code == 405 and not is_documented_status:
                record.counters.method_not_allowed += 1
            else:
                record.counters.other += 1
            if record.counters.other == 0 and record.counters.method_not_allowed >= METHOD_NOT_ALLOWED_THRESHOLD:
                record.verdict = Verdict(
                    directive=SchedulingDirective.SKIP,
                    reason=(
                        f"Skipped after {record.counters.method_not_allowed} consecutive "
                        f"`405 Method Not Allowed` responses"
                    ),
                    warning=SchemathesisWarning.METHOD_NOT_ALLOWED,
                )

    def verdict(self, operation_label: str) -> Verdict:
        record = self._records.get(operation_label)
        if record is None:
            return _DEFAULT_VERDICT
        return record.verdict
