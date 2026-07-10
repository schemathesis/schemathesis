from __future__ import annotations

from typing import TYPE_CHECKING

from schemathesis.engine import Status
from schemathesis.reporting.html.render.components import (
    CHECK_DOCS_URL,
    code_block,
    errors_section,
    esc,
    filter_warnings_for_label,
    humanize_duration,
    label_html,
    method_span,
    page,
    report_top,
    warnings_section,
)

if TYPE_CHECKING:
    from schemathesis.reporting.html.model import CaseEntry, FailureEntry, OperationEntry, ReportData


def render_operation(data: ReportData, entry: OperationEntry) -> str:
    sections = [
        report_top(generated_at=data.generated_at, back_href="../index.html"),
        _hero(entry),
        _body(entry),
        _warnings(data, entry),
        errors_section([error for error in data.errors if error.label == entry.label]),
        _schema(entry),
    ]
    return page(
        title=f"Schemathesis Report: {entry.label}",
        body="\n".join(part for part in sections if part),
        asset_prefix="../",
    )


def _hero(entry: OperationEntry) -> str:
    summary = f'<p class="op-summary">{esc(entry.summary)}</p>' if entry.summary else ""
    phase_cells = []
    for phase, cases in entry.cases_per_phase.items():
        passed = cases.total - cases.failed
        state = "fail" if cases.failed else "ok"
        phase_cells.append(
            f'<span class="ps-cell {state}"><span class="ps-name">{esc(phase.value)}</span>'
            f'<span class="ps-stat">{passed}/{cases.total} cases</span></span>'
        )
    phase_strip = ""
    if phase_cells:
        cells = "".join(phase_cells)
        phase_strip = (
            '<span class="om phase-strip" aria-label="phase summary"><span class="om-k">Phases</span>'
            f'<span class="ps-cells">{cells}</span></span>'
        )
    failed = entry.failed_checks_count
    failed_html = (
        f'<span class="om"><span class="om-k">Failed checks</span><span class="om-v fail">{failed}</span></span>'
        if failed
        else ""
    )
    return (
        '<section class="op-hero">'
        f'<div class="op-hero-row">{label_html(entry.label)}'
        '<a href="../index.html" class="back-pill op-hero-back" title="back to report index">'
        '<svg width="12" height="12" aria-hidden="true"><use href="#icon-arrow-left"/></svg>'
        "<span>All operations</span></a></div>"
        f"{summary}"
        '<div class="op-meta">'
        f'<span class="om"><span class="om-k">Cases</span><span class="om-v">{entry.total_cases}</span></span>'
        f"{failed_html}"
        f'<span class="om"><span class="om-k">Elapsed</span><span class="om-v">{esc(humanize_duration(entry.elapsed))}</span></span>'
        f"{phase_strip}</div></section>"
    )


def _body(entry: OperationEntry) -> str:
    if entry.failing_cases:
        return "\n".join((_failure_band(entry), _case_index(entry), _cases(entry)))
    if entry.status == Status.SUCCESS:
        phase_count = len(entry.cases_per_phase)
        return (
            '<div class="pass-banner">'
            '<span class="pb-icon"><svg width="16" height="16"><use href="#icon-check"/></svg></span>'
            f'<div><div class="pb-title">All {entry.total_cases} cases passed across {phase_count} phase{"s" if phase_count != 1 else ""}.</div>'
            '<div class="pb-sub">No check failures detected.</div></div></div>'
        )
    # Errored/interrupted without a failed check: neutral note, never a celebratory banner.
    meta = ""
    if entry.error_count:
        meta = (
            f'<span class="op-note-meta">{entry.error_count} non-fatal '
            f"error{'s' if entry.error_count != 1 else ''} - details below</span>"
        )
    return (
        '<div class="op-note standalone err"><span class="op-note-tag">Error</span>'
        '<span class="op-note-title">No check failures recorded</span>'
        f"{meta}</div>"
    )


def _failure_band(entry: OperationEntry) -> str:
    cases = len(entry.failing_cases)
    checks = entry.failed_checks_count
    chips = "".join(
        f'<a href="#case-{esc(_first_case_with(entry, name))}" class="ofb-chip">'
        f'<span class="ofb-chip-n">{count}</span><span class="ofb-chip-name">{esc(name)}</span></a>'
        for name, count in entry.check_counts
    )
    return (
        '<section class="op-failure-band"><div class="ofb-headline-v2">'
        f'<span class="ofb-cases-label">Failing cases <span class="ofb-cases-n-inline">{cases}</span></span>'
        f'<span class="ofb-cases-label">Failed checks <span class="ofb-cases-n-inline">{checks}</span></span>'
        f'</div><div class="ofb-chips">{chips}</div></section>'
    )


def _first_case_with(entry: OperationEntry, check_name: str) -> str:
    for case in entry.failing_cases:
        if any(failure.check_name == check_name for failure in case.failures):
            return case.case_id
    return entry.failing_cases[0].case_id


def _case_index(entry: OperationEntry) -> str:
    links = "".join(
        f'<a class="case-index-link" href="#case-{esc(case.case_id)}">{esc(case.case_id)}'
        f'<span class="n">{len(case.failures)}</span></a>'
        for case in entry.failing_cases
    )
    return f'<nav class="case-index" aria-label="failing cases on this page"><span class="case-index-label">Cases</span>{links}</nav>'


