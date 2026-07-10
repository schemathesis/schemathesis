from __future__ import annotations

from typing import TYPE_CHECKING

from schemathesis.engine import Status
from schemathesis.reporting.html.render.components import (
    errors_section,
    esc,
    humanize_duration,
    label_html,
    page,
    report_top,
    warnings_section,
)

if TYPE_CHECKING:
    from schemathesis.reporting.html.model import OperationEntry, ReportData

_PHASE_CSS = {
    "Examples": "examples",
    "Coverage": "coverage",
    "Fuzzing": "fuzzing",
    "Stateful": "stateful",
}

_STOP_REASON_LABELS = {
    "interrupted": "Run interrupted before completion",
    "failure_limit": "Stopped early: failure limit reached",
    "max_time": "Stopped early: time limit reached",
}

_MIN_PHASE_LABEL_WIDTH = 12.0


def render_index(data: ReportData, filenames: dict[str, str]) -> str:
    sections = [
        report_top(generated_at=data.generated_at),
        _hero(data),
        _target_block(data),
        _timeline(data, filenames),
        _filter_bar(),
        _operations_table(data, filenames),
        _warnings(data),
        _orphan_errors(data),
    ]
    return page(title="Schemathesis Report", body="\n".join(part for part in sections if part), asset_prefix="")


def _top_failures_cell(data: ReportData, no_failing_checks_cell: str) -> str:
    if not data.top_failures:
        return no_failing_checks_cell
    top_rows = "".join(
        f'<li><span class="tf-n">{count}</span><span class="tf-name">{esc(name)}</span></li>'
        for name, count in data.top_failures[:3]
    )
    return (
        '<div class="hs-cell hs-top-failures"><span class="m-label">Top failures</span>'
        f'<ul class="tf-list">{top_rows}</ul></div>'
    )


def _hero(data: ReportData) -> str:
    failed = len(data.failed_operations)
    passed = len(data.passed_operations)
    skipped = len(data.skipped_operations)
    tested = failed + passed
    no_failing_checks_cell = (
        '<div class="hs-cell hs-metric"><span class="m-label">Checks</span>'
        '<span class="m-value">no failing checks</span></div>'
    )
    if data.fatal_errors:
        # A fatal error outranks any Failed/Passed verdict, but failures recorded before the
        # crash still deserve the "Top failures" cell rather than a misleading neutral one.
        verdict = '<div class="hero-status-label">Errored</div>'
        bar = ""
        subtitle = f'<div class="hs-sub">{esc(data.fatal_errors[0].title)}</div>'
        second_cell = _top_failures_cell(data, no_failing_checks_cell)
    elif failed:
        verdict = '<div class="hero-status-label">Failed</div>'
        bar = (
            f'<div class="fail-mix-bar" role="img" aria-label="{failed} of {tested} operations failed">'
            f'<span class="seg-failed" style="flex: {failed}"></span>'
            f'<span class="seg-passed" style="flex: {passed}"></span></div>'
        )
        subtitle = f'<div class="hs-sub">{failed} of {tested} operations failed</div>'
        second_cell = _top_failures_cell(data, no_failing_checks_cell)
    elif data.exit_code:
        verdict = '<div class="hero-status-label">Failed</div>'
        bar = ""
        subtitle = '<div class="hs-sub">Run failed</div>'
        second_cell = no_failing_checks_cell
    elif tested:
        verdict = '<div class="hero-status-label">Passed</div>'
        bar = (
            f'<div class="fail-mix-bar" role="img" aria-label="all {tested} operations passed">'
            '<span class="seg-passed" style="flex: 1"></span></div>'
        )
        subtitle = f'<div class="hs-sub">{tested} operations passed</div>'
        second_cell = (
            '<div class="hs-cell hs-metric"><span class="m-label">Checks</span>'
            '<span class="m-value">all passing</span></div>'
        )
    else:
        # No operations tested and no fatal error, e.g. every operation got filtered out.
        verdict = '<div class="hero-status-label">No tests ran</div>'
        bar = ""
        subtitle = ""
        second_cell = no_failing_checks_cell
    duration = humanize_duration(data.running_time) if data.running_time is not None else "-"
    average = ""
    if data.running_time is not None and tested:
        average = f'<span class="m-sub">{humanize_duration(data.running_time / tested)} avg per operation</span>'
    if skipped:
        subtitle += f'<div class="hs-sub">{skipped} skipped</div>'
    return (
        '<section class="hero-strip">'
        f'<div class="hs-cell hs-verdict">{verdict}{bar}{subtitle}{_stop_reason_note(data.stop_reason)}</div>'
        f"{second_cell}"
        f'<div class="hs-cell hs-metric"><span class="m-label">Cases run</span><span class="m-value">{data.total_cases:,}</span></div>'
        f'<div class="hs-cell hs-metric"><span class="m-label">Duration</span><span class="m-value">{esc(duration)}</span>{average}</div>'
        "</section>"
    )


