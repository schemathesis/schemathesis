"""Reduction of a finished run into the numbers the SUMMARY block reports."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from schemathesis.config import SchemathesisWarning
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
class WarningData:
    missing_auth: dict[int, set[str]]
    missing_test_data: set[str]
    validation_mismatch: set[str]
    missing_deserializer: dict[str, dict[str, set[str]]]
    unused_openapi_auth: set[str]
    unsupported_regex: dict[str, set[str]]
    method_not_allowed: set[str]
    constants_extraction: set[str]
    unmatched_filter: set[str]

    def __init__(
        self,
        missing_auth: dict[int, set[str]] | None = None,
        missing_test_data: set[str] | None = None,
        validation_mismatch: set[str] | None = None,
        missing_deserializer: dict[str, dict[str, set[str]]] | None = None,
        unused_openapi_auth: set[str] | None = None,
        unsupported_regex: dict[str, set[str]] | None = None,
        method_not_allowed: set[str] | None = None,
        constants_extraction: set[str] | None = None,
        unmatched_filter: set[str] | None = None,
    ) -> None:
        self.missing_auth = missing_auth or {}
        self.missing_test_data = missing_test_data or set()
        self.validation_mismatch = validation_mismatch or set()
        self.missing_deserializer = missing_deserializer or {}
        self.unused_openapi_auth = unused_openapi_auth or set()
        self.unsupported_regex = unsupported_regex or {}
        self.method_not_allowed = method_not_allowed or set()
        self.constants_extraction = constants_extraction or set()
        self.unmatched_filter = unmatched_filter or set()

    def as_labels(self) -> dict[str, list[str]]:
        """Every warning kind mapped to the affected labels; empty kinds stay present."""
        return {
            SchemathesisWarning.MISSING_AUTH.value: sorted(
                {label for labels in self.missing_auth.values() for label in labels}
            ),
            SchemathesisWarning.MISSING_TEST_DATA.value: sorted(self.missing_test_data),
            SchemathesisWarning.VALIDATION_MISMATCH.value: sorted(self.validation_mismatch),
            SchemathesisWarning.MISSING_DESERIALIZER.value: sorted(
                {label for group in self.missing_deserializer.values() for label in group}
            ),
            SchemathesisWarning.UNUSED_OPENAPI_AUTH.value: sorted(self.unused_openapi_auth),
            SchemathesisWarning.UNSUPPORTED_REGEX.value: sorted(self.unsupported_regex),
            SchemathesisWarning.METHOD_NOT_ALLOWED.value: sorted(self.method_not_allowed),
            SchemathesisWarning.CONSTANTS_EXTRACTION.value: sorted(self.constants_extraction),
            SchemathesisWarning.UNMATCHED_FILTER.value: sorted(self.unmatched_filter),
        }

    @property
    def is_empty(self) -> bool:
        return not bool(
            self.missing_auth
            or self.missing_test_data
            or self.validation_mismatch
            or self.missing_deserializer
            or self.unused_openapi_auth
            or self.unsupported_regex
            or self.method_not_allowed
            or self.constants_extraction
            or self.unmatched_filter
        )

    @property
    def kind_count(self) -> int:
        """Count distinct warning kinds currently recorded."""
        return sum(
            1
            for warnings in (
                self.missing_auth,
                self.missing_test_data,
                self.validation_mismatch,
                self.missing_deserializer,
                self.unused_openapi_auth,
                self.unsupported_regex,
                self.method_not_allowed,
                self.constants_extraction,
                self.unmatched_filter,
            )
            if warnings
        )


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
    # `Failure` subclass name; the stable identifier to group and gate on.
    type: str
    title: str
    severity: Severity
    count: int
    operations: list[str]


@dataclass(slots=True)
class ErrorGroup:
    title: str
    count: int


@dataclass(slots=True)
class SummaryData:
    # `None` when the schema never loaded, which is not the same as zero operations.
    operations: OperationsSummary | None
    # Empty for `st fuzz`, which has no phase concept.
    phases: dict[PhaseName, tuple[Status, PhaseSkipReason | None]]
    test_cases: TestCasesSummary
    failures: list[FailureGroup]
    errors: list[ErrorGroup]
    warnings: WarningData

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
        warnings: WarningData,
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
            warnings=warnings,
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


def build_operations(
    *, api_statistic: ApiStatistic | None, statistic: Statistic, errors: set[events.NonFatalError]
) -> OperationsSummary | None:
    """Operation counts without phase context; `st run` uses `SummaryData.from_run` instead."""
    if api_statistic is None:
        return None
    errored = len(
        {
            error.label
            for error in errors
            if error.related_to_operation and error.label not in statistic.tested_operations
        }
    )
    return OperationsSummary(
        total=api_statistic.operations.total,
        selected=api_statistic.operations.selected,
        tested=len(statistic.tested_operations),
        errored=errored,
        skipped=0,
        skip_reasons=[],
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
    # Grouped by title, matching the terminal. `type` comes from the first failure in each group;
    # titles are derived from the failure class, so the two stay in step.
    groups: dict[str, FailureGroup] = {}
    for label, grouped in statistic.failures.items():
        for entry in grouped.values():
            for failure in entry.failures:
                group = groups.get(failure.title)
                if group is None:
                    group = FailureGroup(
                        type=failure.__class__.__name__,
                        title=failure.title,
                        severity=failure.severity,
                        count=0,
                        operations=[],
                    )
                    groups[failure.title] = group
                group.count += 1
                # Run-level checks are not tied to an API operation.
                if label != RUN_CHECKS_LABEL and label not in group.operations:
                    group.operations.append(label)
    for group in groups.values():
        group.operations.sort()
    return sorted(groups.values(), key=lambda group: (group.severity, group.title))


def reduce_errors(errors: set[events.NonFatalError]) -> list[ErrorGroup]:
    counts: dict[str, int] = {}
    for error in errors:
        counts[error.info.title] = counts.get(error.info.title, 0) + 1
    return [ErrorGroup(title=title, count=counts[title]) for title in sorted(counts)]
