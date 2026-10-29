from __future__ import annotations

import re
from collections import Counter
from html import escape
from importlib.resources import files
from typing import TYPE_CHECKING, NamedTuple

from schemathesis.core.failures import reason_phrase
from schemathesis.core.output import TRUNCATED, escape_surrogates, pluralize
from schemathesis.core.version import SCHEMATHESIS_VERSION
from schemathesis.engine import StopReason
from schemathesis.reporting._command import UNKNOWN_ENTRYPOINT
from schemathesis.reporting.html.model import Command, OperationStatus, Verdict, split_label
from schemathesis.reporting.html.timeline import (
    Lane,
    Marker,
    axis_end,
    axis_label,
    axis_ticks,
    build_lanes,
    collect_markers,
    curve_path,
    failure_count,
    lane_ticks,
    last_phase,
    mark_classes,
    position,
    quiet_stretch,
    short_duration,
    tick_lines,
    total_duration,
)

if TYPE_CHECKING:
    from schemathesis.core.failures import Severity
    from schemathesis.engine.summary import WarningBlock, WarningGroup, WarningItem
    from schemathesis.reporting.html.model import ErrorEntry, FailingCase, OperationRow, ReportData

_LOGO = (files("schemathesis.reporting.html") / "assets" / "logo.svg").read_text(encoding="utf-8").strip()
_CSS = (files("schemathesis.reporting.html") / "assets" / "report.css").read_text(encoding="utf-8").strip()
_NAV_SCRIPT = (files("schemathesis.reporting.html") / "assets" / "nav.js").read_text(encoding="utf-8").strip()


def esc(value: object) -> str:
    return escape(escape_surrogates(str(value)), quote=True)


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


# Copies the value shown next to the button, a reproduction's commands followed by its replay command,
# or a permalink URL. `navigator.clipboard` may be unavailable or denied on `file://`, hence the fallback.
_COPY_SCRIPT = (
    "<script>(function(){function copy(t,done){function legacy(){"
    "var a=document.createElement('textarea');a.value=t;document.body.appendChild(a);a.select();"
    "try{document.execCommand('copy');done()}catch(e){}a.remove()}"
    "if(navigator.clipboard&&window.isSecureContext){navigator.clipboard.writeText(t).then(done,legacy)}else{legacy()}}"
    "document.querySelectorAll('.copy-btn').forEach(function(b){b.addEventListener('click',function(){"
    "var r=b.closest('.repro'),t;if(r){t=[].map.call(r.querySelectorAll('.cmd'),function(c){return c.textContent})"
    ".join('\\n');var p=r.parentNode.querySelector('.replay code');if(p)t+='\\n\\n'+p.textContent}"
    "else{t=b.previousElementSibling.textContent}"
    "copy(t,function(){b.textContent='Copied';setTimeout(function(){b.textContent='Copy'},1200)})})});"
    "var live=document.getElementById('nav-live');"
    "document.addEventListener('click',function(e){"
    "var a=e.target.closest&&e.target.closest('a.permalink,a.permalink-id');if(!a)return;"
    "copy(location.href.split('#')[0]+a.getAttribute('href'),function(){"
    "a.classList.add('copied');live.textContent='';setTimeout(function(){live.textContent='Link copied'},30);"
    "setTimeout(function(){a.classList.remove('copied')},1600)})})})()</script>"
)
# Command arguments longer than this may break anywhere; shorter ones never break inside.
_LONG_ARGUMENT = 40


def page(*, title: str, body: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <script>document.documentElement.classList.add("js")</script>
  <title>{esc(title)}</title>
  <style>{_CSS}</style>
</head>
<body>
  <main class="container">
{body}
  </main>
  <div id="nav-live" class="vh" role="status" aria-live="polite"></div>
{_COPY_SCRIPT}<script>{_NAV_SCRIPT}</script>
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
        _target_block(data),
        _warnings(data, anchors),
        _timeline(data, anchors),
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


# A warning can name hundreds of operations; the rest wait behind an expander so the table stays in view.
_WARNING_ITEMS_SHOWN = 10


def _warning_group(group: WarningGroup, anchors: dict[str, str]) -> str:
    heading = f'<h4 class="w-group-title">{esc(group.heading)}</h4>' if group.heading is not None else ""
    shown, rest = group.items[:_WARNING_ITEMS_SHOWN], group.items[_WARNING_ITEMS_SHOWN:]
    items = f'<ul class="w-items">{"".join(_warning_item(item, anchors) for item in shown)}</ul>'
    if rest:
        more = (
            f'<span class="more"><span class="m-closed">+{len(rest)} more</span><span class="m-open">less</span></span>'
        )
        others = "".join(_warning_item(item, anchors) for item in rest)
        items += f'<details class="fails w-more"><summary>{more}</summary><ul class="w-items">{others}</ul></details>'
    tips = "".join(f'<p class="w-tip"><b>Tip:</b> {_code_spans(tip)}</p>' for tip in group.tips or [])
    return f'<div class="w-group">{heading}{items}{tips}</div>'


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


# The default run enables many checks; the row shows the first few, like the Failures cell.
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
                f"<p>{pluralize(data.summary.filtered, 'failure')} dropped by <code>filter_failure</code></p>",
            )
        )
    if data.reauth_count or data.reauth_broke:
        parts = []
        if data.reauth_count:
            parts.append(f"<p>Re-authenticated {pluralize(data.reauth_count, 'time')}</p>")
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


