from __future__ import annotations

from collections.abc import Iterable
from html import escape
from typing import TYPE_CHECKING

from schemathesis.core.output import escape_surrogates
from schemathesis.core.version import SCHEMATHESIS_VERSION

if TYPE_CHECKING:
    from schemathesis.cli.summary import WarningData
    from schemathesis.reporting.html.model import ErrorEntry

_ICON_SPRITE = """<svg width="0" height="0" style="position:absolute" aria-hidden="true">
  <defs>
    <symbol id="icon-search" viewBox="0 0 16 16">
      <circle cx="7" cy="7" r="5" fill="none" stroke="currentColor" stroke-width="1.5"/>
      <path d="M11 11l3.5 3.5" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>
    </symbol>
    <symbol id="icon-chev-right" viewBox="0 0 16 16">
      <path d="M6 3l5 5-5 5" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/>
    </symbol>
    <symbol id="icon-arrow-left" viewBox="0 0 16 16">
      <path d="M13 8H3M7 4L3 8l4 4" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/>
    </symbol>
    <symbol id="icon-copy" viewBox="0 0 16 16">
      <rect x="5" y="5" width="9" height="9" rx="1.5" fill="none" stroke="currentColor" stroke-width="1.4"/>
      <path d="M11 5V3.5A1.5 1.5 0 0 0 9.5 2H3.5A1.5 1.5 0 0 0 2 3.5v6A1.5 1.5 0 0 0 3.5 11H5" fill="none" stroke="currentColor" stroke-width="1.4"/>
    </symbol>
    <symbol id="icon-check" viewBox="0 0 16 16">
      <path d="M3 8.5l3.5 3.5L13 5" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
    </symbol>
    <symbol id="icon-warning" viewBox="0 0 16 16">
      <path d="M8 2L1.5 13.5h13L8 2z" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linejoin="round"/>
      <path d="M8 6.5v3M8 11.5v.01" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/>
    </symbol>
    <symbol id="icon-logo" viewBox="0 0 24 24">
      <rect x="2" y="4.5" width="20" height="3" rx="1.5" fill="#51cd78"/>
      <rect x="2" y="10.5" width="14" height="3" rx="1.5" fill="#f5b74c"/>
      <rect x="2" y="16.5" width="9" height="3" rx="1.5" fill="#f76f61"/>
    </symbol>
    <symbol id="icon-terminal" viewBox="0 0 16 16">
      <rect x="1.5" y="3" width="13" height="10" rx="1.5" fill="none" stroke="currentColor" stroke-width="1.4"/>
      <path d="M4.5 6.5L7 8l-2.5 1.5M8 10h4" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"/>
    </symbol>
    <symbol id="icon-info" viewBox="0 0 16 16">
      <circle cx="8" cy="8" r="6.5" fill="none" stroke="currentColor" stroke-width="1.4"/>
      <path d="M8 7v4" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/>
      <circle cx="8" cy="4.8" r="0.9" fill="currentColor"/>
    </symbol>
  </defs>
</svg>"""


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


