from __future__ import annotations

import datetime
import os
import time
from collections import Counter
from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple
from urllib.parse import quote

from schemathesis.cli.commands.run.context import ExecutionContext
from schemathesis.cli.commands.run.handlers.base import EventHandler
from schemathesis.cli.commands.run.handlers.output import (
    UNMATCHED_FILTER_TIP,
    extraction_failure_reason,
    extraction_failure_steps,
    warning_blocks,
)
from schemathesis.cli.events import LoadingFinished, LoadingStarted
from schemathesis.cli.output import loader_error_tip, replay_command
from schemathesis.core.errors import LoaderError
from schemathesis.core.output import format_response_payload
from schemathesis.core.output.sanitization import sanitized
from schemathesis.engine import Status, StopReason, events
from schemathesis.engine.errors import EngineErrorInfo
from schemathesis.engine.run import INTERNAL_PHASES, PhaseName, PhaseSkipReason
from schemathesis.engine.summary import running_time
from schemathesis.generation.stateful import STATEFUL_TESTS_LABEL
from schemathesis.reporting.html.build import enabled_checks, failing_case, failure_titles, request_name
from schemathesis.reporting.html.model import (
    AFTER_RUN,
    Command,
    ErrorEntry,
    ExtractionNote,
    FailingCase,
    NothingTested,
    OperationRow,
    OperationStatus,
    PhaseSpan,
    ReportData,
    ReportMeta,
    plural,
)

