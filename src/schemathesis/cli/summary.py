"""Reduction of a finished run into the numbers the SUMMARY block reports."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from schemathesis.core.failures import RUN_CHECKS_LABEL, Severity
from schemathesis.engine import Status
from schemathesis.engine.run import PhaseName, PhaseSkipReason

if TYPE_CHECKING:
    from schemathesis.core.statistic import ApiStatistic
    from schemathesis.engine import StopReason, events
    from schemathesis.engine.statistic import Statistic

# Phases whose operations can error out before any test runs.
UNIT_PHASES = (PhaseName.EXAMPLES, PhaseName.COVERAGE, PhaseName.FUZZING)


@dataclass(slots=True)
class OperationsSummary:
    total: int
    selected: int
    tested: int
    errored: int
    skipped: int
    skip_reasons: list[str]


@dataclass(slots=True)
class TestCasesSummary:
    generated: int
    with_failures: int
    unique_failures: int
    without_checks: int


@dataclass(slots=True)
class FailureGroup:
    title: str
    severity: Severity
    count: int


@dataclass(slots=True)
class ErrorGroup:
    title: str
    count: int


@dataclass(slots=True)
class SummaryData:
    # `None` when the schema never loaded, which is not the same as zero operations.
    operations: OperationsSummary | None
    phases: dict[PhaseName, tuple[Status, PhaseSkipReason | None]]
    test_cases: TestCasesSummary
    failures: list[FailureGroup]
    errors: list[ErrorGroup]

    @classmethod
    def from_run(
        cls,
        *,
        api_statistic: ApiStatistic | None,
        statistic: Statistic,
        errors: set[events.NonFatalError],
        phases: dict[PhaseName, tuple[Status, PhaseSkipReason | None]],
        skip_reasons: dict[str, set[str]],
        stop_reason: StopReason,
    ) -> SummaryData:
        return cls(
            operations=_reduce_operations(
                api_statistic=api_statistic,
                statistic=statistic,
                errors=errors,
                phases=phases,
                skip_reasons=skip_reasons,
                stop_reason=stop_reason,
            )
            if api_statistic is not None
            else None,
            phases=phases,
            test_cases=reduce_test_cases(statistic),
            failures=reduce_failures(statistic),
            errors=reduce_errors(errors),
        )


def _reduce_operations(
    *,
    api_statistic: ApiStatistic,
    statistic: Statistic,
    errors: set[events.NonFatalError],
    phases: dict[PhaseName, tuple[Status, PhaseSkipReason | None]],
    skip_reasons: dict[str, set[str]],
    stop_reason: StopReason,
) -> OperationsSummary:
    errored = len(
        {
            error.label
            for error in errors
            # Some API operations may have some tests before they have an error
            if error.phase in UNIT_PHASES
            and error.label not in statistic.tested_operations
            and error.related_to_operation
        }
    )
    # API operations that are skipped due to fail-fast are counted here as well
    skipped = api_statistic.operations.selected - len(statistic.tested_operations) - errored
    # An operation tested in one phase may have been skipped in another; its reason does not explain
    # the operations counted above, which were never tested at all.
    explained = {label: reasons for label, reasons in skip_reasons.items() if label not in statistic.tested_operations}
    reasons = {reason for values in explained.values() for reason in values}
    # Cases ran, but no selected check applied to them. Operations tested elsewhere are not in the count above.
    without_checks = statistic.operations_without_checks - statistic.tested_operations
    if without_checks:
        reasons.add("No checks ran")
    if skipped > len(explained.keys() | without_checks):
        if stop_reason.skip_explanation is not None:
            reasons.add(stop_reason.skip_explanation)
        elif any(phases[phase][0] == Status.ERROR for phase in UNIT_PHASES):
            reasons.add("Phase errored")
    return OperationsSummary(
        total=api_statistic.operations.total,
        selected=api_statistic.operations.selected,
        tested=len(statistic.tested_operations),
        errored=errored,
        skipped=skipped,
        skip_reasons=sorted(reasons),
    )


def reduce_test_cases(statistic: Statistic) -> TestCasesSummary:
    # `after_run` checks are not tied to cases, so they are counted in the "Failures" block, not here.
    unique_failures = sum(
        len(group.failures)
        for label, grouped in statistic.failures.items()
        if label != RUN_CHECKS_LABEL
        for group in grouped.values()
    )
    return TestCasesSummary(
        generated=statistic.total_cases,
        with_failures=statistic.cases_with_failures,
        unique_failures=unique_failures,
        without_checks=statistic.cases_without_checks,
    )


def reduce_failures(statistic: Statistic) -> list[FailureGroup]:
    counts: dict[str, tuple[Severity, int]] = {}
    for grouped in statistic.failures.values():
        for group in grouped.values():
            for failure in group.failures:
                severity, count = counts.get(failure.title, (failure.severity, 0))
                counts[failure.title] = (severity, count + 1)
    return [
        FailureGroup(title=title, severity=severity, count=count)
        for title, (severity, count) in sorted(counts.items(), key=lambda entry: (entry[1][0], entry[0]))
    ]


def reduce_errors(errors: set[events.NonFatalError]) -> list[ErrorGroup]:
    counts: dict[str, int] = {}
    for error in errors:
        counts[error.info.title] = counts.get(error.info.title, 0) + 1
    return [ErrorGroup(title=title, count=counts[title]) for title in sorted(counts)]