def page(*, title: str, body: str, asset_prefix: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{esc(title)}</title>
  <link rel="stylesheet" href="{esc(asset_prefix)}assets/report.css">
</head>
<body>
{_ICON_SPRITE}
  <main class="container">
{body}
  </main>
</body>
</html>
"""


def report_top(*, generated_at: str) -> str:
    brand = (
        '<span class="brand-mark"><svg width="26" height="26"><use href="#icon-logo"/></svg></span>'
        '<span class="brand-name">Schemathesis</span>'
        f'<span class="brand-ver">v{esc(SCHEMATHESIS_VERSION)}</span>'
    )
    return (
        '<header class="report-top">'
        f'<div class="brand">{brand}</div>'
        '<div class="top-meta"><span class="tm-eyebrow">Generated at</span> '
        f"<span><b>{esc(generated_at)}</b></span></div>"
        "</header>"
    )


def method_span(method: str) -> str:
    return f'<span class="method {esc(method.lower())}">{esc(method.upper())}</span>'


def path_span(path: str) -> str:
    return f'<span class="path">{esc(path)}</span>'


_HTTP_METHODS = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS", "TRACE"})


def label_html(label: str) -> str:
    method, separator, rest = label.partition(" ")
    if separator and method.upper() in _HTTP_METHODS:
        return f"{method_span(method)}{path_span(rest)}"
    return path_span(label)


def section_eyebrow(title: str, count: int | None) -> str:
    number = f' <span class="num">{count}</span>' if count is not None else ""
    return f'<h2 class="section-eyebrow"><span>{esc(title)}</span>{number}<span class="rule"></span></h2>'


def plural(count: int, word: str) -> str:
    return f"{count} {word}" if count == 1 else f"{count} {word}s"


def operation_items(labels: Iterable[str]) -> str:
    items = [f'<li><span class="mg-detail">{label_html(label)}</span></li>' for label in sorted(labels)]
    return f'<ul class="mg-list">{"".join(items)}</ul>'


def _message_items(messages: Iterable[str]) -> str:
    items = "".join(
        f'<li><span class="mg-detail"><code class="mg-code">{esc(message)}</code></span></li>'
        for message in sorted(messages)
    )
    return f'<ul class="mg-list">{items}</ul>'


# `description`, `items`, and `tip` carry pre-escaped HTML; dynamic values inside them must be esc()-ed by the caller.
def card(
    *, title: str, count: str, description: str, items: str = "", tip: str | None = None, variant: str = ""
) -> str:
    tip_html = f'<p class="mg-tip"><span class="tip-label">Tip:</span> {tip}</p>' if tip else ""
    classes = "mg op-mg" + (f" {variant}" if variant else "")
    return (
        f'<article class="{classes}">'
        f'<header class="mg-head"><span class="mg-title">{esc(title)}</span>'
        f'<span class="mg-count">{esc(count)}</span><span class="mg-rule"></span></header>'
        f'<p class="mg-desc">{description}</p>{items}{tip_html}'
        "</article>"
    )


def warning_cards(data: WarningData) -> list[str]:
    cards = []
    for status_code, labels in sorted(data.missing_auth.items()):
        status_text = "Unauthorized" if status_code == 401 else "Forbidden"
        cards.append(
            card(
                title="Authentication failed",
                count=plural(len(labels), "operation"),
                description=f"Operations returned <code>{status_code} {status_text}</code> - tests likely never reached the core logic.",
                items=operation_items(labels),
                tip="Ensure valid authentication credentials are set via <code>--auth</code> or <code>-H</code>.",
            )
        )
    if data.base_url_mismatch:
        suggestion = f" Try <code>--url {esc(data.base_url_suggestion)}</code>." if data.base_url_suggestion else ""
        cards.append(
            card(
                title="Base URL may be missing a path",
                count=plural(len(data.base_url_mismatch), "operation"),
                description="Operations returned only <code>404 Not Found</code>.",
                items=operation_items(data.base_url_mismatch),
                tip=f"The schema declares a base path.{suggestion}",
            )
        )
    if data.missing_test_data:
        cards.append(
            card(
                title="Missing test data",
                count=plural(len(data.missing_test_data), "operation"),
                description="Operations repeatedly returned <code>404 Not Found</code>, preventing tests from reaching your API's core logic.",
                items=operation_items(data.missing_test_data),
                tip="Provide realistic parameter values in your config file so tests can access existing resources.",
            )
        )
    if data.validation_mismatch:
        cards.append(
            card(
                title="Schema validation mismatch",
                count=plural(len(data.validation_mismatch), "operation"),
                description="Operations mostly rejected generated data due to validation errors, indicating schema constraints don't match API validation.",
                items=operation_items(data.validation_mismatch),
                tip="Check your schema constraints - API validation may be stricter than documented.",
            )
        )
    for media_type, operations in sorted(data.missing_deserializer.items()):
        cards.append(
            card(
                title="Schema validation skipped",
                count=plural(len(operations), "operation"),
                description=f"Responses with <code>{esc(media_type)}</code> cannot be validated due to a missing deserializer.",
                # Same detail the terminal prints: `GET /users (200, 404)`.
                items=_message_items(
                    f"{label} ({', '.join(sorted(details))})" for label, details in sorted(operations.items())
                ),
                tip="Register a deserializer with <code>@schemathesis.deserializer()</code> to enable validation.",
            )
        )
    if data.unused_openapi_auth:
        cards.append(
            card(
                title="Unused OpenAPI auth",
                count=plural(len(data.unused_openapi_auth), "configured auth scheme"),
                description="Configured auth schemes are not defined in the schema.",
                items=_message_items(data.unused_openapi_auth),
            )
        )
    if data.unmatched_filter:
        cards.append(
            card(
                title="Unmatched filters",
                count=plural(len(data.unmatched_filter), "filter"),
                description="Filters matched no API operations.",
                items=_message_items(data.unmatched_filter),
                tip="Check the filter for a typo, or update it if the operation was renamed.",
            )
        )
    if data.method_not_allowed:
        cards.append(
            card(
                title="Method Not Allowed",
                count=plural(len(data.method_not_allowed), "operation"),
                description="Operations consistently returned <code>405 Method Not Allowed</code> and were skipped from later phases.",
                items=operation_items(data.method_not_allowed),
                tip="Verify the server actually accepts these methods, or remove them from the schema if unsupported.",
            )
        )
    for label, messages in sorted(data.unsupported_regex.items()):
        cards.append(
            card(
                title="Unsupported regex patterns",
                count=plural(len(messages), "pattern"),
                description=f"{label_html(label)} contains regex patterns no value can be generated for.",
                items=_message_items(messages),
                tip="Supply examples for this operation, or narrow the pattern.",
            )
        )
    for label, messages in sorted(data.unresolvable_reference.items()):
        cards.append(
            card(
                title="Unresolvable references",
                count=plural(len(messages), "reference"),
                description=f"{label_html(label)} skipped parts of the schema.",
                items=_message_items(messages),
                tip="Resolve these references so the skipped parts get tested.",
            )
        )
    if data.constants_extraction:
        cards.append(
            card(
                title="Constant reuse skipped",
                count=plural(len(data.constants_extraction), "registered source"),
                description="Sources could not be scanned for constant reuse.",
                items=_message_items(data.constants_extraction),
                tip="Check that each <code>@schemathesis.python.constants</code> source returns your app or modules.",
            )
        )
    return cards


def warnings_section(warnings: WarningData) -> str:
    cards = warning_cards(warnings)
    if not cards:
        return ""
    # One warning kind can produce several cards, so the eyebrow counts cards rather than kinds.
    return (
        '<section class="section warnings-section">'
        f"{section_eyebrow('Warnings', len(cards))}"
        '<p class="section-note">Warnings do not fail the run unless <code>warnings.fail-on</code> is configured.</p>'
        f"{''.join(cards)}</section>"
    )


def errors_section(errors: list[ErrorEntry], fatal: ErrorEntry | None) -> str:
    entries = ([fatal] if fatal is not None else []) + errors
    if not entries:
        return ""
    cards = "".join(_error_card(entry) for entry in entries)
    return f'<section class="section errors-section">{section_eyebrow("Errors", len(entries))}{cards}</section>'


def _error_card(entry: ErrorEntry) -> str:
    # `card` escapes the whole `count` string, so `scope` must stay unescaped here.
    scope = f"{entry.phase} phase" if entry.phase else "run"
    count = f"{scope} · {plural(entry.count, 'occurrence')}" if entry.count > 1 else scope
    items = operation_items(entry.operations) if entry.operations else ""
    if entry.traceback:
        items += (
            '<details class="err-trace"><summary class="err-trace-summary">Show traceback</summary>'
            f'<pre class="err-trace-body">{esc(entry.traceback)}</pre></details>'
        )
    return card(title=entry.title, count=count, description=esc(entry.message), items=items, variant="mg-error")
