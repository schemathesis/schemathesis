import pytest

from schemathesis.reporting.html.render import index
from schemathesis.reporting.html.render.components import page
from test.reporting.factories import (
    empty_report,
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


@pytest.mark.parametrize(
    "report",
    [
        failing_report(),
        passing_report(),
        errored_report(),
        fatal_report(),
        empty_report(),
        interrupted_report(),
        never_finished_report(),
        stateful_report(),
        stateful_only_report(),
    ],
    ids=["failed", "passed", "errored", "fatal", "empty", "interrupted", "never-finished", "stateful", "stateful-only"],
)
def test_hero_strip(report, snapshot_html):
    assert index._hero(report) == snapshot_html


def test_target_block(snapshot_html):
    assert index._target_block(failing_report()) == snapshot_html


def test_failures_section(snapshot_html):
    assert index._failures(failing_report()) == snapshot_html


@pytest.mark.parametrize(
    "report", [failing_report(), errored_report(), fatal_report()], ids=["one", "with-traceback", "fatal"]
)
def test_errors_section(report, snapshot_html):
    assert index._errors(report) == snapshot_html


def test_warnings_section(snapshot_html):
    assert index._warnings(warnings_report()) == snapshot_html


def test_page_shell(snapshot_html):
    assert page(title="T", body="<p>b</p>", asset_prefix="") == snapshot_html
