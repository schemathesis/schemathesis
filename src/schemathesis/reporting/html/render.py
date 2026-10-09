from __future__ import annotations

import re
from html import escape
from importlib.resources import files
from typing import TYPE_CHECKING

from schemathesis.core.output import escape_surrogates
from schemathesis.core.version import SCHEMATHESIS_VERSION
from schemathesis.engine import StopReason
from schemathesis.reporting.html.model import OperationStatus, Verdict

if TYPE_CHECKING:
    from schemathesis.reporting.html.model import OperationRow, ReportData

_LOGO = (files("schemathesis.reporting.html") / "assets" / "logo.svg").read_text(encoding="utf-8").strip()


def esc(value: object) -> str:
    return escape(escape_surrogates(str(value)), quote=True)


def plural(count: int, word: str) -> str:
    return f"{count} {word}" if count == 1 else f"{count} {word}s"


def humanize_duration(seconds: float) -> str:
    # Round to the displayed precision first so e.g. 59.96 (-> "60.0s") crosses into "1m 0s".
    rounded = round(seconds, 1)
    if rounded >= 3600:
        hours, rest = divmod(int(round(rounded)), 3600)
        return f"{hours}h {rest // 60}m"
    if rounded >= 60:
        minutes, rest = divmod(int(round(rounded)), 60)
        return f"{minutes}m {rest}s"
    return f"{rounded:.1f}s"


# Copies `data-copy` values; `navigator.clipboard` is unavailable on `file://`, hence the fallback.
_COPY_SCRIPT = (
    "<script>document.querySelectorAll('[data-copy]').forEach(function(b){b.addEventListener('click',function(){"
    "var t=b.getAttribute('data-copy');var done=function(){b.textContent='Copied';"
    "setTimeout(function(){b.textContent='Copy'},1200)};"
    "if(navigator.clipboard&&window.isSecureContext){navigator.clipboard.writeText(t).then(done)}"
    "else{var a=document.createElement('textarea');a.value=t;document.body.appendChild(a);a.select();"
    "try{document.execCommand('copy');done()}catch(e){}a.remove()}})})</script>"
)

# Command arguments longer than this may break anywhere; shorter ones never break inside.
_LONG_ARGUMENT = 40


