from __future__ import annotations

import datetime
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from typing import TYPE_CHECKING

from schemathesis.core.output import prepare_response_payload
from schemathesis.engine import Status
from schemathesis.engine.run import PhaseName
from schemathesis.reporting.html.model import (
    CaseEntry,
    ErrorEntry,
    FailureEntry,
    FailureTick,
    OperationEntry,
    ParentStep,
    PhaseCases,
    PhaseTiming,
    ReportData,
    TickItem,
)
from schemathesis.reporting.html.render import render_index, render_operation
from schemathesis.reporting.html.slug import operation_filename

if TYPE_CHECKING:
    from collections.abc import Iterable

    from schemathesis.cli.commands.run.warnings import WarningData
    from schemathesis.config import OutputConfig
    from schemathesis.core.transport import Response
    from schemathesis.engine.recorder import ScenarioRecorder
    from schemathesis.engine.statistic import GroupedFailures

__all__ = ["HtmlReportWriter"]

_STATUS_PRIORITY = {Status.SKIP: 0, Status.SUCCESS: 1, Status.FAILURE: 2}
_REPORT_MARKER = ".schemathesis-report"
# The report only has Failed/Passed/Skipped index groups. ERROR/INTERRUPTED have no group of
# their own, so treat them as failures to keep the operation visible instead of it vanishing.
_FAILURE_LIKE_STATUSES = frozenset({Status.FAILURE, Status.ERROR, Status.INTERRUPTED})


def _normalize_status(status: Status) -> Status:
    return Status.FAILURE if status in _FAILURE_LIKE_STATUSES else status


@dataclass(slots=True)
class _Meta:
    location: str | None = None
    base_url: str | None = None
    command: str | None = None
    seed: int | None = None


