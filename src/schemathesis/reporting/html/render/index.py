from __future__ import annotations

from typing import TYPE_CHECKING

from schemathesis.engine import StopReason
from schemathesis.reporting.html.model import Verdict
from schemathesis.reporting.html.render.components import (
    card,
    errors_section,
    esc,
    humanize_duration,
    operation_items,
    page,
    plural,
    report_top,
    section_eyebrow,
    warnings_section,
)

if TYPE_CHECKING:
    from schemathesis.reporting.html.model import ReportData


def render_index(data: ReportData) -> str:
    sections = [
        report_top(generated_at=data.meta.generated_at),
        _hero(data),
        _failures(data),
        _target_block(data),
        _errors(data),
        _warnings(data),
    ]
    return page(title="Schemathesis Report", body="\n".join(part for part in sections if part), asset_prefix="")


def _metric(label: str, value: str, subtitle: str = "") -> str:
    subtitle_html = f'<span class="m-sub">{subtitle}</span>' if subtitle else ""
    return f'<div class="hs-cell hs-metric"><span class="m-label">{esc(label)}</span><span class="m-value">{esc(value)}</span>{subtitle_html}</div>'


def _top_failures_cell(data: ReportData) -> str:
    if not data.top_failures:
        return _metric("Checks", "no failing checks")
    rows = "".join(
        f'<li><span class="tf-n">{group.count}</span><span class="tf-name">{esc(group.title)}</span></li>'
        for group in data.top_failures[:3]
    )
    return f'<div class="hs-cell hs-top-failures"><span class="m-label">Top failures</span><ul class="tf-list">{rows}</ul></div>'


def _hero(data: ReportData) -> str:
    verdict = data.verdict
    tested = data.tested_operations
    failed = min(len(data.failed_operations), tested)
    passed = tested - failed
    heading = f'<h1 class="hero-status-label">{esc(verdict.value)}</h1>'
    bar = ""
    subtitle = ""
    if data.fatal_error is not None:
        subtitle = f'<div class="hs-sub">{esc(data.fatal_error.title)}</div>'
    elif failed:
        passed_segment = f'<span class="seg-passed" style="flex: {passed}"></span>' if passed else ""
        bar = (
            f'<div class="fail-mix-bar" role="img" aria-label="{failed} of {plural(tested, "operation")} failed">'
            f'<span class="seg-failed" style="flex: {failed}"></span>'
            f"{passed_segment}</div>"
        )
        subtitle = f'<div class="hs-sub">{failed} of {plural(tested, "operation")} failed</div>'
    elif data.summary.failures:
        subtitle = '<div class="hs-sub">Failures not attributed to a tested operation</div>'
    elif verdict is Verdict.FAILED:
        subtitle = '<div class="hs-sub">Run failed</div>'
    elif verdict is Verdict.PASSED:
        bar = (
            f'<div class="fail-mix-bar" role="img" aria-label="all {plural(tested, "operation")} passed">'
            '<span class="seg-passed" style="flex: 1"></span></div>'
        )
        subtitle = f'<div class="hs-sub">{plural(tested, "operation")} passed</div>'
    second_cell = _metric("Checks", "all passing") if verdict is Verdict.PASSED else _top_failures_cell(data)
    phases = data.executed_phases
    cases_subtitle = f"across {plural(phases, 'phase')}" if phases else ""
    operations_parts = []
    if data.skipped_operations:
        operations_parts.append(f"{data.skipped_operations} skipped")
    if data.errored_operations:
        operations_parts.append(f"{data.errored_operations} errored")
    duration = humanize_duration(data.running_time) if data.running_time is not None else "-"
    return (
        f'<section class="hero-strip verdict-{verdict.css}">'
        f'<div class="hs-cell hs-verdict">{heading}{bar}{subtitle}{_stop_reason_note(data)}</div>'
        f"{second_cell}"
        f"{_metric('Cases run', f'{data.summary.test_cases.generated:,}', cases_subtitle)}"
        f"{_metric('Operations', str(tested), ' · '.join(operations_parts))}"
        f"{_metric('Duration', duration)}"
        "</section>"
    )


def _stop_reason_note(data: ReportData) -> str:
    # "Interrupted" is already the verdict, and also the handler's default when the engine never finished.
    if data.stop_reason is StopReason.INTERRUPTED:
        return ""
    explanation = data.stop_reason.skip_explanation
    return f'<div class="hs-stop-note">{esc(explanation)}</div>' if explanation else ""


def _target_block(data: ReportData) -> str:
    rows = []
    if data.meta.base_url:
        rows.append(f'<span class="tk">Base URL</span><span class="tv">{esc(data.meta.base_url)}</span>')
    if data.meta.location:
        rows.append(f'<span class="tk">Spec</span><span class="tv">{esc(data.meta.location)}</span>')
    rows.append(f'<span class="tk">Command</span><span class="tv"><code>{esc(data.meta.command)}</code></span>')
    if data.meta.seed is not None:
        rows.append(f'<span class="tk">Seed</span><span class="tv"><code>{data.meta.seed}</code></span>')
    rows.append(f'<span class="tk">Exit code</span><span class="tv"><code>{data.exit_code}</code></span>')
    return f'<section class="target-block">{"".join(rows)}</section>'


def _failures(data: ReportData) -> str:
    if not data.summary.failures:
        return ""
    cards = "".join(
        card(
            title=group.title,
            count=plural(group.count, "occurrence"),
            description=f"{plural(len(group.operations), 'operation')} affected"
            if group.operations
            else "Run-level check",
            items=operation_items(group.operations) if group.operations else "",
        )
        for group in data.top_failures
    )
    return f'<section class="section failures-section">{section_eyebrow("Failures", len(data.summary.failures))}{cards}</section>'


def _errors(data: ReportData) -> str:
    return errors_section(data.errors, data.fatal_error)


def _warnings(data: ReportData) -> str:
    return warnings_section(data.summary.warnings)
