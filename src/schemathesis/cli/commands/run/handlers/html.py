from __future__ import annotations

import datetime
from collections import Counter
from typing import TYPE_CHECKING, NamedTuple

from schemathesis.cli.commands.run.context import ExecutionContext
from schemathesis.cli.commands.run.handlers.base import EventHandler
from schemathesis.cli.commands.run.handlers.output import (
    UNMATCHED_FILTER_TIP,
    extraction_failure_reason,
    extraction_failure_steps,
    warning_blocks,
)
from schemathesis.cli.events import LoadingFinished, LoadingStarted
from schemathesis.cli.output import LOADER_ERROR_SUGGESTIONS, replay_command
from schemathesis.cli.summary import running_time
from schemathesis.core.errors import LoaderError, LoaderErrorKind
from schemathesis.core.failures import group_failures, reproducible_by_identity
from schemathesis.core.output import format_response_payload
from schemathesis.core.output.sanitization import sanitize_url
from schemathesis.engine import Status, StopReason, events
from schemathesis.engine.errors import EngineErrorInfo
from schemathesis.engine.run import INTERNAL_PHASES, PhaseName
from schemathesis.generation.stateful import STATEFUL_TESTS_LABEL
from schemathesis.reporting.html.model import (
    Command,
    ErrorEntry,
    ExtractionNote,
    FailingCase,
    FailureEntry,
    NothingTested,
    OperationRow,
    OperationStatus,
    ReportData,
    ReportMeta,
    split_label,
)

if TYPE_CHECKING:
    from pathlib import Path

    from schemathesis.cli.commands.run.handlers.output import WarningBlock
    from schemathesis.cli.context import BaseExecutionContext
    from schemathesis.cli.summary import SummaryData
    from schemathesis.config import OutputConfig, SanitizationConfig
    from schemathesis.engine.recorder import RecordedScenario
    from schemathesis.engine.statistic import GroupedFailures
    from schemathesis.generation.stateful.state_machine import ExtractionFailure
    from schemathesis.schemas import APIOperation


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
        # The first error of each exception type per label, the way the terminal lists them.
        self.errors: dict[str, dict[type, ErrorEntry]] = {}

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
            self._record(event.recorder)
            if event.status == Status.SKIP and event.skip_reason is not None and event.label is not None:
                self.skip_reasons.setdefault(event.label, set()).add(event.skip_reason)
        elif isinstance(event, events.FuzzScenarioFinished):
            self._record(event.recorder)
        elif isinstance(event, events.NonFatalError):
            entries = self.errors.setdefault(event.label, {})
            if type(event.value) not in entries:
                entries[type(event.value)] = _error_entry(event.info)
        elif isinstance(event, events.FatalError):
            self.fatal_error = _fatal_entry(event.exception, wait_for_schema=ctx.config.wait_for_schema)
        elif isinstance(event, events.EngineFinished):
            self.finished = event

    def _record(self, recorder: RecordedScenario) -> None:
        for node in recorder.cases.values():
            self.cases[node.value.operation.label] += 1

    def _operation_rows(self, ctx: BaseExecutionContext) -> Rows:
        statistic = ctx.statistic

        def find_operation(label: str) -> APIOperation | None:
            return ctx.find_operation_by_label(label) if ctx.find_operation_by_label is not None else None

        failing_cases: dict[str, list[FailingCase]] = {}
        # Operations with failures from their own cases, not only from stateful sequences.
        failed_directly: set[str] = set()
        unattributed_cases = []
        unattributed: Counter[str] = Counter()
        for label, groups in statistic.failures.items():
            for group in groups.values():
                case = _failing_case(group, config=ctx.config.output, record_crashes=ctx.config.cache.enabled)
                owner = label
                # Stateful failures are keyed by the scenario; the row is the operation whose request failed.
                if label == STATEFUL_TESTS_LABEL:
                    owner = next((step.operation for step in group.steps if step.case_id == group.case_id), label)
                else:
                    failed_directly.add(label)
                # Run-level and undeclared-method failures carry labels that name no operation.
                if find_operation(owner) is None:
                    unattributed_cases.append((label, case))
                    unattributed.update(failure.title for failure in group.failures)
                else:
                    failing_cases.setdefault(owner, []).append(case)
        labels = (
            self.cases.keys()
            | failing_cases.keys()
            | statistic.tested_operations
            | statistic.errored_operations
            | self.errors.keys()
            | self.skip_reasons.keys()
        )
        stopped_early = self.finished is None or self.finished.stop_reason is not StopReason.COMPLETED
        rows = []
        for label in labels:
            operation = find_operation(label)
            if operation is None:
                continue
            cases = failing_cases.get(label, [])
            failures = sorted({failure.title for case in cases for failure in case.failures})
            errors = list(self.errors.get(label, {}).values())
            note = None
            if failures:
                status = OperationStatus.FAILED
            elif errors or (label in statistic.errored_operations and label not in statistic.tested_operations):
                status = OperationStatus.ERRORED
                note = errors[0].title if errors else None
            elif label in statistic.tested_operations:
                status = OperationStatus.PASSED
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
                    stops_at_first_failure=label in failed_directly and not continue_on_failure,
                    failing_cases=cases,
                    errors=errors,
                )
            )
        unattributed_errors = [
            (label, error)
            for label, errors in sorted(self.errors.items())
            if find_operation(label) is None
            for error in errors.values()
        ]
        return Rows(
            operations=rows,
            unattributed_failures=_by_count(unattributed),
            unattributed_cases=unattributed_cases,
            unattributed_errors=unattributed_errors,
        )

    def shutdown(self, ctx: BaseExecutionContext) -> None:
        from schemathesis.reporting._command import get_command_representation
        from schemathesis.reporting.html import write_report

        sanitization = ctx.config.output.sanitization
        sanitization_config = sanitization if sanitization.enabled else None
        summary = ctx.summary()
        rows = self._operation_rows(ctx)
        payload = self.finished.payload if self.finished is not None else None
        # `st fuzz` collects no warnings.
        warnings = warning_blocks(ctx) if isinstance(ctx, ExecutionContext) else []
        nothing_tested = _nothing_tested(ctx.nothing_tested_reason, summary)
        if nothing_tested is not None and nothing_tested.filters:
            # The verdict already lists the filters that matched nothing, with the same tip.
            warnings = [block for block in warnings if block.entity != "filter"]
        warned: dict[str, list[WarningBlock]] = {}
        for block in warnings:
            for label in sorted({item.operation for group in block.groups for item in group.items if item.operation}):
                warned.setdefault(label, []).append(block)
        for row in rows.operations:
            row.warnings = warned.get(row.label, [])
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
            nothing_tested=nothing_tested,
            last_phase=self.last_phase,
            operations=rows.operations,
            unattributed_failures=rows.unattributed_failures,
            unattributed_cases=rows.unattributed_cases,
            unattributed_errors=rows.unattributed_errors,
            warnings=warnings,
            startup_warnings=ctx.startup_warnings if isinstance(ctx, ExecutionContext) else [],
            phases=_phases(ctx) if isinstance(ctx, ExecutionContext) else [],
            checks=_enabled_checks(ctx),
            reauth_count=payload.reauth_count if payload is not None else 0,
            reauth_broke=payload.reauth_broke if payload is not None else False,
            extraction_failures=[
                ExtractionNote(
                    link=failure.id,
                    case_id=failure.case_id,
                    reason=extraction_failure_reason(failure),
                    commands=_extraction_commands(failure),
                    status_code=failure.response.status_code,
                    body=format_response_payload(failure.response, config=ctx.config.output),
                )
                for failure in ctx.statistic.extraction_failures
            ],
            running_time=running_time(self.started_at, self.finished),
            stop_reason=self.finished.stop_reason if self.finished is not None else StopReason.INTERRUPTED,
            started=self.started_at is not None,
            complete=self.finished is not None,
            exit_code=ctx.exit_code,
        )
        write_report(data, self.output_dir)