class HtmlReportWriter:
    """Accumulates run data into `ReportData` and writes the HTML report directory on close()."""

    __slots__ = (
        "_config",
        "_error_counts",
        "_errors",
        "_fatal_errors",
        "_meta",
        "_operations",
        "_output_dir",
        "_phases",
        "_running_time",
        "_seen_failure_pairs",
        "_seen_failures",
        "_stop_reason",
        "_ticks",
        "_warnings",
    )

    def __init__(self, output_dir: Path, config: OutputConfig) -> None:
        self._output_dir = Path(output_dir)
        self._config = config
        self._meta = _Meta()
        self._phases: dict[PhaseName, PhaseTiming] = {}
        self._operations: dict[str, OperationEntry] = {}
        self._ticks: list[FailureTick] = []
        self._seen_failure_pairs: set[tuple[str, str]] = set()
        self._seen_failures: dict[str, set[tuple[str, tuple[str, ...]]]] = {}
        self._errors: list[ErrorEntry] = []
        self._fatal_errors: list[ErrorEntry] = []
        self._error_counts: dict[str, int] = {}
        self._warnings: WarningData | None = None
        self._running_time: float | None = None
        self._stop_reason: str | None = None

    def set_meta(
        self,
        *,
        location: str | None = None,
        base_url: str | None = None,
        command: str | None = None,
        seed: int | None = None,
    ) -> None:
        self._meta = _Meta(location=location, base_url=base_url, command=command, seed=seed)

    def record_phase_started(self, phase: PhaseName, at: float) -> None:
        self._phases.setdefault(phase, PhaseTiming()).started_at = at

    def record_phase_finished(self, phase: PhaseName, at: float) -> None:
        self._phases.setdefault(phase, PhaseTiming()).finished_at = at

    def _get_or_create(self, label: str) -> OperationEntry:
        if label not in self._operations:
            self._operations[label] = OperationEntry(
                label=label,
                status=Status.SKIP,
                summary=None,
                definition=None,
                skip_reason=None,
                elapsed=0.0,
                cases_per_phase={},
                failing_cases=[],
                error_count=0,
            )
        return self._operations[label]

    def record_scenario(
        self,
        *,
        label: str,
        elapsed_sec: float,
        status: Status,
        phase: PhaseName,
        recorder: ScenarioRecorder,
        failures: Iterable[GroupedFailures],
        skip_reason: str | None,
        at: float,
        summary: str | None = None,
        definition: str | None = None,
    ) -> None:
        entry = self._get_or_create(label)
        normalized_status = _normalize_status(status)
        if _STATUS_PRIORITY[normalized_status] >= _STATUS_PRIORITY[entry.status]:
            entry.status = normalized_status
        if skip_reason is not None and entry.skip_reason is None:
            entry.skip_reason = skip_reason
        if summary is not None:
            entry.summary = summary
        if definition is not None:
            entry.definition = definition
        entry.elapsed += elapsed_sec

        if label == recorder.label:
            # Stateful cases carry per-operation labels, not the recorder's aggregate label
            # ("Stateful tests"); count every recorded case instead of filtering by operation.
            label_case_ids = set(recorder.cases.keys())
        else:
            label_case_ids = {
                case_id for case_id, node in recorder.cases.items() if node.value.operation.label == label
            }
        failed_case_ids = {
            case_id
            for case_id in label_case_ids
            if any(check.failure_info is not None for check in recorder.checks.get(case_id, []))
        }
        phase_cases = entry.cases_per_phase.setdefault(phase, PhaseCases())
        phase_cases.total += len(label_case_ids)
        phase_cases.failed += len(failed_case_ids)

        new_tick_items: list[TickItem] = []
        seen_failures = self._seen_failures.setdefault(label, set())
        for group in failures:
            case_entry = self._build_case_entry(group, recorder, phase)
            if group.code_sample is not None:
                # Dedup on the request *and* its failing checks: the same request can fail
                # different checks, and each of those is a distinct failure worth keeping.
                identity = (group.code_sample, tuple(sorted(failure.check_name for failure in case_entry.failures)))
                if identity in seen_failures:
                    continue
                seen_failures.add(identity)
            entry.failing_cases.append(case_entry)
            for failure_entry in case_entry.failures:
                pair = (label, failure_entry.check_name)
                if pair not in self._seen_failure_pairs:
                    self._seen_failure_pairs.add(pair)
                    new_tick_items.append(
                        TickItem(check_name=failure_entry.check_name, label=label, case_id=case_entry.case_id)
                    )
        if new_tick_items:
            self._ticks.append(FailureTick(at=at, items=new_tick_items))

    def _build_case_entry(self, group: GroupedFailures, recorder: ScenarioRecorder, phase: PhaseName) -> CaseEntry:
        checks = recorder.checks.get(group.case_id, []) if group.case_id is not None else []
        failure_entries = [
            FailureEntry(
                check_name=next(
                    (
                        check.name
                        for check in checks
                        if check.failure_info is not None and check.failure_info.failure is failure
                    ),
                    type(failure).__name__,
                ),
                title=failure.title,
                message=failure.message,
            )
            for failure in group.failures
        ]
        response = group.response
        body: str | None = None
        content_type: str | None = None
        status_code: int | None = None
        status_message = ""
        elapsed_ms: int | None = None
        if response is not None:
            status_code = response.status_code
            status_message = response.message or ""
            elapsed_ms = int(response.elapsed * 1000)
            content_type_values = response.headers.get("content-type")
            content_type = content_type_values[0] if content_type_values else None
            body = _decode_body(response, self._config)
        return CaseEntry(
            case_id=group.case_id or "run",
            phase=phase,
            failures=failure_entries,
            curl=group.code_sample,
            response_status=status_code,
            response_message=status_message,
            response_body=body,
            response_content_type=content_type,
            elapsed_ms=elapsed_ms,
            parent_steps=_parent_steps(group.case_id, recorder, self._config),
        )

    def record_error(self, *, label: str, title: str, message: str, traceback: str | None, phase: str | None) -> None:
        self._errors.append(ErrorEntry(label=label, title=title, message=message, traceback=traceback, phase=phase))
        self._error_counts[label] = self._error_counts.get(label, 0) + 1

    def record_fatal_error(self, *, title: str, message: str) -> None:
        # Not tied to any operation, so `label` stays empty; the run-level errors section renders it regardless.
        self._fatal_errors.append(ErrorEntry(label="", title=title, message=message, traceback=None, phase=None))

    def set_run_summary(self, *, running_time: float, stop_reason: str | None) -> None:
        self._running_time = running_time
        self._stop_reason = stop_reason

    def _close_open_phases(self) -> None:
        # An interrupted run (e.g. Ctrl-C mid-fuzzing) would otherwise vanish from the timeline.
        open_phases = [
            timing for timing in self._phases.values() if timing.started_at is not None and timing.finished_at is None
        ]
        if not open_phases:
            return
        known_timestamps = [
            value
            for timing in self._phases.values()
            for value in (timing.started_at, timing.finished_at)
            if value is not None
        ]
        known_timestamps.extend(tick.at for tick in self._ticks)
        run_end = max(known_timestamps)
        for timing in open_phases:
            started_at = timing.started_at
            assert started_at is not None
            timing.finished_at = max(run_end, started_at)

    def set_warnings(self, warnings: WarningData) -> None:
        self._warnings = warnings

    def close(self, *, exit_code: int = 0) -> None:
        for label, count in self._error_counts.items():
            if label in self._operations:
                self._operations[label].error_count = count
        self._close_open_phases()
        data = ReportData(
            generated_at=datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
            location=self._meta.location,
            base_url=self._meta.base_url,
            command=self._meta.command,
            seed=self._meta.seed,
            phases=self._phases,
            operations=self._operations,
            ticks=self._ticks if self._phases else [],
            warnings=self._warnings,
            errors=self._errors,
            fatal_errors=self._fatal_errors,
            running_time=self._running_time,
            stop_reason=self._stop_reason,
            exit_code=exit_code,
        )
        operations_dir = self._output_dir / "operations"
        assets_dir = self._output_dir / "assets"
        is_prior_report = (self._output_dir / _REPORT_MARKER).is_file()
        operations_dir.mkdir(parents=True, exist_ok=True)
        assets_dir.mkdir(parents=True, exist_ok=True)
        # Mark the directory as ours right away so a run interrupted mid-write is still recognized
        # as a prior report next time, and its stale pages get cleaned rather than left as orphans.
        (self._output_dir / _REPORT_MARKER).touch()
        if is_prior_report:
            for stale in operations_dir.glob("*.html"):
                stale.unlink()
        assets = files("schemathesis.reporting.html") / "assets"
        for name in ("report.css", "app.js"):
            (assets_dir / name).write_text((assets / name).read_text(encoding="utf-8"), encoding="utf-8")
        seen: set[str] = set()
        filenames = {
            entry.label: operation_filename(entry.label, seen)
            for entry in (*data.failed_operations, *data.passed_operations, *data.skipped_operations)
        }
        (self._output_dir / "index.html").write_text(render_index(data, filenames), encoding="utf-8")
        for entry in (*data.failed_operations, *data.passed_operations):
            page = render_operation(data, entry)
            (operations_dir / f"{filenames[entry.label]}.html").write_text(page, encoding="utf-8")