def _cases(entry: OperationEntry) -> str:
    return f'<section class="section cases">{"".join(_case_card(case) for case in entry.failing_cases)}</section>'


def _case_card(case: CaseEntry) -> str:
    failures = "".join(_failure(failure) for failure in case.failures)
    request_block = ""
    if case.curl:
        request_block = code_block(title="Request", body=case.curl, copy_id=f"curl-{case.case_id}")
    response_block = ""
    if case.response_status is not None:
        status_class = f"status-{case.response_status // 100}xx"
        elapsed = f'<span class="elapsed">{case.elapsed_ms}ms</span>' if case.elapsed_ms is not None else ""
        status_line = (
            f'<span class="{status_class}">{case.response_status} {esc(case.response_message)}</span>{elapsed}'
        )
        body_lines = []
        if case.response_content_type:
            body_lines.append(f"Content-Type: {case.response_content_type}")
            body_lines.append("")
        body_lines.append(case.response_body if case.response_body is not None else "<binary or empty body>")
        response_block = code_block(title="Response", body="\n".join(body_lines), status_line=status_line)
    return (
        f'<article class="case-card" id="case-{esc(case.case_id)}">'
        '<header class="case-card-head">'
        f'<span class="case-id"><span class="pre">ID</span>{esc(case.case_id)}</span>'
        '<span class="case-rule"></span>'
        f'<span class="phase-chip">{esc(case.phase.value.lower())}</span></header>'
        f'<div class="case-failures">{failures}</div>'
        f"{_parent_trace(case)}"
        f'<div class="rr-block">{request_block}{response_block}</div></article>'
    )


def _failure(failure: FailureEntry) -> str:
    message = failure.message or failure.title
    details = _negative_failure_details(message)
    css = "case-failure cf-detailed" if details else "case-failure"
    body = f'<p class="cf-msg">{esc(details[0] if details else message)}</p>'
    if details:
        rows = "".join(
            f'<div class="cf-detail-row"><span class="cf-detail-k">{esc(key)}</span>'
            f'<span class="cf-detail-v">{esc(value)}</span></div>'
            for key, value in details[1]
        )
        body += f'<div class="cf-detail">{rows}</div>'
    return (
        f'<div class="{css}">'
        f'<div class="cf-head"><span class="cf-name">{esc(failure.check_name)}</span>'
        f'<a href="{CHECK_DOCS_URL}{esc(failure.check_name)}" target="_blank" rel="noopener" class="cf-docs">docs</a></div>'
        f"{body}</div>"
    )


def _negative_failure_details(message: str) -> tuple[str, list[tuple[str, str]]] | None:
    lines = message.splitlines()
    if len(lines) < 3 or lines[0] != "Invalid data should have been rejected":
        return None
    rows = []
    if lines[1].startswith("Expected: "):
        rows.append(("Expected", lines[1].removeprefix("Expected: ")))
    if lines[2].startswith("Invalid component:"):
        rows.append(("Invalid component", "\n".join(lines[2:]).removeprefix("Invalid component:").strip()))
    return (lines[0], rows) if rows else None


def _parent_trace(case: CaseEntry) -> str:
    if not case.parent_steps:
        return ""
    steps = []
    for index, step in enumerate(case.parent_steps, 1):
        status = f"{step.status_code} {esc(step.status_message)}" if step.status_code is not None else "no response"
        elapsed = (
            f'<span class="stat-sep" aria-hidden="true"></span><span class="elapsed">{step.elapsed_ms}ms</span>'
            if step.elapsed_ms is not None
            else ""
        )
        steps.append(
            '<details class="parent-step-expand"><summary>'
            f'<span class="ix">{index}</span>{method_span(step.method)}'
            f'<span class="summary">{esc(step.path)}</span>'
            f'<span class="stat">{status}{elapsed}</span></summary>'
            f'<div class="parent-step-body"><pre class="parent-step-code">{esc(step.detail)}</pre></div></details>'
        )
    terminal_status = f"{case.response_status} {esc(case.response_message)}" if case.response_status is not None else ""
    steps.append(
        '<div class="parent-step terminal">'
        f'<span class="ix">{len(case.parent_steps) + 1}</span>'
        '<span class="summary" style="color: var(--color-brand-danger)">failed (this case)</span>'
        f'<span class="stat">{terminal_status}</span></div>'
    )
    return f'<div class="parent-block"><div class="parent-trace">{"".join(steps)}</div></div>'


def _warnings(data: ReportData, entry: OperationEntry) -> str:
    if data.warnings is None:
        return ""
    return warnings_section(filter_warnings_for_label(data.warnings, entry.label))


def _schema(entry: OperationEntry) -> str:
    if entry.definition is None:
        return ""
    # Collapsed by default: reference material, not signal worth a permanently open block.
    return (
        '<details class="schema-details">'
        '<summary class="schema-details-summary"><span class="sd-label">Schema</span>'
        '<span class="sd-toggle" aria-hidden="true">v</span></summary>'
        '<div class="schema-snippet">'
        f'<pre class="schema-snippet-body">{esc(entry.definition)}</pre></div></details>'
    )