_EXTRACTION_TIP = "The response was not in the format the link expects, so the link could not be followed."


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
            _reproduce(failure.commands, None),
            _evidence("Tip", f'<p class="tip">{_EXTRACTION_TIP}</p>'),
        ]
        cards.append(f'<article class="case"><div class="case-body">{"".join(parts)}</div></article>')
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
    verb = "affects" if len(affecting) == 1 else "affect"
    text = f"{pluralize(len(affecting), 'warning')} {verb} results"
    return f'<a class="hs-caveat" href="#{_warning_id(affecting[0])}">{text}</a>'


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
    if data.verdict in _MESSAGES and not data.tested_operations:
        return _message(data)
    return _result(data)


_NO_CHECKS_TIP = "enable checks with `--checks all` or in the `[checks]` section of `schemathesis.toml`."


class Message(NamedTuple):
    subtitle: str
    title: str
    # Markup, already escaped.
    detail: str = ""
    tip: str | None = None


def _interrupted_message(data: ReportData) -> Message:
    detail = f"Ctrl-C during the {data.last_phase.display} phase." if data.last_phase is not None else ""
    return Message(
        f"Stopped by user after {_duration(data)}",
        "Stopped before any test case completed",
        detail,
        "re-run the command below to get results.",
    )


def _skip_reasons(data: ReportData) -> str:
    # The skip reasons say why, as the terminal does.
    reasons = data.summary.operations.skip_reasons if data.summary.operations is not None else []
    return ", ".join(reasons) or "No operations were tested"


def _filters(filters: list[str]) -> str:
    if not filters:
        return ""
    label = "Filter" if len(filters) == 1 else "Filters"
    return f"{label}: " + " ".join(f"<code>{esc(value)}</code>" for value in filters)


def _empty_message(data: ReportData) -> Message:
    subtitle = f"Finished in {_duration(data)}"
    nothing = data.nothing_tested
    if nothing is None:
        return Message(subtitle, _skip_reasons(data))
    return Message(subtitle, nothing.title, _filters(nothing.filters), nothing.tip)


def _unchecked_message(data: ReportData) -> Message:
    cases = data.summary.test_cases.without_checks
    return Message(
        f"Finished in {_duration(data)}",
        f"No selected check applied to {pluralize(cases, 'response')}",
        tip=_NO_CHECKS_TIP,
    )


def _error_tip(data: ReportData) -> str | None:
    return next((error.tip for row in data.operations for error in row.errors if error.tip is not None), None)


def _error_counts(data: ReportData) -> str:
    return esc("\n".join(f"{group.title}: {group.count}" for group in data.summary.errors))


def _errored_message(data: ReportData) -> Message:
    error = data.fatal_error
    if error is not None:
        return Message("No tests ran", error.title, esc(error.message), error.tip)
    # Requests that went out and errored are still tests that ran.
    subtitle = f"Finished in {_duration(data)}" if data.summary.test_cases.generated else "No tests ran"
    return Message(subtitle, "No operation could be tested", _error_counts(data), _error_tip(data))


_MESSAGES = {
    Verdict.INTERRUPTED: _interrupted_message,
    Verdict.EMPTY: _empty_message,
    Verdict.UNCHECKED: _unchecked_message,
    Verdict.ERRORED: _errored_message,
}


