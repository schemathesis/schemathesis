from __future__ import annotations

from collections.abc import Iterable
from html import escape
from typing import TYPE_CHECKING

from schemathesis.core.output import escape_surrogates
from schemathesis.core.version import SCHEMATHESIS_VERSION

if TYPE_CHECKING:
    from schemathesis.cli.commands.run.warnings import WarningData
    from schemathesis.reporting.html.model import ErrorEntry

CHECK_DOCS_URL = "https://schemathesis.readthedocs.io/en/stable/reference/checks/#"


def esc(value: object) -> str:
    return escape(escape_surrogates(str(value)), quote=True)


def humanize_duration(seconds: float) -> str:
    # Round to the displayed precision first so e.g. 59.96 (-> "60.0s") crosses into "1m 0s".
    rounded = round(seconds, 1)
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
  <main class="container">
{body}
  </main>
  <script src="{esc(asset_prefix)}assets/app.js"></script>
</body>
</html>
"""


def report_top(*, generated_at: str, back_href: str | None = None) -> str:
    brand = (
        '<span class="brand-mark"><svg width="26" height="26"><use href="#icon-logo"/></svg></span>'
        '<span class="brand-name">Schemathesis</span>'
        f'<span class="brand-ver">v{esc(SCHEMATHESIS_VERSION)}</span>'
    )
    if back_href is not None:
        brand = f'<a href="{esc(back_href)}" class="brand-link" title="back to report index">{brand}</a>'
    return (
        '<div class="report-top">'
        f'<div class="brand">{brand}</div>'
        '<div class="top-meta"><span class="tm-eyebrow">Generated at</span> '
        f"<span><b>{esc(generated_at)}</b></span></div>"
        "</div>"
    )


def method_span(method: str) -> str:
    return f'<span class="method {esc(method.lower())}">{esc(method.upper())}</span>'


def path_span(path: str) -> str:
    return f'<span class="path">{esc(path)}</span>'


def label_html(label: str) -> str:
    """Render an operation label as a method badge + path, or just the path when it has no method (GraphQL)."""
    method, separator, rest = label.partition(" ")
    if separator:
        return f"{method_span(method)}{path_span(rest)}"
    return path_span(label)


def section_eyebrow(title: str, count: int | None) -> str:
    number = f' <span class="num">{count}</span>' if count is not None else ""
    return f'<div class="section-eyebrow"><span>{esc(title)}</span>{number}<span class="rule"></span></div>'


def _plural(count: int, word: str) -> str:
    return f"{count} {word}" if count == 1 else f"{count} {word}s"


def _operation_items(labels: Iterable[str]) -> str:
    items = [f'<li><span class="mg-detail">{label_html(label)}</span></li>' for label in sorted(labels)]
    return f'<ul class="mg-list">{"".join(items)}</ul>'


def _message_items(messages: Iterable[str]) -> str:
    items = "".join(
        f'<li><span class="mg-detail"><code class="mg-code">{esc(message)}</code></span></li>'
        for message in sorted(messages)
    )
    return f'<ul class="mg-list">{items}</ul>'


# `description`, `items`, and `tip` carry pre-escaped HTML; dynamic values inside them must be esc()-ed by the caller.
def _card(*, title: str, count: str, description: str, items: str = "", tip: str | None = None) -> str:
    tip_html = f'<p class="mg-tip"><span class="tip-label">Tip:</span> {tip}</p>' if tip else ""
    return (
        '<article class="mg op-mg">'
        f'<header class="mg-head"><span class="mg-title">{esc(title)}</span>'
        f'<span class="mg-count">{esc(count)}</span><span class="mg-rule"></span></header>'
        f'<p class="mg-desc">{description}</p>{items}{tip_html}'
        "</article>"
    )


def filter_warnings_for_label(data: WarningData, label: str) -> WarningData:
    """`WarningData` entries that mention a specific operation label.

    `unused_openapi_auth` reports a schema-level scheme mismatch, not tied to any operation,
    so it is always dropped here.
    """
    from schemathesis.cli.commands.run.warnings import WarningData

    return WarningData(
        missing_auth={status_code: {label} for status_code, labels in data.missing_auth.items() if label in labels},
        missing_test_data={label} if label in data.missing_test_data else set(),
        validation_mismatch={label} if label in data.validation_mismatch else set(),
        missing_deserializer={label: data.missing_deserializer[label]} if label in data.missing_deserializer else {},
        unsupported_regex={label: data.unsupported_regex[label]} if label in data.unsupported_regex else {},
        method_not_allowed={label} if label in data.method_not_allowed else set(),
    )


def warning_cards(data: WarningData) -> str:
    cards = []
    for status_code, labels in sorted(data.missing_auth.items()):
        cards.append(
            _card(
                title="Missing authentication",
                count=_plural(len(labels), "operation"),
                description=f"Operations repeatedly responded with <code>{status_code}</code> - tests likely never reached the core logic.",
                items=_operation_items(labels),
                tip="Provide credentials via <code>--auth</code> or <code>--header</code>.",
            )
        )
    if data.missing_test_data:
        cards.append(
            _card(
                title="Missing test data",
                count=_plural(len(data.missing_test_data), "operation"),
                description="All positive test cases were rejected with 4xx responses, keeping tests from reaching core logic.",
                items=_operation_items(data.missing_test_data),
                tip="Provide realistic parameter values in your config so tests can reach existing resources.",
            )
        )
    if data.validation_mismatch:
        cards.append(
            _card(
                title="Schema validation mismatch",
                count=_plural(len(data.validation_mismatch), "operation"),
                description="Schema-valid test cases were consistently rejected - the schema likely does not match the implementation.",
                items=_operation_items(data.validation_mismatch),
                tip="Compare the schema constraints with the server-side validation rules.",
            )
        )
    for label, messages in sorted(data.missing_deserializer.items()):
        cards.append(
            _card(
                title="Schema validation skipped",
                count=_plural(len(messages), "occurrence"),
                description=f"Responses of {label_html(label)} could not be validated due to a missing deserializer.",
                items=_message_items(messages),
                tip="Register a deserializer with <code>@schemathesis.deserializer()</code> to enable validation.",
            )
        )
    if data.unused_openapi_auth:
        cards.append(
            _card(
                title="Unused OpenAPI auth",
                count=_plural(len(data.unused_openapi_auth), "scheme"),
                description="Configured authentication does not match any security scheme in the schema.",
                items=_message_items(data.unused_openapi_auth),
            )
        )
    for label, messages in sorted(data.unsupported_regex.items()):
        cards.append(
            _card(
                title="Unsupported regular expression",
                count=_plural(len(messages), "pattern"),
                description=f"Some patterns of {label_html(label)} could not be used for data generation.",
                items=_message_items(messages),
            )
        )
    if data.method_not_allowed:
        cards.append(
            _card(
                title="Method not allowed",
                count=_plural(len(data.method_not_allowed), "operation"),
                description="Operations consistently responded with <code>405</code> and were skipped.",
                items=_operation_items(data.method_not_allowed),
            )
        )
    return "".join(cards)


def warnings_section(warnings: WarningData) -> str:
    cards = warning_cards(warnings)
    if not cards:
        return ""
    return (
        '<section class="section warnings-section" aria-label="Warnings">'
        f"{section_eyebrow('Warnings', warnings.kind_count)}{cards}</section>"
    )


def errors_section(errors: list[ErrorEntry]) -> str:
    if not errors:
        return ""
    cards = []
    for error in errors:
        phase = f"{esc(error.phase)} phase" if error.phase else "run"
        traceback_html = ""
        if error.traceback:
            traceback_html = (
                '<details class="err-trace"><summary class="err-trace-summary">Show traceback</summary>'
                f'<pre class="err-trace-body">{esc(error.traceback)}</pre></details>'
            )
        cards.append(
            '<article class="mg op-mg mg-error">'
            f'<header class="mg-head"><span class="mg-title">{esc(error.title)}</span>'
            f'<span class="mg-count">{phase}</span><span class="mg-rule"></span></header>'
            f'<p class="mg-desc">{esc(error.message)}</p>{traceback_html}'
            "</article>"
        )
    return (
        f'<section class="section errors-section" aria-label="Errors">{section_eyebrow("Errors", len(errors))}'
        f"{''.join(cards)}</section>"
    )


# `status_line` carries pre-escaped HTML; dynamic values inside it must be esc()-ed by the caller.
def code_block(*, title: str, body: str, copy_id: str | None = None, status_line: str | None = None) -> str:
    right = ""
    if copy_id is not None:
        right = (
            '<span class="right">'
            f'<button type="button" class="code-head-copy" data-copy-target="#{esc(copy_id)}" '
            f'aria-label="Copy {esc(title.lower())}" title="Copy {esc(title.lower())}">'
            '<svg width="12" height="12" aria-hidden="true"><use href="#icon-copy"/></svg>'
            "</button></span>"
        )
    elif status_line is not None:
        right = f'<span class="right">{status_line}</span>'
    identifier = f' id="{esc(copy_id)}"' if copy_id is not None else ""
    return (
        '<div class="code">'
        f'<div class="code-head"><span>{esc(title)}</span>{right}</div>'
        f'<pre class="code-body"{identifier}>{esc(body)}</pre>'
        "</div>"
    )