if TYPE_CHECKING:
    from schemathesis.cli.context import BaseExecutionContext
    from schemathesis.engine.recorder import RecordedScenario
    from schemathesis.engine.statistic import GroupedFailures, Statistic
    from schemathesis.engine.summary import SummaryData, WarningBlock
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
        "phase_started",
        "phase_spans",
        "found",
        "phase_errors",
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
        # Labels of operations that reported errors, including operations that failed to load.
        self.errored_labels: set[str] = set()
        self.phase_started: dict[PhaseName, float] = {}
        self.phase_spans: list[PhaseSpan] = []
        # Key of each failure group (its case id) -> seconds from the run start when it first appeared, and the phase.
        self.found: dict[str, tuple[float, str]] = {}
        # Distinct errors in each phase, as (label, exception type).
        self.phase_errors: dict[PhaseName, set[tuple[str, type]]] = {}

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
            self._on_phase_started(event)
        elif isinstance(event, events.PhaseFinished):
            self._on_phase_finished(event)
        elif isinstance(event, events.ScenarioFinished) and event.is_test_scenario:
            self._on_scenario_finished(ctx, event)
        elif isinstance(event, events.FuzzScenarioFinished):
            self._on_fuzz_scenario_finished(ctx, event)
        elif isinstance(event, events.NonFatalError):
            self._on_error(event, base_url_is_set=ctx.config.base_url is not None)
        elif isinstance(event, events.FatalError):
            self.fatal_error = _fatal_entry(event.exception, wait_for_schema=ctx.config.wait_for_schema)
        elif isinstance(event, events.EngineFinished):
            self.finished = event
            # Checks that run once after all phases report here.
            self._record_found(ctx, None, AFTER_RUN, event.timestamp)
            self._close_open_phases(event.timestamp)

    def _on_phase_started(self, event: events.PhaseStarted) -> None:
        self.last_phase = event.phase.name
        if event.phase.name not in INTERNAL_PHASES:
            self.phase_started[event.phase.name] = event.timestamp

    def _on_phase_finished(self, event: events.PhaseFinished) -> None:
        started = self.phase_started.pop(event.phase.name, None)
        # A phase without applicable checks still sent its requests.
        ran = event.status != Status.SKIP or event.phase.skip_reason is PhaseSkipReason.NO_CHECKS_RAN
        if started is not None and ran:
            self._add_span(event.phase.name, started, event.timestamp)

    def _close_open_phases(self, now: float) -> None:
        for phase, started in self.phase_started.items():
            self._add_span(phase, started, now)
        self.phase_started.clear()

    def _on_scenario_finished(self, ctx: BaseExecutionContext, event: events.ScenarioFinished) -> None:
        self._record(event.recorder)
        self._record_found(ctx, event.recorder, event.phase.display, event.timestamp)
        if event.status == Status.SKIP and event.skip_reason is not None and event.label is not None:
            self.skip_reasons.setdefault(event.label, set()).add(event.skip_reason)

    def _on_fuzz_scenario_finished(self, ctx: BaseExecutionContext, event: events.FuzzScenarioFinished) -> None:
        # `st fuzz` has no phases; the timeline shows its whole run as one fuzzing span.
        if self.started_at is not None:
            self.phase_started.setdefault(PhaseName.FUZZING, self.started_at)
        self._record(event.recorder)
        self._record_found(ctx, event.recorder, PhaseName.FUZZING.display, event.timestamp)

    def _on_error(self, event: events.NonFatalError, *, base_url_is_set: bool) -> None:
        entries = self.errors.setdefault(event.label, {})
        if event.related_to_operation:
            self.errored_labels.add(event.label)
        if type(event.value) not in entries:
            entries[type(event.value)] = _error_entry(event.info, base_url_is_set=base_url_is_set)
        if event.phase is not None:
            self.phase_errors.setdefault(event.phase, set()).add((event.label, type(event.value)))

    def _record(self, recorder: RecordedScenario) -> None:
        for node in recorder.cases.values():
            self.cases[node.value.operation.label] += 1

    def _add_span(self, phase: PhaseName, started: float, finished: float) -> None:
        if self.started_at is not None:
            self.phase_spans.append(
                PhaseSpan(phase=phase.display, start=started - self.started_at, end=finished - self.started_at)
            )

    def _record_found(
        self, ctx: BaseExecutionContext, recorder: RecordedScenario | None, phase: str, timestamp: float
    ) -> None:
        if self.started_at is None:
            return
        if recorder is not None and not recorder.failing_case_ids():
            return
        for groups in ctx.statistic.failures.values():
            for case_id in groups:
                if case_id in self.found:
                    continue
                # The failing response's own time; the scenario ends later, after shrinking.
                interaction = recorder.interactions.get(case_id) if recorder is not None else None
                found_at = interaction.timestamp if interaction is not None else timestamp
                self.found[case_id] = (max(found_at - self.started_at, 0.0), phase)

    def _failing_case(self, ctx: BaseExecutionContext, key: str, group: GroupedFailures) -> FailingCase:
        case = failing_case(
            group, config=ctx.config.output, replay=replay_command(group, record_crashes=ctx.config.cache.enabled)
        )
        # Keyed like the run's failure groups: run-level failures have no case id.
        case.found_at, case.found_in = self.found.get(key, (None, None))
        return case

    def _operation_rows(self, ctx: BaseExecutionContext) -> Rows:
        statistic = ctx.statistic

        def names_operation(label: str) -> bool:
            # An operation that failed to load cannot be looked up again, yet its error names it.
            return label in self.errored_labels or ctx.find_operation(label) is not None

        operations: dict[str, APIOperation] = {}
        failing_cases: dict[str, list[FailingCase]] = {}
        # Operations with failures from their own cases, not only from stateful sequences.
        failed_directly: set[str] = set()
        unattributed_cases = []
        unattributed: Counter[str] = Counter()
        for label, groups in statistic.failures.items():
            for key, group in groups.items():
                case = self._failing_case(ctx, key, group)
                owner = label
                # Stateful failures are keyed by the scenario; the row is the operation whose request failed.
                if label == STATEFUL_TESTS_LABEL:
                    owner = next((step.operation for step in group.steps if step.case_id == group.case_id), label)
                else:
                    failed_directly.add(label)
                # Run-level and undeclared-method failures carry labels that name no operation.
                operation = ctx.find_operation(owner)
                if operation is None:
                    unattributed_cases.append((label, case))
                    unattributed.update(failure.title for failure in group.failures)
                else:
                    operations[owner] = operation
                    failing_cases.setdefault(owner, []).append(case)
        labels = (
            self.cases.keys()
            | failing_cases.keys()
            | statistic.tested_operations
            | statistic.errored_operations
            | self.errors.keys()
            | self.skip_reasons.keys()
        )
        known = _known_failure_titles(statistic)
        stopped_early = self.finished is None or self.finished.stop_reason is not StopReason.COMPLETED
        rows = []
        for label in labels:
            if not names_operation(label):
                continue
            cases = failing_cases.get(label, [])
            failures = failure_titles(cases)
            errors = list(self.errors.get(label, {}).values())
            note = errors[0].title if errors else None
            if failures:
                status = OperationStatus.FAILED
            elif label in statistic.tested_operations:
                # The terminal summary counts an operation that was also tested as tested, not errored.
                status = OperationStatus.PASSED
            elif errors or label in statistic.errored_operations:
                status = OperationStatus.ERRORED
            elif stopped_early:
                # The footer counts these as not run, matching the operations count in the hero.
                continue
            else:
                status = OperationStatus.SKIPPED
                reasons = self.skip_reasons.get(label, set())
                if label in statistic.operations_without_checks:
                    reasons = reasons | {"No checks ran"}
                note = ", ".join(sorted(reasons)) or None
            operation = operations.get(label)
            stops_at_first_failure = (
                operation is not None
                and label in failed_directly
                and not ctx.config.continue_on_failure_for(operation=operation)
            )
            rows.append(
                OperationRow(
                    label=label,
                    status=status,
                    failures=failures,
                    cases=self.cases[label],
                    note=note,
                    stops_at_first_failure=stops_at_first_failure,
                    failing_cases=cases,
                    errors=errors,
                    known_failures=known.get(label, []),
                )
            )
        unattributed_errors = [
            (label, error)
            for label, errors in sorted(self.errors.items())
            if not names_operation(label)
            for error in errors.values()
        ]
        return Rows(
            operations=rows,
            unattributed_failures=_by_count(unattributed),
            unattributed_cases=unattributed_cases,
            unattributed_errors=unattributed_errors,
        )

    def _phase_outcomes(self, phases: PhaseStatuses, stop_reason: StopReason) -> PhaseOutcomes:
        return PhaseOutcomes(
            errors=_phase_error_counts(phases, self.phase_errors),
            failed={phase for _, phase in self.found.values()},
            interrupted=_cut_phase(self.phase_spans) if stop_reason is StopReason.INTERRUPTED else None,
        )

    def shutdown(self, ctx: BaseExecutionContext) -> None:
        from schemathesis.reporting._command import get_command_representation
        from schemathesis.reporting.html import write_report

        sanitization = ctx.config.output.sanitization
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
        # An interrupted run never finishes the phase it was in; a finished run has none open.
        self._close_open_phases(time.time())
        stop_reason = self.finished.stop_reason if self.finished is not None else StopReason.INTERRUPTED
        outcomes = self._phase_outcomes(summary.phases, stop_reason)
        data = ReportData(
            meta=ReportMeta(
                generated_at=datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%d %H:%M:%S UTC"),
                location=sanitized(self.location, sanitization),
                base_url=sanitized(self.base_url, sanitization),
                command=get_command_representation(sanitization),
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
            startup_warnings=(
                [message for _, message in ctx.startup_warnings] if isinstance(ctx, ExecutionContext) else []
            ),
            phases=_phases(summary.phases, outcomes),
            checks=enabled_checks(ctx.config.checks_config_for()),
            reports=_report_links(ctx, self.output_dir),
            reauth_count=payload.reauth_count if payload is not None else 0,
            reauth_broke=payload.reauth_broke if payload is not None else False,
            extraction_failures=_extraction_notes(ctx),
            running_time=running_time(self.started_at, self.finished),
            phase_spans=self.phase_spans,
            stop_reason=stop_reason,
            started=self.started_at is not None,
            complete=self.finished is not None,
            exit_code=ctx.exit_code,
            phase_errors=outcomes.errors,
        )
        write_report(data, self.output_dir)


class Rows(NamedTuple):
    operations: list[OperationRow]
    unattributed_failures: list[tuple[str, int]]
    unattributed_cases: list[tuple[str, FailingCase]]
    unattributed_errors: list[tuple[str, ErrorEntry]]


_PHASE_OUTCOMES = {
    Status.SUCCESS: "passed",
    Status.FAILURE: "failed",
    Status.ERROR: "errored",
    Status.INTERRUPTED: "interrupted",
}


PhaseStatuses = dict[PhaseName, tuple[Status, PhaseSkipReason | None]]


class PhaseOutcomes(NamedTuple):
    # Distinct errors per phase name.
    errors: dict[str, int]
    # Phases that found a failure first.
    failed: set[str]
    # The phase the run was in when the user stopped it.
    interrupted: str | None


def _phase_error_counts(phases: PhaseStatuses, errors: dict[PhaseName, set[tuple[str, type]]]) -> dict[str, int]:
    counts = {}
    for phase, (status, _) in phases.items():
        # The engine drops a run-level error that repeats one shown on an operation; the phase still had it.
        count = len(errors.get(phase, ())) or int(status == Status.ERROR)
        if count and phase not in INTERNAL_PHASES:
            counts[phase.display] = count
    return counts


def _cut_phase(spans: list[PhaseSpan]) -> str | None:
    """The phase whose span ends last, the one an interrupted run cut short."""
    return max(spans, key=lambda span: span.end).phase if spans else None


def _skip_outcome(reason: PhaseSkipReason | None) -> str:
    if reason is PhaseSkipReason.FAILURE_LIMIT_REACHED:
        return "not run, failure limit"
    return reason.display if reason is not None else "skipped"


def _phase_outcome(name: str, status: Status, outcomes: PhaseOutcomes) -> tuple[str, str]:
    """The phase kind and its outcome text, e.g. ("failed", "failed, 1 error")."""
    errors = outcomes.errors.get(name, 0)
    if name == outcomes.interrupted:
        kind = "interrupted"
    elif name in outcomes.failed or status == Status.FAILURE:
        kind = "failed"
    elif errors:
        return "errored", plural(errors, "error")
    else:
        kind = _PHASE_OUTCOMES[status]
    return kind, f"{kind}, {plural(errors, 'error')}" if errors else kind


def _phases(statuses: PhaseStatuses, outcomes: PhaseOutcomes) -> list[tuple[str, str, str]]:
    phases = []
    for phase, (status, skip_reason) in statuses.items():
        if phase in INTERNAL_PHASES:
            continue
        if status == Status.SKIP:
            phases.append((phase.display, "skipped", _skip_outcome(skip_reason)))
        else:
            phases.append((phase.display, *_phase_outcome(phase.display, status, outcomes)))
    return phases


def _known_failure_titles(statistic: Statistic) -> dict[str, list[str]]:
    titles: dict[str, list[str]] = {}
    for identity in statistic.known_failures:
        failure, _ = statistic.observed_failures[identity]
        titles.setdefault(identity[0], []).append(failure.title)
    return titles


def _report_links(ctx: BaseExecutionContext, output_dir: Path) -> list[tuple[str, str]]:
    base = output_dir.resolve()
    own = base / "index.html"
    links = []
    for name, path in ctx.all_reports():
        target = path.resolve()
        if target == own:
            continue
        try:
            href = quote(Path(os.path.relpath(target, base)).as_posix())
        except ValueError:
            # Paths on different Windows drives have no relative form.
            href = target.as_uri()
        links.append((name, href))
    return links


def _by_count(counts: Counter[str]) -> list[tuple[str, int]]:
    return sorted(counts.items(), key=lambda item: (-item[1], item[0]))


def _fatal_entry(exception: Exception, *, wait_for_schema: float | int | None) -> ErrorEntry:
    if isinstance(exception, LoaderError):
        return ErrorEntry(
            title="Failed to load specification",
            message="\n".join([exception.message, *exception.extras]),
            tip=loader_error_tip(exception, wait_for_schema=wait_for_schema),
        )
    info = EngineErrorInfo(error=exception)
    return ErrorEntry(title=info.title, message=info.message, tip=None)


def _extraction_notes(ctx: BaseExecutionContext) -> list[ExtractionNote]:
    return [
        ExtractionNote(
            link=failure.id,
            case_id=failure.case_id,
            reason=extraction_failure_reason(failure),
            commands=_extraction_commands(failure),
            status_code=failure.response.status_code,
            body=format_response_payload(failure.response, config=ctx.config.output),
        )
        for failure in ctx.statistic.extraction_failures
    ]


def _extraction_commands(failure: ExtractionFailure) -> list[Command]:
    commands = []
    for (case, _), (_, curl) in zip(reversed(failure.history), extraction_failure_steps(failure), strict=True):
        method, path = request_name(case.operation.label, case.method, case.formatted_path)
        commands.append(Command(curl=curl, method=method, path=path, failed=False))
    return commands


def _error_entry(info: EngineErrorInfo, *, base_url_is_set: bool) -> ErrorEntry:
    return ErrorEntry(
        title=info.title,
        message=info.message or str(info),
        tip=info.suggestion(base_url_is_set=base_url_is_set),
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