def page(*, title: str, body: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{esc(title)}</title>
  <link rel="stylesheet" href="assets/report.css">
</head>
<body>
  <main class="container">
{body}
  </main>
{_COPY_SCRIPT}
</body>
</html>
"""


def report_top(*, generated_at: str) -> str:
    return (
        '<header class="report-top">'
        f'<div class="brand"><span class="logo">{_LOGO}</span>'
        f'<span class="version-chip">v{esc(SCHEMATHESIS_VERSION)}</span></div>'
        '<div class="top-meta"><span class="tm-eyebrow">Generated at</span>'
        f"<span><b>{esc(generated_at)}</b></span></div>"
        "</header>"
    )


def render_index(data: ReportData) -> str:
    body = "\n".join(
        [report_top(generated_at=data.meta.generated_at), _hero(data), _target_block(data), _operations_table(data)]
    )
    return page(title="Schemathesis Report", body=body)


def _duration(data: ReportData) -> str:
    return humanize_duration(data.running_time) if data.running_time is not None else "-"


def _tip(text: str | None) -> str:
    if text is None:
        return ""
    body = re.sub(r"`([^`]+)`", r"<code>\1</code>", esc(text))
    return f'<p class="ex-tip"><b>Tip:</b> {body}</p>'


def _hero(data: ReportData) -> str:
    # Nothing was tested, so metric cells would only show zeros; explain what happened instead.
    if data.verdict in (Verdict.ERRORED, Verdict.EMPTY, Verdict.INTERRUPTED) and not data.tested_operations:
        return _message(data)
    return _result(data)


def _message(data: ReportData) -> str:
    verdict = data.verdict
    detail = ""
    tip = ""
    if verdict is Verdict.INTERRUPTED:
        subtitle = f"Stopped by user after {_duration(data)}"
        title = "Stopped before any test case completed"
        if data.last_phase is not None:
            detail = f"Ctrl-C during the {data.last_phase.display} phase."
        tip = _tip("re-run the command below to get results.")
    elif verdict is Verdict.EMPTY:
        subtitle = f"Finished in {_duration(data)}"
        if data.nothing_tested is not None:
            title = data.nothing_tested.title
            if data.nothing_tested.filters:
                label = "Filter" if len(data.nothing_tested.filters) == 1 else "Filters"
                codes = " ".join(f"<code>{esc(value)}</code>" for value in data.nothing_tested.filters)
                detail = f"{label}: {codes}"
            tip = _tip(data.nothing_tested.tip)
        else:
            # Cases ran without any check applying; the skip reasons say why, as the terminal does.
            reasons = data.summary.operations.skip_reasons if data.summary.operations is not None else []
            title = ", ".join(reasons) or "No operations were tested"
    else:
        subtitle = "No tests ran"
        if data.fatal_error is not None:
            title = data.fatal_error.title
            detail = esc(data.fatal_error.message)
            tip = _tip(data.fatal_error.tip)
        else:
            title = "No operation could be tested"
            detail = esc("\n".join(f"{group.title}: {group.count}" for group in data.summary.errors))
    detail_html = f'<p class="ex-detail">{detail}</p>' if detail else ""
    return (
        f'<section class="hero-strip is-message verdict-{verdict.css}">'
        f'<div class="hs-cell hs-verdict"><h1 class="hero-status-label">{esc(verdict.value)}</h1>'
        f'<div class="hs-sub">{esc(subtitle)}</div></div>'
        f'<div class="hs-cell hs-explain"><div class="ex-title">{esc(title)}</div>{detail_html}{tip}</div>'
        "</section>"
    )


def _metric(label: str, value: str, subtitle: str = "", *, extra_class: str = "") -> str:
    subtitle_html = f'<span class="m-sub">{esc(subtitle)}</span>' if subtitle else ""
    return (
        f'<div class="hs-cell hs-metric{extra_class}"><span class="m-label">{esc(label)}</span>'
        f'<span class="m-value">{esc(value)}</span>{subtitle_html}</div>'
    )


def _second_cell(data: ReportData) -> str:
    if data.top_failures:
        rows = "".join(
            f'<li><span class="tf-n">{group.count}</span>'
            f'<span class="tf-name" title="{esc(group.title)}">{esc(group.title)}</span></li>'
            for group in data.top_failures[:3]
        )
        return (
            '<div class="hs-cell hs-top-failures hs-second"><span class="m-label">Top failures</span>'
            f'<ul class="tf-list">{rows}</ul></div>'
        )
    subtitle = {Verdict.PASSED: "all passing", Verdict.INTERRUPTED: "none so far"}.get(data.verdict, "")
    return _metric("Failing checks", "0", subtitle, extra_class=" hs-second")


def _verdict_line(data: ReportData) -> tuple[str, str]:
    tested = data.tested_operations
    failed = min(len(data.failed_operations), tested)
    if failed:
        text = f"{failed} of {plural(tested, 'tested operation')} failed"
        passed = tested - failed
        passed_segment = f'<span class="seg-passed" style="flex: {passed}"></span>' if passed else ""
        bar = (
            f'<div class="fail-mix-bar" role="img" aria-label="{esc(text)}">'
            f'<span class="seg-failed" style="flex: {failed}"></span>{passed_segment}</div>'
        )
        return bar, text
    if data.summary.failures:
        return "", "Failures not attributed to a tested operation"
    if data.verdict is Verdict.FAILED:
        baseline = data.summary.baseline
        return "", baseline.write_error if baseline is not None and baseline.write_error else "Run failed"
    if data.verdict is Verdict.ERRORED:
        return "", f"{plural(data.errored_operations, 'operation')} errored"
    text = "1 tested operation passed" if tested == 1 else f"All {tested} tested operations passed"
    bar = (
        f'<div class="fail-mix-bar" role="img" aria-label="{esc(text)}">'
        '<span class="seg-passed" style="flex: 1"></span></div>'
    )
    return bar, text


def _stop_note(data: ReportData) -> str:
    if data.verdict is Verdict.INTERRUPTED:
        note: str | None = "Stopped by user, partial results"
    else:
        note = data.stop_reason.skip_explanation if data.stop_reason is not StopReason.INTERRUPTED else None
    return f'<div class="hs-stop-note">{esc(note)}</div>' if note else ""


def _result(data: ReportData) -> str:
    verdict = data.verdict
    bar, text = _verdict_line(data)
    phases = data.executed_phase_count
    cases_subtitle = f"across {plural(phases, 'phase')}" if phases else ""
    operations_parts = [f"{data.tested_operations} tested"]
    for count, word in (
        (data.not_run_operations, "not run"),
        (data.skipped_operations, "skipped"),
        (data.errored_operations, "errored"),
    ):
        if count:
            operations_parts.append(f"{count} {word}")
    return (
        f'<section class="hero-strip verdict-{verdict.css}">'
        f'<div class="hs-cell hs-verdict"><h1 class="hero-status-label">{esc(verdict.value)}</h1>'
        f'{bar}<div class="hs-sub">{esc(text)}</div>{_stop_note(data)}</div>'
        f"{_second_cell(data)}"
        f"{_metric('Cases run', f'{data.summary.test_cases.generated:,}', cases_subtitle)}"
        f"{_metric('Operations', str(data.selected_operations), ', '.join(operations_parts))}"
        f"{_metric('Duration', _duration(data))}"
        "</section>"
    )


def _copy_button(value: str, name: str) -> str:
    return f'<button type="button" class="copy-btn" data-copy="{esc(value)}" aria-label="Copy {name}">Copy</button>'


def _row(key: str, value: str, copy: str = "") -> str:
    return f'<span class="tk">{key}</span><span class="tv"><span class="tv-text">{value}</span>{copy}</span>'


def _link(url: str) -> str:
    return f'<a href="{esc(url)}">{esc(url)}</a>'


def _is_url(value: str) -> bool:
    return value.startswith(("http://", "https://"))


def _command(command: str) -> str:
    # Lines break between arguments, never inside a flag.
    arguments = " ".join(
        f'<span class="arg{" arg-long" if len(argument) > _LONG_ARGUMENT else ""}">{esc(argument)}</span>'
        for argument in command.split(" ")
    )
    return f"<code>{arguments}</code>"


def _target_block(data: ReportData) -> str:
    meta = data.meta
    rows = []
    if meta.base_url:
        rows.append(_row("Base URL", _link(meta.base_url), _copy_button(meta.base_url, "base URL")))
    if meta.location:
        # A local spec cannot be opened from a copied-around report, so it is plain text.
        value = _link(meta.location) if _is_url(meta.location) else esc(meta.location)
        rows.append(_row("Spec", value, _copy_button(meta.location, "spec")))
    rows.append(_row("Command", _command(meta.command), _copy_button(meta.command, "command")))
    if meta.seed is not None:
        rows.append(_row("Seed", f"<code>{meta.seed}</code>", _copy_button(str(meta.seed), "seed")))
    return f'<section class="target-block">{"".join(rows)}</section>'


# Above this many rows a failing run collapses its Passed group so failures stay in view.
_COLLAPSE_PASSED_ABOVE = 25


def _path(path: str) -> str:
    # Long paths wrap before a `/`, not inside a segment; `<wbr>` adds nothing to copied text.
    head, *segments = esc(path).split("/")
    return "<wbr>/".join([head + "/" + segments[0], *segments[1:]]) if segments else head


def _operation_cell(row: OperationRow) -> str:
    method = f'<span class="method {esc(row.method.lower())}">{esc(row.method)}</span>' if row.method else ""
    return f'<td class="op-cell"><span class="op">{method}<span class="path">{_path(row.path)}</span></span></td>'


def _failures_cell(row: OperationRow) -> str:
    if not row.failures:
        note = f'<span class="op-note">{esc(row.note)}</span>' if row.note else ""
        return f'<td class="failures-cell">{note}</td>'
    first, *rest = row.failures
    title = f'<span class="failure-title">{esc(first)}</span>'
    if not rest:
        return f'<td class="failures-cell">{title}</td>'
    items = "".join(f"<li>{esc(name)}</li>" for name in rest)
    return (
        '<td class="failures-cell"><details class="fails"><summary>'
        f'{title}<span class="more"><span class="m-closed">+{len(rest)} more</span><span class="m-open">less</span></span>'
        f'</summary><ul class="more-list">{items}</ul></details></td>'
    )


def _cases_cell(row: OperationRow) -> str:
    # Zero would read as "ran and found nothing".
    if not row.cases:
        return '<td class="numeric na"><span class="none" aria-label="no cases run">-</span></td>'
    return f'<td class="numeric">{row.cases:,}</td>'


def _operation_order(row: OperationRow) -> tuple[int, str, str]:
    return (-len(row.failures), row.path, row.method)


def _group_header(status: OperationStatus, rows: list[OperationRow], *, collapsible: bool) -> str:
    title = f'<span class="group-title">{esc(status.value)}</span><span class="group-count">{len(rows)}</span>'
    css = status.css
    if collapsible:
        title = (
            f'<label class="group-toggle"><input type="checkbox" aria-label="Show {len(rows)} passed operations">'
            f'<span class="chev" aria-hidden="true"></span>{title}<span class="tg-hint" aria-hidden="true"></span></label>'
        )
    # Failed operations usually stop at their first failure, which explains their small case counts.
    if status is OperationStatus.FAILED and all(row.stops_at_first_failure for row in rows):
        return (
            f'<tr class="group-row group-{css}"><th colspan="1" scope="rowgroup" id="group-{css}">{title}</th>'
            '<td colspan="2" class="group-note">until first failure</td></tr>'
        )
    return f'<tr class="group-row group-{css}"><th colspan="3" scope="rowgroup" id="group-{css}">{title}</th></tr>'


def _operations_table(data: ReportData) -> str:
    if not data.operations:
        return ""
    has_failures = any(row.status is OperationStatus.FAILED for row in data.operations)
    collapse_passed = has_failures and len(data.operations) > _COLLAPSE_PASSED_ABOVE
    groups = []
    for status in OperationStatus:
        rows = sorted((row for row in data.operations if row.status is status), key=_operation_order)
        if not rows:
            continue
        header = _group_header(status, rows, collapsible=collapse_passed and status is OperationStatus.PASSED)
        body = "".join(
            f'<tr class="op-row row-{status.css}">{_operation_cell(row)}{_failures_cell(row)}{_cases_cell(row)}</tr>'
            for row in rows
        )
        groups.append(f'<tbody class="ops-group">{header}{body}</tbody>')
    footer_rows = []
    if data.unattributed_failures:
        items = "".join(
            f"<li>{esc(title)}" + (f'<span class="fx">x{count}</span>' if count > 1 else "") + "</li>"
            for title, count in data.unattributed_failures
        )
        footer_rows.append(
            '<tr class="run-level-row"><td colspan="3">'
            f'<span class="foot-label">Not tied to an operation</span><ul>{items}</ul></td></tr>'
        )
    if data.not_run_operations:
        reason = data.stop_reason.skip_explanation or StopReason.INTERRUPTED.skip_explanation
        footer_rows.append(
            '<tr class="not-run-row"><td colspan="3">'
            f"{esc(plural(data.not_run_operations, 'operation'))} not run: {esc(reason)}</td></tr>"
        )
    footer = f"<tfoot>{''.join(footer_rows)}</tfoot>" if footer_rows else ""
    return (
        '<section class="ops"><table class="ops-table" aria-label="Operations">'
        '<thead><tr><th scope="col">Operation</th><th scope="col">Failures</th>'
        '<th scope="col" class="numeric">Cases</th></tr></thead>'
        f"{''.join(groups)}{footer}</table></section>"
    )