def _message(data: ReportData) -> str:
    verdict = data.verdict
    message = _MESSAGES[verdict](data)
    detail_html = f'<p class="ex-detail">{message.detail}</p>' if message.detail else ""
    return (
        f'<section class="hero-strip is-message verdict-{verdict.css}{_at_risk(data)}">'
        f'<div class="hs-cell hs-verdict"><h1 class="hero-status-label">{esc(verdict.value)}</h1>'
        f'<div class="hs-sub">{esc(message.subtitle)}</div>{_caveat(data)}{_exit_note(data)}</div>'
        f'<div class="hs-cell hs-explain"><div class="ex-title">{esc(message.title)}</div>{detail_html}'
        f"{_tip(message.tip)}</div>"
        "</section>"
    )


def _metric(label: str, value: str, subtitle: str = "", *, extra_class: str = "") -> str:
    subtitle_html = f'<span class="m-sub">{esc(subtitle)}</span>' if subtitle else ""
    return (
        f'<div class="hs-cell hs-metric{extra_class}"><span class="m-label">{esc(label)}</span>'
        f'<span class="m-value">{esc(value)}</span>{subtitle_html}</div>'
    )


_TOP_FAILURES_SHOWN = 3


def _top_failure_name(title: str, kinds: dict[str, Kind]) -> str:
    kind = kinds[title]
    return f'<a class="tf-name" href="#kind={kind.slug}" title="{esc(title)}">{esc(title)}</a>'


def _second_cell(data: ReportData) -> str:
    if data.top_failures:
        # A "+1 more" line takes the space the hidden item would, so show that item instead.
        shown = (
            data.top_failures
            if len(data.top_failures) <= _TOP_FAILURES_SHOWN + 1
            else data.top_failures[:_TOP_FAILURES_SHOWN]
        )
        kinds = _failure_kinds(data)
        rows = "".join(
            f'<li><span class="tf-n">{group.count}</span>{_top_failure_name(group.title, kinds)}</li>'
            for group in shown
        )
        hidden = len(data.top_failures) - len(shown)
        if hidden > 0:
            rows += f'<li><a class="tf-more" href="#operations">+{hidden} more</a></li>'
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
        text = f"{failed} of {pluralize(tested, 'tested operation')} failed"
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
        # Tested operations keep their errors in their rows.
        errored = sum(bool(row.errors) or row.status is OperationStatus.ERRORED for row in data.operations)
        return "", f"{pluralize(errored, 'operation')} errored" if errored else "Errors not tied to an operation"
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
    cases_subtitle = f"across {pluralize(phases, 'phase')}" if phases else ""
    # The same buckets as the operations table groups, so the counts agree.
    buckets = Counter(row.status for row in data.operations)
    operations_parts = [f"{buckets[status]} {status.value.lower()}" for status in OperationStatus if buckets[status]]
    if data.not_run_operations:
        operations_parts.append(f"{data.not_run_operations} not run")
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


def _copy_button(name: str) -> str:
    return f'<button type="button" class="copy-btn" aria-label="Copy {name}">Copy</button>'


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
        rows.append(_row("Base URL", _link(meta.base_url), _copy_button("base URL")))
    if meta.location:
        # A local spec cannot be opened from a copied-around report, so it is plain text.
        value = _link(meta.location) if _is_url(meta.location) else esc(meta.location)
        rows.append(_row("Spec", value, _copy_button("spec")))
    if meta.command != UNKNOWN_ENTRYPOINT:
        rows.append(_row("Command", _command(meta.command), _copy_button("command")))
    if meta.seed is not None:
        rows.append(_row("Seed", f"<code>{meta.seed}</code>", _copy_button("seed")))
    if data.phases:
        phases = "".join(
            f'<li class="phase phase-{kind}"><span class="ph-name">{esc(name)}</span>'
            f'<span class="ph-outcome">{esc(outcome)}</span></li>'
            for name, kind, outcome in data.phases
        )
        rows.append(_row("Phases", f'<ol class="phases">{phases}</ol>'))
    if data.checks:
        rows.append(_row("Checks", _checks(data.checks)))
    if data.reports:
        links = ", ".join(
            f'<a href="{esc(href)}" target="_blank" rel="noopener">{esc(name)}</a>' for name, href in data.reports
        )
        rows.append(_row("Reports", links))
    rows.extend(_note_rows(data))
    return f'<section class="target-block">{"".join(rows)}</section>'


def _curve(markers: list[Marker], total: float) -> str:
    path = curve_path(markers, total)
    return (
        '<svg class="tl-svg" viewBox="0 0 1000 100" preserveAspectRatio="none" aria-hidden="true" focusable="false">'
        f'<path class="tl-area" d="{path}V100Z"/><path class="tl-line" d="{path}"/></svg>'
    )


