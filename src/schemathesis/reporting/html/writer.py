from __future__ import annotations

import datetime
import time
from collections import Counter
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from schemathesis.cli.summary import (
    ErrorGroup,
    OperationsSummary,
    SummaryData,
    WarningData,
    reduce_failures,
    reduce_test_cases,
)
from schemathesis.core.failures import Failure
from schemathesis.core.output.sanitization import sanitized
from schemathesis.engine import Status, StopReason
from schemathesis.engine.statistic import GroupedFailures, Statistic
from schemathesis.reporting.html import write_report
from schemathesis.reporting.html.build import failing_case, failure_titles
from schemathesis.reporting.html.model import ErrorEntry, OperationRow, OperationStatus, ReportData, ReportMeta
from schemathesis.reporting.recorders import grouped_failures_from_recorder

if TYPE_CHECKING:
    from pathlib import Path

    from schemathesis.config import OutputConfig
    from schemathesis.engine.recorder import RecordedScenario


@dataclass(slots=True)
class _Operation:
    cases: int = 0
    # Failure titles already reported for this operation.
    seen: set[str] = field(default_factory=set)
    errors: list[ErrorEntry] = field(default_factory=list)
    skip_reasons: set[str] = field(default_factory=set)
    ran: bool = False


class HtmlWriter:
    """Accumulates pytest results per API operation and writes the HTML report on close."""

    def __init__(
        self,
        output_dir: Path,
        *,
        config: OutputConfig,
        location: str | None,
        base_url: str | None,
        command: str,
        seed: int | None,
        checks: list[str],
        started_at: float,
    ) -> None:
        self._output_dir = output_dir
        self._config = config
        self._location = location
        self._base_url = base_url
        self._command = command
        self._seed = seed
        self._checks = checks
        self._started_at = started_at
        self._statistic = Statistic()
        self._operations: dict[str, _Operation] = {}

    def write(
        self,
        recorder: RecordedScenario,
        elapsed_sec: float = 0.0,
        *,
        status: Status | None = None,
        message: str | None = None,
    ) -> None:
        label = recorder.label
        operation = self._operations.setdefault(label, _Operation())
        operation.cases += len(recorder.cases)
        self._statistic.total_cases += len(recorder.cases)
        recorded = grouped_failures_from_recorder(recorder)
        groups = _latest_per_failure(recorded, operation.seen)
        if not recorded and status == Status.FAILURE:
            # The test itself failed, e.g. on an assertion, without any check failing.
            failure = Failure(operation=label, title="Test failed", message=message or "")
            groups = [GroupedFailures(case_id=None, code_sample=None, failures=[failure], response=None)]
        if groups:
            self._statistic.failures.setdefault(label, {}).update({group.case_id or label: group for group in groups})
            self._statistic.cases_with_failures += sum(group.case_id is not None for group in groups)
        if status == Status.ERROR:
            title, _, detail = (message or "").partition(": ")
            operation.errors.append(ErrorEntry(title=title, message=detail, tip=None))
        elif status == Status.SKIP:
            operation.skip_reasons.add(message or "Skipped")
        else:
            operation.ran = True

    def close(self) -> None:
        rows = [self._row(label, operation) for label, operation in self._operations.items()]
        tested = sum(row.status in (OperationStatus.PASSED, OperationStatus.FAILED) for row in rows)
        errored = sum(row.status is OperationStatus.ERRORED for row in rows)
        error_titles = Counter(error.title for row in rows for error in row.errors)
        sanitization = self._config.sanitization
        data = ReportData(
            meta=ReportMeta(
                generated_at=datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%d %H:%M:%S UTC"),
                location=sanitized(self._location, sanitization),
                base_url=sanitized(self._base_url, sanitization),
                command=self._command,
                seed=self._seed,
            ),
            summary=SummaryData(
                operations=OperationsSummary(
                    total=len(rows),
                    selected=len(rows),
                    tested=tested,
                    errored=errored,
                    skipped=len(rows) - tested - errored,
                    skip_reasons=sorted(
                        {reason for operation in self._operations.values() for reason in operation.skip_reasons}
                    ),
                ),
                # pytest runs Hypothesis phases, not Schemathesis ones.
                phases={},
                test_cases=reduce_test_cases(self._statistic),
                failures=reduce_failures(self._statistic),
                errors=[ErrorGroup(title=title, count=count) for title, count in sorted(error_titles.items())],
                warnings=WarningData(),
                baseline=None,
                filtered=0,
            ),
            fatal_error=None,
            nothing_tested=None,
            last_phase=None,
            operations=rows,
            unattributed_failures=[],
            unattributed_cases=[],
            unattributed_errors=[],
            warnings=[],
            startup_warnings=[],
            phases=[],
            checks=self._checks,
            reports=[],
            reauth_count=0,
            reauth_broke=False,
            extraction_failures=[],
            running_time=time.time() - self._started_at,
            phase_spans=[],
            stop_reason=StopReason.COMPLETED,
            started=True,
            complete=True,
            # The pytest exit code also covers tests unrelated to the API, so the verdict alone describes the run.
            exit_code=0,
        )
        write_report(data, self._output_dir)

    def _row(self, label: str, operation: _Operation) -> OperationRow:
        cases = [
            failing_case(group, config=self._config, replay=None)
            for group in self._statistic.failures.get(label, {}).values()
        ]
        failures = failure_titles(cases)
        note = None
        if failures:
            status = OperationStatus.FAILED
        elif operation.errors:
            status = OperationStatus.ERRORED
            note = operation.errors[0].title
        elif operation.ran:
            status = OperationStatus.PASSED
        else:
            status = OperationStatus.SKIPPED
            note = ", ".join(sorted(operation.skip_reasons))
        return OperationRow(
            label=label,
            status=status,
            failures=failures,
            cases=operation.cases,
            note=note,
            # Hypothesis stops generating at the first failure and only shrinks it.
            stops_at_first_failure=True,
            failing_cases=cases,
            errors=operation.errors,
        )


def _latest_per_failure(groups: list[GroupedFailures], seen: set[str]) -> list[GroupedFailures]:
    # Hypothesis replays the minimal failing example last, so the latest case of each failure kind is kept.
    kept = []
    for group in reversed(groups):
        group.failures = [failure for failure in group.failures if failure.title not in seen]
        if group.failures:
            seen.update(failure.title for failure in group.failures)
            kept.append(group)
    kept.reverse()
    return kept