def _decode_body(response: Response, config: OutputConfig) -> str | None:
    if not response.content:
        return None
    try:
        text = response.content.decode(response.encoding or "utf-8")
    except (UnicodeError, LookupError, ValueError):
        # Undecodable bytes or a server-supplied bogus/unusable charset (a NUL-laced codec name
        # raises a bare ValueError, not LookupError); show a placeholder instead of crashing.
        return None
    return prepare_response_payload(text, config=config)


def _parent_steps(case_id: str | None, recorder: ScenarioRecorder, config: OutputConfig) -> list[ParentStep]:
    if case_id is None or case_id not in recorder.cases:
        return []
    chain = []
    seen = {case_id}
    current = recorder.cases[case_id]
    while current.parent_id is not None and current.parent_id not in seen:
        seen.add(current.parent_id)
        parent = recorder.cases.get(current.parent_id)
        if parent is None:
            break
        response = recorder.find_response(case_id=current.parent_id)
        case = parent.value
        detail_parts = [f"{case.method} {case.path}"]
        if response is not None:
            body = _decode_body(response, config)
            detail_parts.append(f"\n{response.status_code} {response.message or ''}")
            if body:
                detail_parts.append(body)
        chain.append(
            ParentStep(
                method=case.method,
                path=case.path,
                status_code=response.status_code if response is not None else None,
                status_message=(response.message or "") if response is not None else "",
                elapsed_ms=int(response.elapsed * 1000) if response is not None else None,
                detail="\n".join(detail_parts),
            )
        )
        current = parent
    chain.reverse()
    return chain
