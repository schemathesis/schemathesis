from __future__ import annotations

import datetime
from collections import Counter
from typing import TYPE_CHECKING

from schemathesis.cli.commands.run.handlers.base import EventHandler
from schemathesis.cli.commands.run.handlers.output import UNMATCHED_FILTER_TIP
from schemathesis.cli.events import LoadingFinished, LoadingStarted
from schemathesis.cli.output import LOADER_ERROR_SUGGESTIONS
from schemathesis.cli.summary import running_time
from schemathesis.core.errors import LoaderError, LoaderErrorKind
from schemathesis.core.output.sanitization import sanitize_url
from schemathesis.engine import Status, StopReason, events
from schemathesis.engine.errors import EngineErrorInfo
from schemathesis.reporting.html.model import (
    ErrorEntry,
    NothingTested,
    OperationRow,
    OperationStatus,
    ReportData,
    ReportMeta,
)

if TYPE_CHECKING:
    from pathlib import Path

    from schemathesis.cli.context import BaseExecutionContext
    from schemathesis.cli.summary import SummaryData
    from schemathesis.config import SanitizationConfig
    from schemathesis.engine.recorder import ScenarioRecorder
    from schemathesis.engine.run import PhaseName


class HtmlReportHandler(EventHandler["BaseExecutionContext"]):
    __slots__ = (
        "output_dir",
        "location",
        "base_url",
        "started_at",
        "finished",
        "fatal_error",
        "last_phase",
        "cases",
        "skip_reasons",
        "errors",
    )

    def __init__(self, output_dir: Path) -> None:
        self.output_dir = output_dir
        self.location: str | None = None
        self.base_url: str | None = None
        self.started_at: float | None = None
        self.finished: events.EngineFinished | None = None
        self.fatal_error: ErrorEntry | None = None
        self.last_phase: PhaseName | None = None
        self.cases: Counter[str] = Counter()
        self.skip_reasons: dict[str, set[str]] = {}
        # The first error title per operation.
        self.errors: dict[str, str] = {}

    def handle_event(self, ctx: BaseExecutionContext, event: events.EngineEvent) -> None:
        # A failed load never reaches `LoadingFinished`, so the location is recorded as soon as it is known.
        if isinstance(event, LoadingStarted):
            self.location = event.location
        elif isinstance(event, LoadingFinished):
            self.location = event.location
            self.base_url = event.base_url
        elif isinstance(event, events.EngineStarted):
            self.started_at = event.timestamp
        elif isinstance(event, events.PhaseStarted):
            self.last_phase = event.phase.name
        elif isinstance(event, events.ScenarioFinished):
            self._count_cases(event.recorder)
            if event.status == Status.SKIP and event.skip_reason is not None and event.label is not None:
                self.skip_reasons.setdefault(event.label, set()).add(event.skip_reason)
        elif isinstance(event, events.FuzzScenarioFinished):
            self._count_cases(event.recorder)
        elif isinstance(event, events.NonFatalError):
            # Errors outside any operation never become rows: their label names no operation.
            self.errors.setdefault(event.label, event.info.title)
        elif isinstance(event, events.FatalError):
            self.fatal_error = _fatal_entry(event.exception, wait_for_schema=ctx.config.wait_for_schema)
        elif isinstance(event, events.EngineFinished):
            self.finished = event

    def _count_cases(self, recorder: ScenarioRecorder) -> None:
        for node in recorder.cases.values():
            self.cases[node.value.operation.label] += 1

    def _operation_rows(self, ctx: BaseExecutionContext) -> tuple[list[OperationRow], list[tuple[str, int]]]:
        statistic = ctx.statistic
        labels = (
            self.cases.keys()
            | statistic.failures.keys()
            | statistic.tested_operations
            | statistic.errored_operations
            | self.errors.keys()
            | self.skip_reasons.keys()
        )
        stopped_early = self.finished is None or self.finished.stop_reason is not StopReason.COMPLETED
        rows = []
        unattributed: Counter[str] = Counter()
        for label in labels:
            failures = sorted(
                {failure.title for group in statistic.failures.get(label, {}).values() for failure in group.failures}
            )
            operation = ctx.find_operation_by_label(label) if ctx.find_operation_by_label is not None else None
            # Stateful, run-level and undeclared-method failures carry labels that name no operation.
            if operation is None:
                unattributed.update(
                    failure.title for group in statistic.failures.get(label, {}).values() for failure in group.failures
                )
                continue
            note = None
            if failures:
                status = OperationStatus.FAILED
            elif label in statistic.tested_operations:
                status = OperationStatus.PASSED
            elif label in self.errors or label in statistic.errored_operations:
                status = OperationStatus.ERRORED
                note = self.errors.get(label)
            elif stopped_early:
                # The footer counts these as not run, matching the operations count in the hero.
                continue
            else:
                status = OperationStatus.SKIPPED
                reasons = self.skip_reasons.get(label, set())
                if label in statistic.operations_without_checks:
                    reasons = reasons | {"No checks ran"}
                note = ", ".join(sorted(reasons)) or None
            continue_on_failure = (
                ctx.config.operations.get_for_operation(operation=operation).continue_on_failure
                or ctx.config.continue_on_failure
            )
            rows.append(
                OperationRow(
                    label=label,
                    status=status,
                    failures=failures,
                    cases=self.cases[label],
                    note=note,
                    stops_at_first_failure=not continue_on_failure,
                )
            )
        return rows, _by_count(unattributed)

    def shutdown(self, ctx: BaseExecutionContext) -> None:
        from schemathesis.reporting._command import get_command_representation
        from schemathesis.reporting.html import write_report

        sanitization = ctx.config.output.sanitization
        sanitization_config = sanitization if sanitization.enabled else None
        summary = ctx.summary()
        operations, unattributed_failures = self._operation_rows(ctx)
        data = ReportData(
            meta=ReportMeta(
                generated_at=datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
                location=_sanitized(self.location, sanitization_config),
                base_url=_sanitized(self.base_url, sanitization_config),
                command=get_command_representation(sanitization_config),
                seed=ctx.config.seed,
            ),
            summary=summary,
            fatal_error=self.fatal_error,
            nothing_tested=_nothing_tested(ctx.nothing_tested_reason, summary),
            last_phase=self.last_phase,
            operations=operations,
            unattributed_failures=unattributed_failures,
            running_time=running_time(self.started_at, self.finished),
            stop_reason=self.finished.stop_reason if self.finished is not None else StopReason.INTERRUPTED,
            started=self.started_at is not None,
            complete=self.finished is not None,
            exit_code=ctx.exit_code,
        )
        write_report(data, self.output_dir)


