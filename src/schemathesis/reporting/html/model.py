from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING

from schemathesis.core.failures import Severity
from schemathesis.engine import Status, StopReason
from schemathesis.engine.run import INTERNAL_PHASES, PhaseName

if TYPE_CHECKING:
    from schemathesis.engine.summary import FailureGroup, SummaryData, WarningBlock


# The phase name for failures from checks that run once after all phases.
AFTER_RUN = "After the run"


def split_label(label: str) -> tuple[str | None, str]:
    """Split "GET /users" into its method and path; other labels ("Query.users", "Run checks") have no method."""
    method, separator, path = label.partition(" ")
    if separator and method.isupper():
        return method, path
    return None, label


class Verdict(StrEnum):
    PASSED = "Passed"
    FAILED = "Failed"
    ERRORED = "Errored"
    INTERRUPTED = "Interrupted"
    EMPTY = "No tests ran"
    UNCHECKED = "No checks ran"

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
    code: str
    title: str
    severity: Severity
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
    # Shown in place of the status code and reason when the transport provides one.
    status_line: str | None
    # The body as the terminal shows it, `<EMPTY>` and `<BINARY>` included.
    body: str | None
    # One per request the failure needs, oldest first.
    commands: list[Command]
    # `st replay <id>` when a crash record exists.
    replay: str | None
    auth_identity: str | None
    # Seconds from the run start to the moment the run first recorded this failure, and the phase that found it.
    found_at: float | None = None
    found_in: str | None = None


@dataclass(slots=True)
class PhaseSpan:
    """One pass of a test phase; phases repeat under a time budget."""

    phase: str
    # Seconds from the run start.
    start: float
    end: float


@dataclass(slots=True)
class NothingTested:
    title: str
    # Filters as written on the command line or in the config file.
    filters: list[str]
    tip: str | None


class OperationStatus(StrEnum):
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
    # Distinct failure titles, most severe first; the run keeps one failure per kind per operation.
    failures: list[str]
    cases: int
    # Why the operation errored or was skipped.
    note: str | None
    # False when the operation keeps generating cases after a failure.
    stops_at_first_failure: bool
    failing_cases: list[FailingCase] = field(default_factory=list)
    errors: list[ErrorEntry] = field(default_factory=list)
    # Warnings that name this operation.
    warnings: list[WarningBlock] = field(default_factory=list)
    # Titles of the failures the baseline suppressed.
    known_failures: list[str] = field(default_factory=list)

    @property
    def severity(self) -> Severity:
        # Rows without failures are only ordered among themselves, so their value never decides anything.
        return min((failure.severity for case in self.failing_cases for failure in case.failures), default=Severity.LOW)

    @property
    def method(self) -> str:
        return split_label(self.label)[0] or ""

    @property
    def path(self) -> str:
        return split_label(self.label)[1]


@dataclass(slots=True)
class ExtractionNote:
    """A link that could not take the data it needs from a response."""

    link: str
    case_id: str
    reason: list[str]
    # Requests that led to the response, oldest first.
    commands: list[Command]
    status_code: int
    body: str


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
    warnings: list[WarningBlock]
    # Problems with the invocation itself, e.g. a timeout given in milliseconds.
    startup_warnings: list[str]
    # Each test phase as (name, kind, outcome): ("Fuzzing", "passed", "passed"), ("Coverage", "errored", "2 errors").
    phases: list[tuple[str, str, str]]
    # Checks enabled for the run.
    checks: list[str]
    # Other reports of the run as (name, href), the href relative to this report's directory.
    reports: list[tuple[str, str]]
    reauth_count: int
    # Authentication stopped working mid-run.
    reauth_broke: bool
    extraction_failures: list[ExtractionNote]
    running_time: float | None
    # Oldest first; empty when nothing ran or the run has no phases (pytest).
    phase_spans: list[PhaseSpan]
    stop_reason: StopReason
    # `started` is False when the engine never ran (schema failed to load); `complete` is False
    # when `EngineFinished` never arrived (crash, load-time Ctrl-C). Both feed the verdict.
    started: bool
    complete: bool
    exit_code: int
    # Distinct errors per phase name.
    phase_errors: dict[str, int] = field(default_factory=dict)

    @property
    def tested_operations(self) -> int:
        return self.summary.operations.tested if self.summary.operations is not None else 0

    @property
    def selected_operations(self) -> int:
        return self.summary.operations.selected if self.summary.operations is not None else 0

    @property
    def untested_operations(self) -> int:
        return max(self.selected_operations - self.tested_operations - self.errored_operations, 0)

    @property
    def not_run_operations(self) -> int:
        return self.untested_operations if self.stop_reason is not StopReason.COMPLETED else 0

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
            # Requests went out, yet no selected check applied to their responses.
            return Verdict.UNCHECKED if self.summary.test_cases.without_checks else Verdict.EMPTY
        # The verdict comes from the tests; other reasons for a non-zero exit show next to it.
        if self.summary.failures:
            return Verdict.FAILED
        if self.summary.errors:
            return Verdict.ERRORED
        return Verdict.PASSED

    @property
    def exit_note(self) -> tuple[str, str] | None:
        """Why the run exits non-zero although its verdict passed, and where the details are."""
        if self.exit_code == 0 or self.verdict is not Verdict.PASSED:
            return None
        baseline = self.summary.baseline
        if baseline is not None and baseline.write_error:
            return f"Exit code {self.exit_code}: baseline not saved", "note-baseline"
        # Rendering links this to the first warning.
        if self.warnings:
            return f"Exit code {self.exit_code}: warnings fail the run", "warnings"
        if self.startup_warnings:
            return f"Exit code {self.exit_code}: warnings fail the run", "w-heading"
        return f"Exit code {self.exit_code}", ""
