from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

from schemathesis.engine import Status, StopReason
from schemathesis.engine.run import INTERNAL_PHASES
from schemathesis.generation.stateful import STATEFUL_TESTS_LABEL

if TYPE_CHECKING:
    from schemathesis.cli.summary import FailureGroup, SummaryData


class Verdict(str, Enum):
    PASSED = "Passed"
    FAILED = "Failed"
    ERRORED = "Errored"
    INTERRUPTED = "Interrupted"
    EMPTY = "No tests ran"

    @property
    def css(self) -> str:
        return self.name.lower()


@dataclass(slots=True)
class ReportMeta:
    generated_at: str
    location: str | None
    base_url: str | None
    command: str
    seed: int | None


@dataclass(slots=True)
class ErrorEntry:
    # Empty `operations` and `None` phase for run-level errors; `count` is how many errors share the title.
    operations: list[str]
    title: str
    message: str
    traceback: str | None
    phase: str | None
    count: int = 1


@dataclass(slots=True)
class ReportData:
    meta: ReportMeta
    summary: SummaryData
    # First-seen detail per error title; counts come from `summary.errors`.
    errors: list[ErrorEntry]
    fatal_error: ErrorEntry | None
    running_time: float | None
    stop_reason: StopReason
    # `started` is False when the engine never ran (schema failed to load); `complete` is False
    # when `EngineFinished` never arrived (crash, load-time Ctrl-C). Both feed the verdict.
    started: bool
    complete: bool
    exit_code: int

    @property
    def tested_operations(self) -> int:
        return self.summary.operations.tested if self.summary.operations is not None else 0

    @property
    def skipped_operations(self) -> int:
        return self.summary.operations.skipped if self.summary.operations is not None else 0

    @property
    def errored_operations(self) -> int:
        return self.summary.operations.errored if self.summary.operations is not None else 0

    @property
    def failed_operations(self) -> list[str]:
        # Stateful failures are keyed by a pseudo-label, and unsupported-method probes by a method the schema
        # never lists; neither is a tested operation.
        return sorted(
            {
                label
                for group in self.summary.failures
                if group.type != "UnsupportedMethodResponse"
                for label in group.operations
                if label != STATEFUL_TESTS_LABEL
            }
        )

    @property
    def top_failures(self) -> list[FailureGroup]:
        return sorted(self.summary.failures, key=lambda group: (-group.count, group.title))

    @property
    def executed_phases(self) -> int:
        return sum(
            1
            for phase, (status, _) in self.summary.phases.items()
            if phase not in INTERNAL_PHASES and status != Status.SKIP
        )

    @property
    def verdict(self) -> Verdict:
        if self.fatal_error is not None or not self.started:
            return Verdict.ERRORED
        # An in-engine Ctrl-C still produces `EngineFinished` (with `INTERRUPTED`) and the CLI exits 0.
        if not self.complete or self.stop_reason is StopReason.INTERRUPTED:
            return Verdict.INTERRUPTED
        if self.exit_code == 0:
            return Verdict.PASSED if self.tested_operations else Verdict.EMPTY
        if self.summary.failures or not self.errors:
            return Verdict.FAILED
        return Verdict.ERRORED