def _quiet_tail(markers: list[Marker], total: float) -> str:
    quiet = quiet_stretch(markers, total)
    if quiet is None:
        return ""
    return (
        f'<div class="tl-quiet" style="left:{position(total - quiet, total)}"><span>'
        '<span class="tl-q-long">No new failures for</span><span class="tl-q-short">Quiet</span> '
        f"{esc(short_duration(quiet))}</span></div>"
    )


def _tick(group: list[Marker], total: float) -> str:
    lines = tick_lines(group)
    count = failure_count(group)
    badge = f'<span class="tl-count" aria-hidden="true">{count}</span>' if count > 1 else ""
    at = group[0].found_at
    return (
        f'<a class="tl-mark{mark_classes(at, total)}" href="#{esc(group[0].href)}" style="left:{position(at, total)}">'
        f'{badge}<span class="tl-vh">{"<br>".join(esc(line) for line in lines)}</span></a>'
    )


def _lane_blocks(lane: Lane, total: float, *, stopped: bool) -> str:
    blocks = []
    for index, (start, end) in enumerate(lane.passes, 1):
        classes = "tl-span tl-span-stopped" if stopped and index == len(lane.passes) else "tl-span"
        blocks.append(
            f'<span class="{classes}" style="left:{position(start, total)};width:{position(end - start, total)}"></span>'
        )
    return "".join(blocks)


def _lane_row(lane: Lane, total: float, *, ticks: list[list[Marker]], stopped: bool) -> str:
    marks = "".join(_tick(group, total) for group in ticks)
    return (
        f'<div class="tl-row"><div class="tl-label"><span class="tl-lane-name">{esc(lane.phase)}</span>'
        f'<span class="tl-sub">{esc(lane.details())}</span></div>'
        f'<div class="tl-track tl-lane">{_lane_blocks(lane, total, stopped=stopped)}{marks}</div></div>'
    )


def _axis_row(total: float, *, stopped: bool) -> str:
    axis = "".join(
        f'<span class="tl-tick" style="left:{position(tick, total)}">{esc(axis_label(tick))}</span>'
        for tick in axis_ticks(total)
    )
    axis += f'<span class="tl-tick tl-end">{esc(axis_end(total, stopped=stopped))}</span>'
    return f'<div class="tl-row tl-axis-row"><div class="tl-label"></div><div class="tl-axis">{axis}</div></div>'


def _found_row(markers: list[Marker], total: float) -> str:
    if not markers:
        return ""
    return (
        '<div class="tl-row tl-found"><div class="tl-label"><span class="tl-name">Failures found</span>'
        f'<span class="tl-sub">{failure_count(markers)} total</span></div>'
        f'<div class="tl-track">{_curve(markers, total)}{_quiet_tail(markers, total)}</div></div>'
    )


def _chart(lanes: list[Lane], markers: list[Marker], total: float, *, stopped: bool) -> str:
    rows = [_found_row(markers, total)]
    cut = last_phase(lanes) if stopped else None
    rows.extend(
        _lane_row(lane, total, ticks=ticks, stopped=lane.phase == cut)
        for lane, ticks in zip(lanes, lane_ticks(lanes, total), strict=True)
    )
    rows.append(_axis_row(total, stopped=stopped))
    return f'<div class="tl-rows">{"".join(rows)}</div>'


def _timeline_section(body: str) -> str:
    return (
        f'<section class="timeline" aria-labelledby="tl-heading">'
        f'<div class="tl-head"><h2 class="tl-heading" id="tl-heading">Timeline</h2></div>{body}</section>'
    )


def _timeline(data: ReportData, anchors: dict[str, str]) -> str:
    total = total_duration(data)
    if not total:
        return ""
    markers = collect_markers(data, anchors)
    lanes = build_lanes(data, markers)
    stopped = data.stop_reason is StopReason.INTERRUPTED
    return _timeline_section(_chart(lanes, markers, total, stopped=stopped))


# Above this many rows a failing run collapses its Passed group so failures stay in view.
_COLLAPSE_PASSED_ABOVE = 25


def _path(path: str) -> str:
    # Long paths wrap before a `/`, not inside a segment; `<wbr>` adds nothing to copied text.
    head, *segments = esc(path).split("/")
    return "<wbr>/".join([head + "/" + segments[0], *segments[1:]]) if segments else head