class Rows(NamedTuple):
    operations: list[OperationRow]
    unattributed_failures: list[tuple[str, int]]
    unattributed_cases: list[tuple[str, FailingCase]]
    unattributed_errors: list[tuple[str, ErrorEntry]]


def _failing_case(group: GroupedFailures, *, config: OutputConfig, record_crashes: bool) -> FailingCase:
    response = group.response
    return FailingCase(
        case_id=group.case_id,
        failures=[
            FailureEntry(title=title, messages=[failure.message for failure in failures])
            for title, failures in group_failures(group.failures)
        ],
        status_code=response.status_code if response is not None else None,
        body=format_response_payload(response, config=config) if response is not None else None,
        commands=_commands(group),
        replay=replay_command(group, record_crashes=record_crashes),
        auth_identity=group.auth_identity if reproducible_by_identity(group.failures) else None,
    )


def _commands(group: GroupedFailures) -> list[Command]:
    if group.steps:
        commands = []
        for step in group.steps:
            method, path = _request_name(step.operation, step.method, step.path)
            commands.append(Command(curl=step.curl, method=method, path=path, failed=step.case_id == group.case_id))
        return commands
    if group.code_sample is None:
        return []
    return [Command(curl=group.code_sample, method=None, path=None, failed=False)]


_PHASE_OUTCOMES = {
    Status.SUCCESS: "passed",
    Status.FAILURE: "failed",
    Status.ERROR: "errored",
    Status.INTERRUPTED: "interrupted",
}


def _phases(ctx: ExecutionContext) -> list[tuple[str, str, str]]:
    phases = []
    for phase in PhaseName:
        if phase in INTERNAL_PHASES:
            continue
        status, skip_reason = ctx.phases[phase]
        if status == Status.SKIP:
            phases.append((phase.display, "skipped", skip_reason.display if skip_reason is not None else "skipped"))
        else:
            phases.append((phase.display, _PHASE_OUTCOMES[status], _PHASE_OUTCOMES[status]))
    return phases


def _enabled_checks(ctx: BaseExecutionContext) -> list[str]:
    from schemathesis.checks import CHECKS, max_response_time

    config = ctx.config.checks_config_for()
    names = [check.__name__ for check in CHECKS.get_all() if config.get_by_name(name=check.__name__).enabled]
    if config.max_response_time.enabled:
        names.append(max_response_time.__name__)
    return sorted(names)


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


def _extraction_commands(failure: ExtractionFailure) -> list[Command]:
    commands = []
    for (case, _), (_, curl) in zip(reversed(failure.history), extraction_failure_steps(failure), strict=True):
        method, path = _request_name(case.operation.label, case.method, case.formatted_path)
        commands.append(Command(curl=curl, method=method, path=path, failed=False))
    return commands


def _request_name(operation: str, method: str, path: str) -> tuple[str | None, str]:
    # Without a method in the label (GraphQL), every request goes to the same URL, so the step names the field.
    if split_label(operation)[0] is None:
        return None, operation
    return method, path


def _error_entry(info: EngineErrorInfo) -> ErrorEntry:
    return ErrorEntry(
        title=info.title,
        message=info.message or str(info),
        tip=info.suggestion(),
        details=info.details,
        reproduce=info.code_sample,
    )


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
