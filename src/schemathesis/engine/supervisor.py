from __future__ import annotations

import threading
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING

from schemathesis.core.cache import CacheWriter, Kind, request_from_case
from schemathesis.core.transport import CallOutcome
from schemathesis.core.warnings import SchemathesisWarning

if TYPE_CHECKING:
    from schemathesis.core.transport import Response
    from schemathesis.generation.case import Case

# Fires only on operations that 405, or answer that they are not served, from the first call; any other response
# cancels the streak permanently. High enough to ride out noisy early-response orderings.
METHOD_NOT_ALLOWED_THRESHOLD = 10


class SchedulingDirective(StrEnum):
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


def _method_not_allowed_verdict(reason: str) -> Verdict:
    return Verdict(directive=SchedulingDirective.SKIP, reason=reason, warning=SchemathesisWarning.METHOD_NOT_ALLOWED)


@dataclass(slots=True)
class _Counters:
    method_not_allowed: int = 0
    not_served: int = 0
    other: int = 0


@dataclass(slots=True)
class _OperationRecord:
    counters: _Counters = field(default_factory=_Counters)
    verdict: Verdict = _DEFAULT_VERDICT


class Supervisor:
    """Per-operation runtime supervisor consulted by the scheduler.

    `observe` is called for every response and may flip an operation's
    verdict once a rule fires. `verdict` is the read path the scheduler
    consults before queueing each scenario.
    """

    __slots__ = ("_records", "_lock")

    def __init__(self) -> None:
        self._records: dict[str, _OperationRecord] = {}
        self._lock = threading.Lock()

    def observe(self, case: Case, response: Response, *, cache_writer: CacheWriter | None) -> None:
        """Record a response for the case's operation, judging 405s against its documented responses.

        The schema decides which other responses say the server does not serve the operation at all.
        """
        schema = case.operation.schema
        status_code = response.status_code
        not_served_status = None
        if schema.classify_call_outcome(case, response) is CallOutcome.NOT_SERVED:
            not_served_status = schema.status_name_for_code(status_code) or str(status_code)
        self.record_response(
            operation_label=case.operation.label,
            status_code=status_code,
            is_documented_status=case.operation.responses.find_by_status_code(status_code) is not None,
            not_served_status=not_served_status,
            case=case,
            cache_writer=cache_writer,
        )

    def record_response(
        self,
        *,
        operation_label: str,
        status_code: int,
        is_documented_status: bool = False,
        not_served_status: str | None = None,
        case: Case | None = None,
        cache_writer: CacheWriter | None = None,
    ) -> None:
        flipped_now = False
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
            elif not_served_status is not None:
                record.counters.not_served += 1
            else:
                record.counters.other += 1
            if record.counters.other == 0 and record.counters.method_not_allowed >= METHOD_NOT_ALLOWED_THRESHOLD:
                record.verdict = _method_not_allowed_verdict(
                    f"Skipped after {record.counters.method_not_allowed} consecutive `405 Method Not Allowed` responses"
                )
                flipped_now = True
            elif record.counters.other == 0 and record.counters.not_served >= METHOD_NOT_ALLOWED_THRESHOLD:
                record.verdict = Verdict(
                    directive=SchedulingDirective.SKIP,
                    reason=f"Skipped after {record.counters.not_served} consecutive `{not_served_status}` responses",
                    warning=SchemathesisWarning.NOT_SERVED,
                )
        if flipped_now and cache_writer is not None and case is not None:
            cache_writer.record(Kind.METHOD_NOT_ALLOWED, operation_label, request_from_case(case))

    def hydrate_method_not_allowed_skip(self, operation_label: str) -> None:
        """Apply the same SKIP verdict the live `record_response` streak would have produced."""
        with self._lock:
            record = self._records.get(operation_label)
            if record is None:
                record = _OperationRecord()
                self._records[operation_label] = record
            elif record.verdict.directive is not SchedulingDirective.RUN:
                return
            record.verdict = _method_not_allowed_verdict(
                f"Skipped after {METHOD_NOT_ALLOWED_THRESHOLD}+ consecutive `405 Method Not Allowed` responses"
                " (restored from cache)"
            )

    def verdict(self, operation_label: str) -> Verdict:
        record = self._records.get(operation_label)
        if record is None:
            return _DEFAULT_VERDICT
        return record.verdict