def _operation_cell(row: OperationRow, anchor: str, toggle: str) -> str:
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
    if row.known_failures:
        kinds = ", ".join(sorted(set(row.known_failures)))
        warned += (
            f'<a class="row-known" href="#note-baseline" title="{esc(kinds)}">'
            f"{pluralize(len(row.known_failures), 'known failure')}</a>"
        )
    return (
        f'<td class="op-cell">{toggle}<span class="op">{method}<span class="path">{_path(row.path)}</span>'
        f'<a class="permalink" href="#{anchor}" aria-label="Copy link to {esc(row.label)}">#</a>{warned}</span></td>'
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


def _status(code: int, status_line: str | None = None) -> str:
    if status_line is not None:
        return f'<span class="status s{code // 100}xx">{esc(status_line)}</span>'
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
    if len(commands) == 1:
        content = f'<pre class="cmd" tabindex="0">{esc(commands[0].curl)}</pre>'
    else:
        steps = []
        for command in commands:
            failed = '<span class="step-failed">failed here</span>' if command.failed else ""
            method = _method(command.method) if command.method is not None else ""
            label = f'<span class="step-req">{method}{esc(command.path)}{failed}</span>'
            steps.append(f'<li>{label}<pre class="cmd" tabindex="0">{esc(command.curl)}</pre></li>')
        content = f'<ol class="steps">{"".join(steps)}</ol>'
    replay_line = f'<p class="replay">or <code>{esc(replay)}</code></p>' if replay is not None else ""
    return _evidence("Reproduce", f'<div class="repro">{content}{_copy_button("reproduction")}</div>{replay_line}')


DOCS_URL = "https://schemathesis.readthedocs.io/en/stable/reference/failures/"


def _failing_case(case: FailingCase, owner: str = "", filters: str = "") -> str:
    parts = [owner]
    for failure in case.failures:
        count = len(failure.messages)
        violations = f'<span class="violations">{count} violations</span>' if count > 1 else ""
        messages = "".join(f'<pre class="msg">{esc(message)}</pre>' for message in failure.messages if message)
        code = (
            f'<a class="fcode" href="{DOCS_URL}#{failure.code.lower()}" target="_blank" rel="noopener">'
            f"{esc(failure.code)}</a>"
        )
        parts.append(
            f'<div class="finding"><h4 class="finding-title">{esc(failure.title)}{code}{violations}</h4>'
            f"{messages}</div>"
        )
    if case.status_code is not None and case.body is not None:
        parts.append(_evidence("Response", _body(case.body), _status(case.status_code, case.status_line)))
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
        meta = (
            f'<div class="case-meta"><a class="case-id permalink-id" href="#case-{esc(case.case_id)}" '
            f'title="Link to this case">{esc(case.case_id)}</a>{identity}</div>'
        )
    return f'<article class="case"{case_id}{filters}><div class="case-body">{"".join(parts)}</div>{meta}</article>'


_FRAME = re.compile(r'^\s*File "(?P<path>[^"]+)", line (?P<line>\d+)')
_FRAMEWORK_FRAME = re.compile(r"[/\\](?:src|site-packages)[/\\]schemathesis[/\\]")


def _traceback(lines: list[str]) -> str:
    frames = [match for match in map(_FRAME.match, lines) if match is not None]
    # The last frame outside Schemathesis, or the last frame when every one is inside it.
    where = ([frame for frame in frames if not _FRAMEWORK_FRAME.search(frame["path"])] or frames)[-1]
    name = re.split(r"[/\\]", where["path"])[-1]
    summary = (
        f"Traceback ({pluralize(len(frames), 'frame')}), raised in "
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


def _row_items(row: OperationRow, kinds: dict[str, Kind]) -> list[str]:
    items = [_failing_case(case, filters=_case_filters(case, kinds)) for case in row.failing_cases]
    # A lone error on an errored row repeats the title the row already shows.
    with_title = bool(row.failing_cases) or len(row.errors) > 1
    items.extend(_error(error, with_title=with_title) for error in row.errors)
    return items


_DIGITS = re.compile(r"(\d+)")


def _natural_key(text: str) -> tuple[str | int, ...]:
    # Splitting on a capture group puts digit runs at odd positions, so `/4` sorts before `/12`.
    return tuple(int(part) if index % 2 else part for index, part in enumerate(_DIGITS.split(text)))


def _operation_order(row: OperationRow) -> tuple[Severity, int, tuple[str | int, ...], str]:
    return (row.severity, -len(row.failures), _natural_key(row.path), row.method)


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


class Kind(NamedTuple):
    # The option value in the kind select and the index cards list in `data-k`.
    value: int
    slug: str
    title: str
    failures: int


_METHOD_ORDER = ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS", "TRACE")


def _failure_kinds(data: ReportData) -> dict[str, Kind]:
    counts: Counter[str] = Counter()
    for group in data.top_failures:
        counts[group.title] += group.count
    kinds: dict[str, Kind] = {}
    taken: set[str] = set()
    for title, count in counts.items():
        slug = base = _slug(title) or "kind"
        suffix = 2
        while slug in taken:
            slug, suffix = f"{base}-{suffix}", suffix + 1
        taken.add(slug)
        kinds[title] = Kind(len(kinds), slug, title, count)
    return kinds


def _case_filters(case: FailingCase, kinds: dict[str, Kind]) -> str:
    indexes = dict.fromkeys(str(kinds[failure.title].value) for failure in case.failures if failure.title in kinds)
    return f' data-k="{" ".join(indexes)}"'


def _filter_bar(data: ReportData, kinds: dict[str, Kind]) -> str:
    controls = [
        '<label class="fb-search"><span class="vh">Filter by method or path</span>'
        '<input type="search" class="fb-input" id="fb-q" placeholder="Filter by path" autocomplete="off" '
        'spellcheck="false"><kbd class="fb-key" aria-hidden="true">/</kbd></label>'
    ]
    if kinds:
        options = "".join(
            f'<option value="{kind.value}" data-slug="{kind.slug}">{esc(kind.title)} ({kind.failures:,})</option>'
            for kind in kinds.values()
        )
        controls.append(
            '<label><span class="vh">Failure kind</span><select class="fb-select" id="fb-k">'
            f'<option value="">All failure kinds</option>{options}</select></label>'
        )
    present = {row.method for row in data.operations if row.method}
    methods = sorted(
        present, key=lambda method: (_METHOD_ORDER.index(method) if method in _METHOD_ORDER else 99, method)
    )
    if methods:
        toggles = "".join(
            f'<button type="button" class="fb-btn {esc(method.lower())}" data-m="{esc(method.lower())}" '
            f'aria-pressed="false">{esc(method)}</button>'
            for method in methods
        )
        controls.append(f'<div class="fb-set fb-methods" id="fb-m" role="group" aria-label="Method">{toggles}</div>')
    controls.append(
        '<p class="fb-status"><span class="fb-count" id="fb-count" role="status">'
        f"{len(data.operations)} operations</span>"
        '<button type="button" class="fb-clear" id="fb-clear" hidden>Clear filters</button></p>'
    )
    return (
        '<div class="filter-bar js-only" id="filters" role="search" aria-label="Filter operations">'
        f"{''.join(controls)}</div>"
    )


_EMPTY_RESULT = (
    '<tbody class="fb-empty js-only" id="fb-empty" hidden><tr><td colspan="3">'
    'No operations match these filters. <button type="button">Clear filters</button></td></tr></tbody>'
)


def _operations_table(data: ReportData, anchors: dict[str, str]) -> str:
    if not data.operations:
        return ""
    kinds = _failure_kinds(data)
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
                    f"{_operation_cell(row, anchor, toggle)}{_failures_cell(row)}{_cases_cell(row, _CHEVRON)}</tr>"
                    f"{_detail_row(_row_items(row, kinds), anchor, status.css)}"
                )
            else:
                body += (
                    f'<tr class="op-row row-{status.css}" id="{anchor}">'
                    f"{_operation_cell(row, anchor, '')}{_failures_cell(row)}{_cases_cell(row, '')}</tr>"
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
        cases = [
            _failing_case(case, owner=_case_owner(owner), filters=_case_filters(case, kinds))
            for owner, case in data.unattributed_cases
        ]
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
            f"{esc(pluralize(data.not_run_operations, 'operation'))} not run: {esc(reason)}</td></tr>"
        )
    footer = f"<tfoot>{''.join(footer_rows)}</tfoot>" if footer_rows else ""
    return (
        '<section class="ops" id="operations">'
        f"{_filter_bar(data, kinds)}"
        '<table class="ops-table" aria-label="Operations">'
        '<thead><tr><th scope="col">Operation</th><th scope="col">Failures</th>'
        '<th scope="col" class="numeric">Cases</th></tr></thead>'
        f"{''.join(groups)}{_EMPTY_RESULT}{footer}</table></section>"
    )
