from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING

from schemathesis.engine import Status, StopReason
from schemathesis.engine.run import INTERNAL_PHASES, PhaseName

if TYPE_CHECKING:
    from schemathesis.cli.summary import FailureGroup, SummaryData


def split_label(label: str) -> tuple[str | None, str]:
    """Split "GET /users" into its method and path; other labels ("Query.users", "Run checks") have no method."""
    method, separator, path = label.partition(" ")
    if separator and method.isupper():
        return method, path
    return None, label


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
    title: str
    message: str
    tip: str | None
    # Error extras or the traceback, line by line.
    details: list[str] = field(default_factory=list)
    reproduce: str | None = None


@dataclass(slots=True)
class FailureEntry:
    title: str
    # One per violation; same-title violations on one response share an entry.
    messages: list[str]


@dataclass(slots=True)
class Command:
    curl: str
    # The request the command sends; unknown when the reproduction and the recorded chain disagree.
    # GraphQL requests have no method and name the field they call instead of a path.
    method: str | None
    path: str | None
    # True for the request whose response failed the checks.
    failed: bool


@dataclass(slots=True)
class FailingCase:
    case_id: str | None
    failures: list[FailureEntry]
    status_code: int | None
    # The body as the terminal shows it, `<EMPTY>` and `<BINARY>` included.
    body: str | None
    # One per request the failure needs, oldest first.
    commands: list[Command]
    # `st replay <id>` when a crash record exists.
    replay: str | None
    auth_identity: str | None


@dataclass(slots=True)
class NothingTested:
    title: str
    # Filters as written on the command line or in the config file.
    filters: list[str]
    tip: str | None


class OperationStatus(str, Enum):
    FAILED = "Failed"
    PASSED = "Passed"
    ERRORED = "Errored"
    SKIPPED = "Skipped"

    @property
    def css(self) -> str:
        return self.name.lower()


@dataclass(slots=True)
class OperationRow:
    label: str
    status: OperationStatus
    # Distinct failure titles; the run keeps one failure per kind per operation, so titles do not repeat.
    failures: list[str]
    cases: int
    # Why the operation errored or was skipped.
    note: str | None
    # False when the operation keeps generating cases after a failure.
    stops_at_first_failure: bool
    failing_cases: list[FailingCase] = field(default_factory=list)
    errors: list[ErrorEntry] = field(default_factory=list)

    @property
    def method(self) -> str:
        return split_label(self.label)[0] or ""

    @property
    def path(self) -> str:
        return split_label(self.label)[1]


@dataclass(slots=True)
class ReportData:
    meta: ReportMeta
    summary: SummaryData
    fatal_error: ErrorEntry | None
    nothing_tested: NothingTested | None
    # The phase running when the run stopped early.
    last_phase: PhaseName | None
    operations: list[OperationRow]
    # Failure titles with counts for failures that belong to no operation, e.g. undeclared methods or stateful runs.
    unattributed_failures: list[tuple[str, int]]
    unattributed_cases: list[tuple[str, FailingCase]]
    unattributed_errors: list[tuple[str, ErrorEntry]]
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
    def selected_operations(self) -> int:
        return self.summary.operations.selected if self.summary.operations is not None else 0

    @property
    def untested_operations(self) -> int:
        return max(self.selected_operations - self.tested_operations - self.errored_operations, 0)

    # ponytail: a run that stopped early reports every untested operation as not run, including any
    # excluded on purpose; phase-level skip reasons cannot tell the two apart.
    @property
    def not_run_operations(self) -> int:
        return self.untested_operations if self.stop_reason is not StopReason.COMPLETED else 0

    @property
    def skipped_operations(self) -> int:
        return self.untested_operations if self.stop_reason is StopReason.COMPLETED else 0

    @property
    def errored_operations(self) -> int:
        return self.summary.operations.errored if self.summary.operations is not None else 0

    @property
    def failed_operations(self) -> list[str]:
        return [row.label for row in self.operations if row.status is OperationStatus.FAILED]

    @property
    def top_failures(self) -> list[FailureGroup]:
        return sorted(self.summary.failures, key=lambda group: (-group.count, group.title))

    @property
    def executed_phase_count(self) -> int:
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
        # Filters that match nothing exit non-zero, yet nothing failed.
        if not self.tested_operations and not self.summary.failures and not self.summary.errors:
            return Verdict.EMPTY
        if self.exit_code == 0:
            return Verdict.PASSED
        if self.summary.failures or not self.summary.errors:
            return Verdict.FAILED
        return Verdict.ERRORED
