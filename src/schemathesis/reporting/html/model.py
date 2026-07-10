from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import TYPE_CHECKING

from schemathesis.engine import Status
from schemathesis.engine.run import PhaseName

if TYPE_CHECKING:
    from schemathesis.cli.commands.run.warnings import WarningData


@dataclass(slots=True)
class FailureEntry:
    check_name: str
    title: str
    message: str


@dataclass(slots=True)
class ParentStep:
    method: str
    path: str
    status_code: int | None
    status_message: str
    elapsed_ms: int | None
    # Preformatted request/response text for the expandable step body.
    detail: str


@dataclass(slots=True)
class CaseEntry:
    case_id: str
    phase: PhaseName
    failures: list[FailureEntry]
    curl: str | None
    response_status: int | None
    response_message: str
    response_body: str | None
    response_content_type: str | None
    elapsed_ms: int | None
    parent_steps: list[ParentStep]


@dataclass(slots=True)
class PhaseCases:
    total: int = 0
    failed: int = 0


@dataclass(slots=True)
class OperationEntry:
    label: str
    status: Status
    summary: str | None
    definition: str | None
    skip_reason: str | None
    elapsed: float
    cases_per_phase: dict[PhaseName, PhaseCases]
    failing_cases: list[CaseEntry]
    error_count: int

    @property
    def method(self) -> str:
        method, separator, _ = self.label.partition(" ")
        # GraphQL labels ("Type.field") have no method; only OpenAPI labels are "METHOD /path".
        return method if separator else ""

    @property
    def path(self) -> str:
        _, separator, path = self.label.partition(" ")
        return path if separator else self.label

    @property
    def total_cases(self) -> int:
        return sum(cases.total for cases in self.cases_per_phase.values())

    @property
    def failed_checks_count(self) -> int:
        return sum(len(case.failures) for case in self.failing_cases)

    @property
    def check_counts(self) -> list[tuple[str, int]]:
        counter = Counter(failure.check_name for case in self.failing_cases for failure in case.failures)
        return sorted(counter.items(), key=lambda item: (-item[1], item[0]))


@dataclass(slots=True)
class PhaseTiming:
    started_at: float | None = None
    finished_at: float | None = None


@dataclass(slots=True)
class TickItem:
    check_name: str
    label: str
    case_id: str


@dataclass(slots=True)
class FailureTick:
    at: float
    items: list[TickItem]


@dataclass(slots=True)
class ErrorEntry:
    label: str
    title: str
    message: str
    traceback: str | None
    phase: str | None


@dataclass(slots=True)
class ReportData:
    generated_at: str
    location: str | None
    base_url: str | None
    command: str | None
    seed: int | None
    phases: dict[PhaseName, PhaseTiming]
    operations: dict[str, OperationEntry]
    ticks: list[FailureTick]
    warnings: WarningData | None
    errors: list[ErrorEntry]
    fatal_errors: list[ErrorEntry]
    running_time: float | None
    stop_reason: str | None
    exit_code: int = 0

    def _by_status(self, status: Status) -> list[OperationEntry]:
        return [entry for entry in self.operations.values() if entry.status == status]

    @property
    def failed_operations(self) -> list[OperationEntry]:
        return self._by_status(Status.FAILURE)

    @property
    def passed_operations(self) -> list[OperationEntry]:
        return self._by_status(Status.SUCCESS)

    @property
    def skipped_operations(self) -> list[OperationEntry]:
        return self._by_status(Status.SKIP)

    @property
    def total_cases(self) -> int:
        return sum(entry.total_cases for entry in self.operations.values())

    @property
    def top_failures(self) -> list[tuple[str, int]]:
        counter: Counter[str] = Counter()
        for entry in self.operations.values():
            for check_name in {failure.check_name for case in entry.failing_cases for failure in case.failures}:
                counter[check_name] += 1
        return sorted(counter.items(), key=lambda item: (-item[1], item[0]))

    @property
    def executed_phases(self) -> list[tuple[PhaseName, PhaseTiming]]:
        return [
            (phase, self.phases[phase])
            for phase in PhaseName.defaults()
            if phase in self.phases
            and self.phases[phase].started_at is not None
            and self.phases[phase].finished_at is not None
        ]