def _by_count(counts: Counter[str]) -> list[tuple[str, int]]:
    return sorted(counts.items(), key=lambda item: (-item[1], item[0]))


def _fatal_entry(exception: Exception, *, wait_for_schema: float | int | None) -> ErrorEntry:
    if isinstance(exception, LoaderError):
        tip = LOADER_ERROR_SUGGESTIONS.get(exception.kind)
        # The run already waited for the schema, so suggesting a wait would repeat what failed.
        if exception.kind == LoaderErrorKind.CONNECTION_OTHER and wait_for_schema is not None:
            tip = None
        return ErrorEntry(
            title="Failed to load specification", message="\n".join([exception.message, *exception.extras]), tip=tip
        )
    info = EngineErrorInfo(error=exception)
    return ErrorEntry(title=info.title, message=info.message, tip=None)


def _nothing_tested(reason: str | None, summary: SummaryData) -> NothingTested | None:
    if reason is None:
        return None
    operations = summary.operations
    if operations is not None and operations.total and not operations.selected:
        return NothingTested(
            title=f"0 of {operations.total} operation{'' if operations.total == 1 else 's'} matched the filters",
            filters=sorted(summary.warnings.unmatched_filter),
            tip=UNMATCHED_FILTER_TIP,
        )
    return NothingTested(title=reason, filters=[], tip=None)


def _sanitized(url: str | None, sanitization_config: SanitizationConfig | None) -> str | None:
    # The schema location may be a file path; only URLs can carry credentials.
    if url is None or sanitization_config is None or not url.startswith(("http://", "https://")):
        return url
    return sanitize_url(url, config=sanitization_config)