def _stop_reason_note(stop_reason: str | None) -> str:
    label = _STOP_REASON_LABELS.get(stop_reason or "")
    if label is None:
        return ""
    return f'<div class="hs-stop-note">{esc(label)}</div>'


def _target_block(data: ReportData) -> str:
    rows = []
    if data.base_url:
        rows.append(f'<span class="tk">Base URL</span><span class="tv">{esc(data.base_url)}</span>')
    if data.location:
        rows.append(f'<span class="tk">Spec</span><span class="tv">{esc(data.location)}</span>')
    if data.command:
        rows.append(
            f'<span class="tk">Command</span><span class="tv"><code id="run-cmd-text">{esc(data.command)}</code>'
            '<button type="button" class="seed-copy" data-copy-target="#run-cmd-text" title="copy command">'
            '<svg width="12" height="12"><use href="#icon-copy"/></svg></button></span>'
        )
    if data.seed is not None:
        rows.append(
            f'<span class="tk">Seed</span><span class="tv"><code id="run-seed">{data.seed}</code>'
            '<button type="button" class="seed-copy" data-copy-target="#run-seed" title="copy seed">'
            '<svg width="12" height="12"><use href="#icon-copy"/></svg></button></span>'
        )
    if not rows:
        return ""
    return f'<section class="target-block">{"".join(rows)}</section>'


def _timeline(data: ReportData, filenames: dict[str, str]) -> str:
    # `executed_phases` guarantees both timestamps are set; re-derive that here so
    # the timestamps are known non-optional at this call site.
    executed = [
        (phase, timing.started_at, timing.finished_at)
        for phase, timing in data.executed_phases
        if timing.started_at is not None and timing.finished_at is not None
    ]
    if not executed:
        return ""
    start = min(started for _, started, _ in executed)
    end = max(finished for _, _, finished in executed)
    total = max(end - start, 1e-6)
    bands = []
    for phase, started, finished in executed:
        width = round((finished - started) / total * 100, 1)
        css = _PHASE_CSS.get(phase.value, "fuzzing")
        label = f'<span class="rt-phase-label">{esc(phase.value)}</span>' if width >= _MIN_PHASE_LABEL_WIDTH else ""
        bands.append(f'<div class="rt-phase {css}" style="width: {width}%">{label}</div>')
    ticks = []
    for tick in data.ticks:
        left = round(min(max((tick.at - start) / total, 0.0), 1.0) * 100, 1)
        height = {1: 14, 2: 24}.get(len(tick.items), 32)
        offset = humanize_duration(tick.at - start)
        items = "".join(
            f'<a class="rt-pop-item" href="operations/{esc(filenames[item.label])}.html#case-{esc(item.case_id)}">'
            f'<span class="check">{esc(item.check_name)}</span>'
            f'<span class="rt-pop-op">{label_html(item.label)}</span></a>'
            for item in tick.items
            if item.label in filenames
        )
        count = len(tick.items)
        ticks.append(
            f'<div class="rt-tick-group{" cluster" if count > 1 else ""}" tabindex="0" style="left: {left}%">'
            f'<span class="rt-tick" style="height: {height}px"></span>'
            f'<div class="rt-pop"><div class="rt-pop-head"><span class="rt-pop-time">{esc(offset)}</span>'
            f'<span class="rt-pop-summary">{count} new failure{"s" if count != 1 else ""}</span></div>'
            f'<div class="rt-pop-list">{items}</div></div></div>'
        )
    axis = "".join(f"<span>{esc(humanize_duration(total * fraction))}</span>" for fraction in (0, 0.25, 0.5, 0.75, 1))
    return (
        '<section class="run-timeline phased" aria-label="Run timeline">'
        '<div class="rt-head"><span class="rt-eyebrow">Run timeline</span></div>'
        f'<div class="rt-canvas"><div class="rt-clip"><div class="rt-phases">{"".join(bands)}</div></div>'
        f'<div class="rt-ticks">{"".join(ticks)}</div></div>'
        f'<div class="rt-axis">{axis}</div>'
        "</section>"
    )


