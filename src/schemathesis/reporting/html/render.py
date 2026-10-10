from __future__ import annotations

import re
from html import escape
from importlib.resources import files
from typing import TYPE_CHECKING

from schemathesis.core.failures import reason_phrase
from schemathesis.core.output import TRUNCATED, escape_surrogates
from schemathesis.core.version import SCHEMATHESIS_VERSION
from schemathesis.engine import StopReason
from schemathesis.reporting.html.model import Command, OperationStatus, Verdict, split_label

if TYPE_CHECKING:
    from schemathesis.cli.commands.run.handlers.output import WarningBlock, WarningGroup, WarningItem
    from schemathesis.reporting.html.model import ErrorEntry, FailingCase, OperationRow, ReportData

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
# A `#hash` link opens the row holding its target, and any `<details>` around it.
_HASH_SCRIPT = (
    "<script>(function(){function openTo(){var id=location.hash.slice(1);if(!id)return;"
    "var el=document.getElementById(id);if(!el)return;var d=el.closest('.detail-row');"
    "var row=d?d.previousElementSibling:el.closest('.has-details');var box=row&&row.querySelector('.row-open');"
    "if(box)box.checked=true;for(var p=el;p;p=p.parentElement){if(p.tagName==='DETAILS')p.open=true}}"
    "window.addEventListener('hashchange',openTo);openTo()})()</script>"
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
{_COPY_SCRIPT}{_HASH_SCRIPT}
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
    anchors = _operation_anchors(data.operations)
    parts = [
        report_top(generated_at=data.meta.generated_at),
        _hero(data),
        _warnings(data, anchors),
        _target_block(data),
        _operations_table(data, anchors),
        _extraction_failures(data),
    ]
    body = "\n".join(part for part in parts if part)
    return page(title="Schemathesis Report", body=body)


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def _warning_id(block: WarningBlock) -> str:
    return f"w-{_slug(block.title)}"


def _warning_item(item: WarningItem, anchors: dict[str, str]) -> str:
    details = "\n".join(item.details)
    extra = f'<pre class="w-details">{esc(details)}</pre>' if details else ""
    anchor = anchors.get(item.operation) if item.operation is not None else None
    if item.operation is None or anchor is None:
        return f"<li><code>{esc(item.text)}</code>{extra}</li>"
    method, path = split_label(item.operation)
    badge = _method(method) if method is not None else ""
    # Text after the label, e.g. "(fuzzing): 10% accepted".
    rest = item.text[len(item.operation) :].strip()
    note = f'<span class="w-item-note">{esc(rest)}</span>' if rest else ""
    return f'<li><a class="w-op" href="#{anchor}">{badge}<span class="path">{esc(path)}</span></a>{note}{extra}</li>'


def _warning_group(group: WarningGroup, anchors: dict[str, str]) -> str:
    heading = f'<h4 class="w-group-title">{esc(group.heading)}</h4>' if group.heading is not None else ""
    items = "".join(_warning_item(item, anchors) for item in group.items)
    tips = "".join(f'<p class="w-tip"><b>Tip:</b> {_code_spans(tip)}</p>' for tip in group.tips or [])
    return f'<div class="w-group">{heading}<ul class="w-items">{items}</ul>{tips}</div>'


def _affecting_warnings(data: ReportData) -> list[WarningBlock]:
    return [block for block in data.warnings if block.affects_verdict]


def _warnings(data: ReportData, anchors: dict[str, str]) -> str:
    if not data.warnings and not data.startup_warnings:
        return ""
    entries = [
        f'<li class="warning startup"><span>{_code_spans(message)}</span></li>' for message in data.startup_warnings
    ]
    # Warnings that change how the verdict reads come first and start open.
    for block in sorted(data.warnings, key=lambda block: not block.affects_verdict):
        opened = " open" if block.affects_verdict else ""
        note = "" if block.affects_verdict else " note"
        groups = "".join(_warning_group(group, anchors) for group in block.groups)
        entries.append(
            f'<li class="warning{note}"><details id="{_warning_id(block)}"{opened}><summary>'
            f'<span class="dchev" aria-hidden="true"></span><span class="w-title">{esc(block.title)}</span>'
            f'<span class="w-summary">{_code_spans(block.summary)}</span></summary>'
            f'<div class="w-body">{groups}</div></details></li>'
        )
    return (
        f'<section class="warnings" aria-labelledby="w-heading"><h2 class="w-heading" id="w-heading">Warnings</h2>'
        f'<ul class="w-list">{"".join(entries)}</ul></section>'
    )


def _at_risk(data: ReportData) -> str:
    return " at-risk" if _affecting_warnings(data) else ""


def _exit_note(data: ReportData) -> str:
    note = data.exit_note
    if note is None:
        return ""
    text, target = note
    if target == "warnings":
        target = _warning_id(min(data.warnings, key=lambda block: not block.affects_verdict))
    href = f' href="#{target}"' if target else ""
    tag = "a" if target else "span"
    return f'<{tag} class="hs-exit"{href}>{esc(text)}</{tag}>'


# The default run enables over a dozen checks; the row shows the first few, like the Failures cell.
_CHECKS_SHOWN = 5


def _checks(names: list[str]) -> str:
    shown, rest = names[:_CHECKS_SHOWN], names[_CHECKS_SHOWN:]
    first = "".join(f"<code>{esc(name)}</code>" for name in shown)
    if not rest:
        return f'<span class="checks-inline">{first}</span>'
    more = f'<span class="more"><span class="m-closed">+{len(rest)} more</span><span class="m-open">less</span></span>'
    others = "".join(f"<code>{esc(name)}</code>" for name in rest)
    return (
        f'<details class="fails checks"><summary><span class="checks-inline">{first}{more}</span></summary>'
        f'<span class="checks-inline checks-rest">{others}</span></details>'
    )


def _note_rows(data: ReportData) -> list[str]:
    rows = []
    baseline = data.summary.baseline
    if baseline is not None:
        facts = [("Known failures", str(baseline.known))]
        if baseline.recorded is not None:
            facts.append(("Recorded", str(baseline.recorded)))
        if baseline.pruned is not None:
            facts.append(("Pruned", str(len(baseline.pruned))))
        if baseline.unobserved:
            facts.append(("Unobserved entries", str(baseline.unobserved)))
        if baseline.expired_ids:
            facts.append(("Expired entries", f"{len(baseline.expired_ids)} ({', '.join(baseline.expired_ids)})"))
        items = "".join(
            f'<li><span class="k">{esc(key)}</span><span class="v">{esc(value)}</span></li>' for key, value in facts
        )
        error = f'<p class="note-error">{esc(baseline.write_error)}</p>' if baseline.write_error else ""
        rows.append(_note_row("Baseline", "note-baseline", f'<ul class="rn-facts">{items}</ul>{error}'))
    if data.summary.filtered:
        rows.append(
            _note_row(
                "Filtered failures",
                "note-filtered",
                f"<p>{plural(data.summary.filtered, 'failure')} dropped by <code>filter_failure</code></p>",
            )
        )
    if data.reauth_count or data.reauth_broke:
        parts = []
        if data.reauth_count:
            parts.append(f"<p>Re-authenticated {plural(data.reauth_count, 'time')}</p>")
        if data.reauth_broke:
            parts.append(
                '<p class="note-error">Authentication stopped working mid-run - credentials likely invalidated</p>'
            )
        rows.append(_note_row("Authentication", "note-reauth", "".join(parts)))
    return rows


def _note_row(key: str, anchor: str, value: str) -> str:
    return (
        f'<span class="tk" id="{anchor}">{key}</span>'
        f'<span class="tv"><span class="tv-text tv-note">{value}</span></span>'
    )


def _extraction_failures(data: ReportData) -> str:
    if not data.extraction_failures:
        return ""
    cards = []
    for failure in data.extraction_failures:
        title, *messages = failure.reason
        details = "".join(f'<pre class="msg">{_code_spans(message)}</pre>' for message in messages)
        parts = [
            f'<div class="case-owner"><span class="ex-link">{esc(failure.link)}</span></div>',
            f'<div class="finding"><h4 class="finding-title">{_code_spans(title)}</h4>{details}</div>',
            _evidence("Response", _body(failure.body), _status(failure.status_code)),
        ]
        if failure.commands:
            parts.append(_reproduce(failure.commands, None))
        cards.append(
            f'<article class="case" id="case-{esc(failure.case_id)}"><div class="case-body">{"".join(parts)}</div>'
            f'<div class="case-meta"><span class="case-id">{esc(failure.case_id)}</span></div></article>'
        )
    return (
        '<section class="run-notes" id="note-extraction" aria-labelledby="note-extraction-h">'
        '<h2 class="rn-title" id="note-extraction-h">Failed to extract data from response</h2>'
        f'<div class="case-list">{"".join(cards)}</div></section>'
    )


def _caveat(data: ReportData) -> str:
    affecting = _affecting_warnings(data)
    # One amber line in the verdict cell: the exit note already points at the warnings.
    if not affecting or data.exit_note is not None:
        return ""
    return f'<a class="hs-caveat" href="#{_warning_id(affecting[0])}">{plural(len(affecting), "warning")}</a>'


def _duration(data: ReportData) -> str:
    return humanize_duration(data.running_time) if data.running_time is not None else "-"


def _code_spans(text: str) -> str:
    return re.sub(r"`([^`]+)`", r"<code>\1</code>", esc(text))


def _tip(text: str | None) -> str:
    if text is None:
        return ""
    return f'<p class="ex-tip"><b>Tip:</b> {_code_spans(text)}</p>'


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
        f'<section class="hero-strip is-message verdict-{verdict.css}{_at_risk(data)}">'
        f'<div class="hs-cell hs-verdict"><h1 class="hero-status-label">{esc(verdict.value)}</h1>'
        f'<div class="hs-sub">{esc(subtitle)}</div>{_caveat(data)}{_exit_note(data)}</div>'
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
        return "", "Failures not tied to an operation"
    if data.verdict is Verdict.ERRORED:
        errored = sum(row.status is OperationStatus.ERRORED for row in data.operations)
        return "", f"{plural(errored, 'operation')} errored" if errored else "Errors not tied to an operation"
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
        f'<section class="hero-strip verdict-{verdict.css}{_at_risk(data)}">'
        f'<div class="hs-cell hs-verdict"><h1 class="hero-status-label">{esc(verdict.value)}</h1>'
        f'{bar}<div class="hs-sub">{esc(text)}</div>{_stop_note(data)}{_caveat(data)}{_exit_note(data)}</div>'
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
    if data.phases:
        phases = "".join(
            f'<li class="phase phase-{kind}"><span class="ph-name">{esc(name)}</span>'
            f'<span class="ph-outcome">{esc(outcome)}</span></li>'
            for name, kind, outcome in data.phases
        )
        rows.append(_row("Phases", f'<ol class="phases">{phases}</ol>'))
    if data.checks:
        rows.append(_row("Checks", _checks(data.checks)))
    rows.extend(_note_rows(data))
    return f'<section class="target-block">{"".join(rows)}</section>'


# Above this many rows a failing run collapses its Passed group so failures stay in view.
_COLLAPSE_PASSED_ABOVE = 25


def _path(path: str) -> str:
    # Long paths wrap before a `/`, not inside a segment; `<wbr>` adds nothing to copied text.
    head, *segments = esc(path).split("/")
    return "<wbr>/".join([head + "/" + segments[0], *segments[1:]]) if segments else head


def _operation_cell(row: OperationRow, toggle: str) -> str:
    method = _method(row.method) if row.method else ""
    warned = ""
    if row.warnings:
        # Name the warning that matters most; the label lists them all.
        first = next((block for block in row.warnings if block.affects_verdict), row.warnings[0])
        more = f" +{len(row.warnings) - 1}" if len(row.warnings) > 1 else ""
        titles = ", ".join(block.title for block in row.warnings)
        warned = (
            f'<a class="row-warn" href="#{_warning_id(first)}" aria-label="Warning: {esc(titles)}">'
            f"{esc(first.title)}{more}</a>"
        )
    return (
        f'<td class="op-cell">{toggle}<span class="op">{method}<span class="path">{_path(row.path)}</span>'
        f"{warned}</span></td>"
    )


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


_CHEVRON = '<span class="rchev" aria-hidden="true"></span>'


def _toggle(target: str, name: str, *, checked: bool) -> str:
    state = " checked" if checked else ""
    return (
        f'<label class="row-toggle"><input type="checkbox" class="row-open" aria-controls="{target}" '
        f'aria-label="Details for {esc(name)}"{state}></label>'
    )


def _cases_cell(row: OperationRow, chevron: str) -> str:
    # Zero would read as "ran and found nothing".
    if not row.cases:
        return f'<td class="numeric na"><span class="none" aria-label="no cases run">-</span>{chevron}</td>'
    # Narrow layouts spell out the unit after the number.
    one = " one" if row.cases == 1 else ""
    return f'<td class="numeric{one}">{row.cases:,}{chevron}</td>'


def _operation_anchors(operations: list[OperationRow]) -> dict[str, str]:
    # Same order as the table, so a suffix for a colliding slug is stable.
    taken: set[str] = set()
    return {
        row.label: _anchor(row.label, taken)
        for status in OperationStatus
        for row in sorted((row for row in operations if row.status is status), key=_operation_order)
    }


def _anchor(label: str, taken: set[str]) -> str:
    base = "op-" + (_slug(label) or "operation")
    anchor, index = base, 2
    # The detail row's id derives from the anchor, so it must not collide either.
    while anchor in taken or f"{anchor}-details" in taken:
        anchor, index = f"{base}-{index}", index + 1
    taken.update((anchor, f"{anchor}-details"))
    return anchor


def _status(code: int) -> str:
    return f'<span class="status s{code // 100}xx"><span class="status-code">{code}</span> {esc(reason_phrase(code))}</span>'


def _evidence(label: str, content: str, extra: str = "") -> str:
    return (
        f'<section class="evidence"><div class="ev-head"><span class="ev-label">{label}</span>{extra}</div>'
        f"{content}</section>"
    )


_PLACEHOLDERS = ("<EMPTY>", "<BINARY>")
# Longer one-line bodies read better in a box.
_INLINE_BODY = 80


def _body(body: str) -> str:
    if body in _PLACEHOLDERS:
        return f'<span class="body-placeholder">{esc(body)}</span>'
    if body.endswith(TRUNCATED):
        return f'<pre class="body">{esc(body[: -len(TRUNCATED)])}<span class="trunc">{TRUNCATED}</span></pre>'
    if "\n" not in body and len(body) <= _INLINE_BODY:
        return f'<span class="body-inline">{esc(body)}</span>'
    return f'<pre class="body">{esc(body)}</pre>'


def _method(method: str) -> str:
    return f'<span class="method {esc(method.lower())}">{esc(method)}</span>'


def _reproduce(commands: list[Command], replay: str | None) -> str:
    curls = [command.curl for command in commands]
    copy = "\n".join(curls) + (f"\n\n{replay}" if replay is not None else "")
    if len(commands) == 1:
        content = f'<pre class="cmd" tabindex="0">{esc(curls[0])}</pre>'
    else:
        steps = []
        for command in commands:
            label = ""
            if command.path is not None:
                failed = '<span class="step-failed">failed here</span>' if command.failed else ""
                method = _method(command.method) if command.method is not None else ""
                label = f'<span class="step-req">{method}{esc(command.path)}{failed}</span>'
            steps.append(f'<li>{label}<pre class="cmd" tabindex="0">{esc(command.curl)}</pre></li>')
        content = f'<ol class="steps">{"".join(steps)}</ol>'
    replay_line = f'<p class="replay">or <code>{esc(replay)}</code></p>' if replay is not None else ""
    return _evidence(
        "Reproduce", f'<div class="repro">{content}{_copy_button(copy, "reproduction")}</div>{replay_line}'
    )


def _failing_case(case: FailingCase, owner: str = "") -> str:
    parts = [owner]
    for failure in case.failures:
        count = len(failure.messages)
        violations = f'<span class="violations">{count} violations</span>' if count > 1 else ""
        messages = "".join(f'<pre class="msg">{esc(message)}</pre>' for message in failure.messages if message)
        parts.append(
            f'<div class="finding"><h4 class="finding-title">{esc(failure.title)}{violations}</h4>{messages}</div>'
        )
    if case.status_code is not None and case.body is not None:
        parts.append(_evidence("Response", _body(case.body), _status(case.status_code)))
    if case.commands:
        parts.append(_reproduce(case.commands, case.replay))
    meta = ""
    case_id = ""
    if case.case_id is not None:
        case_id = f' id="case-{esc(case.case_id)}"'
        identity = (
            f'<span class="case-identity">as <code>{esc(case.auth_identity)}</code></span>'
            if case.auth_identity is not None
            else ""
        )
        meta = f'<div class="case-meta"><span class="case-id">{esc(case.case_id)}</span>{identity}</div>'
    return f'<article class="case"{case_id}><div class="case-body">{"".join(parts)}</div>{meta}</article>'


_FRAME = re.compile(r'^\s*File "(?P<path>[^"]+)", line (?P<line>\d+)')
_FRAMEWORK_FRAME = re.compile(r"[/\\](?:src|site-packages)[/\\]schemathesis[/\\]")


def _traceback(lines: list[str]) -> str:
    frames = [match for match in map(_FRAME.match, lines) if match is not None]
    summary = "Traceback"
    if frames:
        # The last frame outside Schemathesis, or the last frame when every one is inside it.
        where = ([frame for frame in frames if not _FRAMEWORK_FRAME.search(frame["path"])] or frames)[-1]
        name = re.split(r"[/\\]", where["path"])[-1]
        summary = (
            f"Traceback ({plural(len(frames), 'frame')}), raised in "
            f'<span class="trace-where"><code>{esc(name)}:{where["line"]}</code></span>'
        )
    text = "\n".join(lines)
    return (
        f'<details class="trace"><summary><span class="dchev" aria-hidden="true"></span>{summary}</summary>'
        f'<pre tabindex="0">{esc(text)}</pre></details>'
    )


def _error(error: ErrorEntry, *, with_title: bool, owner: str = "") -> str:
    lead = "" if with_title else " lead"
    parts = [f'<h4 class="finding-title">{esc(error.title)}</h4>' if with_title else ""]
    parts.append(f'<pre class="msg{lead}">{esc(error.message)}</pre>')
    if error.details and error.details[0].startswith("Traceback"):
        parts.append(_traceback(error.details))
    elif error.details:
        details = "\n".join(error.details)
        parts.append(f'<pre class="msg msg-detail{lead}">{esc(details)}</pre>')
    sections = [owner, f'<div class="finding">{"".join(parts)}</div>']
    if error.reproduce is not None:
        sections.append(_reproduce([Command(curl=error.reproduce, method=None, path=None, failed=False)], None))
    if error.tip is not None:
        sections.append(_evidence("Tip", f'<p class="tip">{_code_spans(error.tip)}</p>'))
    return f'<article class="case case-error"><div class="case-body">{"".join(sections)}</div></article>'


def _case_owner(label: str) -> str:
    method, path = split_label(label)
    badge = _method(method) if method is not None else ""
    return f'<div class="case-owner">{badge}<span class="path">{esc(path)}</span></div>'


def _has_details(row: OperationRow) -> bool:
    return bool(row.failing_cases or row.errors)


def _detail_row(items: list[str], anchor: str, css: str) -> str:
    return (
        f'<tr class="detail-row detail-{css}" id="{anchor}-details"><td colspan="3">'
        f'<div class="case-list">{"".join(items)}</div></td></tr>'
    )


def _row_items(row: OperationRow) -> list[str]:
    items = [_failing_case(case) for case in row.failing_cases]
    # A lone error on an errored row repeats the title the row already shows.
    with_title = bool(row.failing_cases) or len(row.errors) > 1
    items.extend(_error(error, with_title=with_title) for error in row.errors)
    return items


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


def _operations_table(data: ReportData, anchors: dict[str, str]) -> str:
    if not data.operations:
        return ""
    has_failures = any(row.status is OperationStatus.FAILED for row in data.operations)
    collapse_passed = has_failures and len(data.operations) > _COLLAPSE_PASSED_ABOVE
    groups = []
    # A single expandable block answers "what broke" with nothing competing for the space, so it starts open.
    open_details = (
        sum(map(_has_details, data.operations)) + bool(data.unattributed_cases or data.unattributed_errors) == 1
    )
    for status in OperationStatus:
        rows = sorted((row for row in data.operations if row.status is status), key=_operation_order)
        if not rows:
            continue
        header = _group_header(status, rows, collapsible=collapse_passed and status is OperationStatus.PASSED)
        body = ""
        for row in rows:
            anchor = anchors[row.label]
            if _has_details(row):
                toggle = _toggle(f"{anchor}-details", row.label, checked=open_details)
                body += (
                    f'<tr class="op-row row-{status.css} has-details" id="{anchor}">'
                    f"{_operation_cell(row, toggle)}{_failures_cell(row)}{_cases_cell(row, _CHEVRON)}</tr>"
                    f"{_detail_row(_row_items(row), anchor, status.css)}"
                )
            else:
                body += (
                    f'<tr class="op-row row-{status.css}" id="{anchor}">'
                    f"{_operation_cell(row, '')}{_failures_cell(row)}{_cases_cell(row, '')}</tr>"
                )
        groups.append(f'<tbody class="ops-group">{header}{body}</tbody>')
    footer_rows = []
    if data.unattributed_failures or data.unattributed_errors:
        titles = [*data.unattributed_failures, *((error.title, 1) for _, error in data.unattributed_errors)]
        items = "".join(
            f"<li>{esc(title)}" + (f'<span class="fx">x{count}</span>' if count > 1 else "") + "</li>"
            for title, count in titles
        )
        label = f'<span class="foot-label">Not tied to an operation</span><ul>{items}</ul>'
        toggle = _toggle("run-level-details", "problems not tied to an operation", checked=open_details)
        cases = [_failing_case(case, owner=_case_owner(owner)) for owner, case in data.unattributed_cases]
        cases.extend(
            _error(error, with_title=True, owner=_case_owner(owner)) for owner, error in data.unattributed_errors
        )
        footer_rows.append(
            f'<tr class="run-level-row has-details" id="run-level"><td colspan="2">{toggle}{label}</td>'
            f'<td class="numeric">{_CHEVRON}</td></tr>'
            f'<tr class="detail-row detail-failed run-level-details" id="run-level-details"><td colspan="3">'
            f'<div class="case-list">{"".join(cases)}</div></td></tr>'
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
