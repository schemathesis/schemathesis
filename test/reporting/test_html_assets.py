import re
from importlib.resources import files

from schemathesis.engine import StopReason
from schemathesis.reporting.html.render import render_index
from test.reporting.factories import (
    build_report,
    build_summary,
    empty_report,
    error_entry,
    errored_report,
    failing_report,
    fatal_report,
    interrupted_report,
    never_finished_report,
    passing_report,
    stateful_only_report,
    stateful_report,
    warnings_report,
)

CLASS_SELECTOR = re.compile(r"\.([A-Za-z_][\w-]*)")


def stylesheet():
    return (files("schemathesis.reporting.html") / "assets" / "report.css").read_text(encoding="utf-8")


def declared_classes(css):
    without_comments = re.sub(r"/\*.*?\*/", "", css, flags=re.DOTALL)
    # Drop declaration blocks and attribute selectors so `0.5rem` or `[href$=".html"]` never read as classes.
    selectors_only = re.sub(r"\[[^\]]*\]", "", re.sub(r"\{[^{}]*\}", "", without_comments))
    return {match.group(1) for match in CLASS_SELECTOR.finditer(selectors_only)}


def stopped_early_report():
    return build_report(stop_reason=StopReason.MAX_TIME)


def head_operation_report():
    errors = [error_entry("Timeout", operations=["HEAD /ping"])]
    return build_report(build_summary(errors=errors), errors=errors, exit_code=1)


def test_every_css_class_is_emitted_by_a_renderer():
    rendered = "".join(
        render_index(report())
        for report in (
            failing_report,
            passing_report,
            errored_report,
            fatal_report,
            empty_report,
            interrupted_report,
            never_finished_report,
            stateful_report,
            stateful_only_report,
            warnings_report,
            # Markup the shared fixtures never reach: the early-stop note and the HEAD method badge.
            stopped_early_report,
            head_operation_report,
        )
    )
    used = {name for group in re.findall(r'class="([^"]*)"', rendered) for name in group.split()}
    declared = declared_classes(stylesheet())
    assert declared <= used, sorted(declared - used)


def test_stylesheet_is_offline_and_dependency_free():
    css = stylesheet()
    assert "fonts.googleapis.com" not in css
    assert "@import" not in css
    assert "color-mix(" not in css
    assert "outline: none" not in css
    assert "--sev-" not in css
    assert len(css.splitlines()) <= 600