def _filter_bar() -> str:
    return (
        '<div class="filter-bar"><div class="filter-search">'
        '<svg width="14" height="14"><use href="#icon-search"/></svg>'
        '<input type="search" placeholder="search method, path, or check" data-filter="search" aria-label="filter operations">'
        '</div><span class="filter-pills" id="active-filters"></span><span class="filter-result"></span></div>'
    )


def _search_text(entry: OperationEntry) -> str:
    checks = " ".join(name for name, _ in entry.check_counts)
    return f"{entry.method.lower()} {entry.path.lower()} {checks}".strip()


def _gutter_note(entry: OperationEntry) -> str:
    if entry.error_count:
        return f'<span class="gutter-note err" title="{entry.error_count} non-fatal error{"s" if entry.error_count != 1 else ""}"></span>'
    return ""


def _failed_checks_cell(entry: OperationEntry) -> str:
    counts = entry.check_counts
    if not counts:
        return "<td></td>"
    first_name = counts[0][0]
    more = ""
    if len(counts) > 1:
        rows = "".join(f'<li><span class="n">{count}</span><span>{esc(name)}</span></li>' for name, count in counts)
        more = f'<span class="more" tabindex="0">+{len(counts) - 1} more<span class="more-pop"><ul>{rows}</ul></span></span>'
    return f'<td class="checks-cell"><span class="name fail">{esc(first_name)}</span>{more}</td>'


def _operation_row(entry: OperationEntry, filenames: dict[str, str]) -> str:
    link = f'<a class="row-link" href="operations/{esc(filenames[entry.label])}.html">{label_html(entry.label)}</a>'
    # Keyed on status, not failing checks: errored operations may fail without a single failed check.
    status = "failed" if entry.status == Status.FAILURE else "passed"
    return (
        f'<tr class="op-row row-{status}" data-status="{status}" '
        f'data-search-text="{esc(_search_text(entry))}">'
        f'<td>{_gutter_note(entry)}<div class="row-link-wrap">{link}</div></td>'
        f"{_failed_checks_cell(entry)}"
        f'<td class="numeric">{entry.total_cases}</td></tr>'
    )


def _skipped_row(entry: OperationEntry) -> str:
    reason = esc(entry.skip_reason or "skipped")
    return (
        f'<tr class="op-row row-untested" data-status="skipped" data-search-text="{esc(_search_text(entry))}">'
        f'<td><div class="row-link-wrap"><span class="row-link" style="opacity: 0.6;">'
        f"{label_html(entry.label)}</span></div></td>"
        f'<td colspan="2"><span class="skip-reason">{reason}</span></td></tr>'
    )


def _group(title: str, css: str, rows: list[str]) -> str:
    if not rows:
        return ""
    header = (
        '<tr class="group-header-row"><td colspan="3" style="padding: 0; border: none;">'
        f'<div class="group-header {css}"><b>{esc(title)}</b></div></td></tr>'
    )
    return f'<tbody class="group">{header}{"".join(rows)}</tbody>'


def _operations_table(data: ReportData, filenames: dict[str, str]) -> str:
    groups = [
        _group("Failed", "failed", [_operation_row(entry, filenames) for entry in data.failed_operations]),
        _group("Passed", "passed", [_operation_row(entry, filenames) for entry in data.passed_operations]),
        _group("Skipped", "untested", [_skipped_row(entry) for entry in data.skipped_operations]),
    ]
    return (
        '<div class="ops-wrap"><table class="ops-table" aria-label="operations">'
        '<thead><tr><th style="width: 30%;">Operation</th><th style="width: 58%;">Failed checks</th>'
        '<th class="numeric" style="width: 12%;">Cases</th></tr></thead>'
        f"{''.join(groups)}</table></div>"
    )


def _warnings(data: ReportData) -> str:
    if data.warnings is None:
        return ""
    return warnings_section(data.warnings)


def _orphan_errors(data: ReportData) -> str:
    # Only failed/passed operations get their own page; an error on any other operation (skipped,
    # or never recorded) has nowhere else to appear, so surface it in the run-level errors section.
    paged = {entry.label for entry in (*data.failed_operations, *data.passed_operations)}
    orphans = [error for error in data.errors if error.label not in paged]
    return errors_section([*data.fatal_errors, *orphans])
