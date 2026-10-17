import itertools
import json
import re
import sys
import threading
import time
from html import escape, unescape
from types import SimpleNamespace

import pytest
from flask import Response, jsonify, request
from hypothesis import strategies as st

import schemathesis
from schemathesis.checks import CHECKS
from schemathesis.cli.constants import ExitCode
from schemathesis.engine import events
from schemathesis.graphql import nodes
from schemathesis.reporting.html.model import PhaseSpan
from schemathesis.reporting.html.render import humanize_duration
from schemathesis.reporting.html.timeline import (
    BADGE_PX,
    MAX_TICKS,
    TICK_GAP_PX,
    TRACK_PX,
    Lane,
    Marker,
    axis_ticks,
    curve_path,
    lane_ticks,
    mark_classes,
    phase_blocks,
    short_duration,
    tick_groups,
)
from schemathesis.specs.graphql.scalars import CUSTOM_SCALARS
from test.apps.catalog.openapi.modifiers.stateful import FailAfterRequests


@pytest.fixture
def report_dir(tmp_path):
    return tmp_path / "report"


def count_of(pattern, text):
    match = re.search(pattern, text)
    return int(match.group(1)) if match else 0


def assert_phases_match_lanes(html):
    phases = {
        name: (kind, outcome)
        for kind, name, outcome in re.findall(
            r'<li class="phase phase-(\w+)"><span class="ph-name">([^<]+)</span><span class="ph-outcome">([^<]*)</span>',
            html,
        )
    }
    if not phases:
        return
    lanes = {
        name: (details, track)
        for name, details, track in re.findall(
            r'<span class="tl-lane-name">([^<]+)</span><span class="tl-sub">([^<]*)</span></div>'
            r'<div class="tl-track tl-lane">(.*?)</div></div>',
            html,
        )
    }
    ran = {name for name, (kind, outcome) in phases.items() if kind != "skipped" or outcome == "no checks ran"}
    charted = {name for name, (_, track) in lanes.items() if 'class="tl-span' in track}
    assert ran == charted, f"Phases that ran {sorted(ran)} vs timeline lanes {sorted(charted)}"
    for name in ran:
        kind, outcome = phases[name]
        details = lanes[name][0]
        errors = count_of(r"(\d+) errors?\b", outcome)
        assert errors == count_of(r"(\d+) errors?\b", details), (name, outcome, details)
        assert outcome.split(",")[0] in ("passed", "failed", "interrupted", "no checks ran") or re.fullmatch(
            r"\d+ errors?", outcome
        ), (name, outcome)
        stopped = 'class="tl-span tl-span-stopped"' in lanes[name][1]
        assert stopped == (kind == "interrupted"), (name, outcome)
    blocks = {name: assert_lane_blocks(name, details, track, html) for name, (details, track) in lanes.items()}
    assert_lanes_take_turns(blocks)
    if '<h1 class="hero-status-label">Interrupted</h1>' in html and charted:
        assert re.search(r'<span class="tl-tick tl-end">[^<]+, stopped</span>', html)
        assert "interrupted" in [kind for kind, _ in phases.values()]


def track_seconds(html):
    return parse_duration(re.search(r'<span class="tl-tick tl-end">([^<,]+)', html).group(1))


def parse_duration(text):
    if text.endswith("ms"):
        return float(text[:-2]) / 1000
    return sum(float(part[:-1]) * {"h": 3600, "m": 60, "s": 1}[part[-1]] for part in text.split())


def display_rounding(seconds):
    for limit, error in ((1, 0.0005), (60, 0.05), (3600, 0.5)):
        if seconds < limit:
            return error
    return 30


def assert_lane_blocks(name, details, track, html):
    # Offset and width are rounded separately, so touching blocks may overlap by one rounding step.
    blocks = [
        (float(left), float(left) + float(width))
        for left, width in re.findall(
            r'<span class="tl-span[^"]*" style="left:([\d.]+)%;width:([\d.]+)%"></span>', track
        )
    ]
    for (_, previous_end), (start, _) in itertools.pairwise(blocks):
        assert previous_end <= start + 0.02, (name, blocks)
    assert all(0 <= start <= end <= 100.02 for start, end in blocks), (name, blocks)
    if blocks:
        passes = count_of(r" in (\d+) passes", details) or 1
        assert passes == len(blocks), (name, details, blocks)
        label = parse_duration(details.split(",")[0].split(" in ")[0])
        drawn = sum(end - start for start, end in blocks) / 100
        track = track_seconds(html)
        tolerance = display_rounding(label) + drawn * display_rounding(track) + 0.0001 * len(blocks) * track
        assert drawn * track == pytest.approx(label, abs=tolerance), (name, details, blocks)
    return blocks


def assert_lanes_take_turns(blocks):
    drawn = sorted((start, end, name) for name, items in blocks.items() for start, end in items)
    for (_, previous_end, previous), (start, _, current) in itertools.pairwise(drawn):
        assert previous_end <= start + 0.02, f"{previous} and {current} overlap: {drawn}"


def assert_failure_counts_match(html):
    lanes = re.findall(r'<span class="tl-lane-name">[^<]+</span><span class="tl-sub">([^<]*)</span>', html)
    found = sum(count_of(r"(\d+) found", details) for details in lanes)
    assert (
        count_of(r'<span class="tl-name">Failures found</span><span class="tl-sub">(\d+) total</span>', html) == found
    )
    ticks = re.findall(
        r'<a class="tl-mark[^"]*"[^>]*>(?:<span class="tl-count" aria-hidden="true">(\d+)</span>)?', html
    )
    if ticks:
        assert sum(int(count or 1) for count in ticks) == found
    top = re.findall(r'<span class="tf-n">(\d+)</span>', html)
    if top and 'class="tf-more"' not in html:
        assert sum(map(int, top)) == found


def assert_operation_counts_match(html):
    hero = re.search(r'Operations</span><span class="m-value">\d+</span><span class="m-sub">([^<]*)</span>', html)
    if hero is None:
        return
    buckets = {word: int(count) for count, word in re.findall(r"(\d+) ([a-z ]+)", hero.group(1))}
    groups = {
        title.lower(): int(count)
        for title, count in re.findall(
            r'<span class="group-title">(\w+)</span><span class="group-count">(\d+)</span>', html
        )
    }
    not_run = count_of(r'<tr class="not-run-row"><td colspan="3">(\d+) operations? not run', html)
    assert buckets == {**groups, **({"not run": not_run} if not_run else {})}, (buckets, groups, not_run)


def assert_top_failures_match(html):
    shown = len(re.findall(r'<span class="tf-n">\d+</span>', html))
    more = count_of(r'<a class="tf-more"[^>]*>\+(\d+) more</a>', html)
    ops = re.search(r'<section class="ops[^"]*"[^>]*>.*?</table></section>', html, flags=re.DOTALL)
    cards = re.findall(r'<article class="case"[^>]*>.*?</article>', ops.group(0) if ops else "", flags=re.DOTALL)
    kinds = {title for card in cards for title in re.findall(r'<h4 class="finding-title">([^<]+)', card)}
    assert shown + more == len(kinds), (shown, more, sorted(kinds))


def assert_report_consistent(html):
    assert_phases_match_lanes(html)
    assert_failure_counts_match(html)
    assert_operation_counts_match(html)
    assert_top_failures_match(html)
    ids = re.findall(r' id="([^"]+)"', html)
    assert len(ids) == len(set(ids)), sorted(id_ for id_ in ids if ids.count(id_) > 1)
    if "No tests ran" in html:
        assert not re.search(r'<td class="numeric(?: one)?">[\d,]+', html), "No tests ran, yet operations ran cases"


def read_report(report_dir):
    html = (report_dir / "index.html").read_text(encoding="utf-8")
    assert_report_consistent(html)
    return html


def run_with_report(cli, report_dir, *args, exit_code=ExitCode.OK, phases="fuzzing", **kwargs):
    cli.run_and_assert(
        *args,
        "--max-examples=1",
        f"--phases={phases}",
        "--seed=42",
        f"--report-html-path={report_dir}",
        exit_code=exit_code,
        **kwargs,
    )
    return read_report(report_dir)


def test_html_report_passed(ctx, cli, report_dir, snapshot_html):
    api = ctx.openapi.apps.success()
    assert run_with_report(cli, report_dir, api.schema_url) == snapshot_html
    assert list(report_dir.iterdir()) == [report_dir / "index.html"]


# The only failing operation stops at its first failing response, so every failure arrives at one moment.
def test_html_report_failed(ctx, cli, report_dir, snapshot_html):
    api = ctx.openapi.apps.failure()
    html = run_with_report(cli, report_dir, api.schema_url, exit_code=ExitCode.FAILURES)
    assert html == snapshot_html
    assert "tl-compact" not in html
    assert re.findall(r'<span class="tl-lane-name">([^<]+)</span>', html) == ["Fuzzing"]
    assert len(re.findall(r'class="tl-mark', html)) == 1
    assert '<div class="tl-head"><h2 class="tl-heading" id="tl-heading">Timeline</h2></div>' in html
    assert "title=" not in re.search(r'<section class="timeline".*?</section>', html).group(0)


def test_html_report_schema_not_loaded(ctx, cli, report_dir, snapshot_html):
    api = ctx.openapi.apps.success()
    # Serves JSON that is not an API schema.
    url = f"{api.base_url}/api/success"
    assert run_with_report(cli, report_dir, url, exit_code=ExitCode.ERROR) == snapshot_html


# A custom handler that fails on start aborts the run before the schema location is known.
def test_html_report_handler_error_before_loading(ctx, cli, report_dir, snapshot_html):
    module = ctx.write_pymodule(
        """
from schemathesis import cli


@cli.handler()
class BrokenHandler(cli.EventHandler):
    def start(self, ctx):
        raise RuntimeError("broken handler")
"""
    )
    api = ctx.openapi.apps.success()
    html = run_with_report(cli, report_dir, api.schema_url, exit_code=ExitCode.ERROR, hooks=module)
    assert html == snapshot_html


def test_html_report_no_operations_selected(ctx, cli, report_dir, snapshot_html):
    api = ctx.openapi.apps.success()
    html = run_with_report(
        cli,
        report_dir,
        api.schema_url,
        "--include-path=/unknown",
        "--include-method=PURGE",
        exit_code=ExitCode.ERROR,
    )
    assert html == snapshot_html


def test_html_report_stopped_at_failure_limit(ctx, cli, app_runner, report_dir, snapshot_html):
    app, _ = ctx.openapi.make_flask_app(
        {
            "/api/failure": {"get": {"responses": {"200": {"description": "OK"}}}},
            "/api/success": {"get": {"responses": {"200": {"description": "OK"}}}},
        }
    )

    @app.route("/api/failure")
    def failure():
        return "", 500

    @app.route("/api/success")
    def success():
        return "{}"

    html = run_with_report(
        cli, report_dir, app_runner.openapi_url(app), "--max-failures=1", exit_code=ExitCode.FAILURES
    )
    assert html == snapshot_html


def test_html_report_schema_without_operations(ctx, cli, app_runner, report_dir, snapshot_html):
    app, _ = ctx.openapi.make_flask_app({})
    assert run_with_report(cli, report_dir, app_runner.openapi_url(app), exit_code=ExitCode.ERROR) == snapshot_html


def test_html_report_schema_unreachable_after_waiting(cli, report_dir, snapshot_html):
    # The terminal drops the "wait for the schema" tip once the run already waited.
    html = run_with_report(
        cli, report_dir, "http://127.0.0.1:1/openapi.json", "--wait-for-schema=1", exit_code=ExitCode.ERROR
    )
    assert html == snapshot_html


def test_html_report_schema_loading_hook_error(ctx, cli, report_dir, snapshot_html):
    api = ctx.openapi.apps.success()
    module = ctx.write_pymodule(
        """
@schemathesis.hook
def before_load_schema(ctx, raw_schema):
    raise ValueError("Broken hook")
"""
    )
    assert run_with_report(cli, report_dir, api.schema_url, exit_code=ExitCode.ERROR, hooks=module) == snapshot_html


def test_html_report_interrupted_before_any_case(ctx, cli, report_dir, snapshot_html):
    api = ctx.openapi.apps.success()
    module = ctx.write_pymodule(
        """
@schemathesis.hook
def before_call(ctx, case, **kwargs):
    raise KeyboardInterrupt
"""
    )
    html = run_with_report(cli, report_dir, api.schema_url, exit_code=ExitCode.INTERRUPTED, hooks=module)
    assert html == snapshot_html


INTERRUPT_ON_SECOND_CALL = """
CALLS = []


@schemathesis.hook
def before_call(ctx, case, **kwargs):
    # The first operation completes; Ctrl-C lands on the second.
    CALLS.append(case)
    if len(CALLS) > 1:
        raise KeyboardInterrupt
"""


def test_html_report_interrupted_with_results(ctx, cli, report_dir, snapshot_html):
    api = ctx.openapi.apps.success_and_failure()
    module = ctx.write_pymodule(INTERRUPT_ON_SECOND_CALL)
    html = run_with_report(cli, report_dir, api.schema_url, exit_code=ExitCode.INTERRUPTED, hooks=module)
    assert html == snapshot_html


def test_html_report_failure_outside_tested_operations(ctx, cli, app_runner, report_dir, snapshot_html):
    app, _ = ctx.openapi.make_flask_app(
        {
            "/api/items": {"get": {"responses": {"200": {"description": "OK"}}}},
            "/api/users": {"get": {"responses": {"200": {"description": "OK"}}}},
        }
    )

    # Accepting a method the schema does not declare fails on that method, not on a tested operation.
    @app.route("/api/items", methods=["GET", "TRACE"])
    @app.route("/api/users", methods=["GET", "TRACE"])
    def items():
        return jsonify({})

    html = run_with_report(
        cli,
        report_dir,
        app_runner.openapi_url(app),
        "--checks=unsupported_method",
        phases="coverage",
        exit_code=ExitCode.FAILURES,
    )
    assert html == snapshot_html


INVALID_BODY = {
    "post": {
        "requestBody": {
            "required": True,
            "content": {"application/json": {"schema": {"type": "integer", "minimum": "abc"}}},
        },
        "responses": {"200": {"description": "OK"}},
    }
}


def test_html_report_every_operation_errored(ctx, cli, app_runner, report_dir, snapshot_html):
    app, _ = ctx.openapi.make_flask_app({"/api/broken": INVALID_BODY})
    assert run_with_report(cli, report_dir, app_runner.openapi_url(app), exit_code=ExitCode.FAILURES) == snapshot_html


def test_html_report_some_operations_errored(ctx, cli, app_runner, report_dir, snapshot_html):
    app, _ = ctx.openapi.make_flask_app(
        {
            "/api/broken": INVALID_BODY,
            "/api/not-loaded": {
                "get": {
                    "parameters": [{"$ref": "#/components/parameters/Missing"}],
                    "responses": {"200": {"description": "OK"}},
                }
            },
            "/api/success": {"get": {"responses": {"200": {"description": "OK"}}}},
        }
    )

    @app.route("/api/success")
    def success():
        return "{}"

    assert run_with_report(cli, report_dir, app_runner.openapi_url(app), exit_code=ExitCode.FAILURES) == snapshot_html


# Slow requests time out after other cases of the same operation passed; the invalid body errors before any case.
def test_html_report_operations_errored_after_cases(ctx, cli, app_runner, report_dir):
    parameter = {"name": "q", "in": "query", "required": True, "schema": {"type": "integer"}}
    app, _ = ctx.openapi.make_flask_app(
        {
            "/api/broken": INVALID_BODY,
            "/api/slow": {"get": {"parameters": [parameter], "responses": {"200": {"description": "OK"}}}},
            "/api/success": {"get": {"responses": {"200": {"description": "OK"}}}},
        }
    )

    release = threading.Event()

    @app.route("/api/slow")
    def slow():
        if len(request.args.get("q", "")) > 2:
            release.wait(timeout=10)
        return jsonify({})

    @app.route("/api/success")
    def success():
        return "{}"

    html = run_with_report(
        cli,
        report_dir,
        app_runner.openapi_url(app),
        "--checks=not_a_server_error",
        "--request-timeout=0.1",
        phases="coverage,fuzzing",
        exit_code=ExitCode.FAILURES,
    )
    release.set()
    groups = re.findall(r'<span class="group-title">(\w+)</span><span class="group-count">(\d+)</span>', html)
    assert groups == [("Passed", "2"), ("Errored", "1")]
    operations = re.search(r'Operations</span><span class="m-value">(\d+)</span><span class="m-sub">([^<]*)<', html)
    assert operations.groups() == ("3", "2 passed, 1 errored")
    phases = re.findall(r'<span class="ph-name">(\w+)</span><span class="ph-outcome">([^<]*)</span>', html)
    assert [name for name, _ in phases] == ["Examples", "Coverage", "Fuzzing", "Stateful"]
    assert all(re.fullmatch(r"\d+ errors?", outcome) for name, outcome in phases if name in ("Coverage", "Fuzzing"))


# The undecodable body errors only for non-integer `q`; other cases of the same operation pass.
def test_html_report_operation_tested_and_errored(ctx, cli, app_runner, report_dir, snapshot_html):
    parameter = {"name": "q", "in": "query", "required": True, "schema": {"type": "integer"}}
    app, _ = ctx.openapi.make_flask_app(
        {
            "/api/flaky": {"get": {"parameters": [parameter], "responses": {"200": {"description": "OK"}}}},
            "/api/success": {"get": {"responses": {"200": {"description": "OK"}}}},
        }
    )

    @app.route("/api/flaky")
    def flaky():
        if not request.args.get("q", "").isdigit():
            return Response(b"not gzip", headers={"Content-Encoding": "gzip"})
        return jsonify({})

    @app.route("/api/success")
    def success():
        return "{}"

    html = run_with_report(
        cli,
        report_dir,
        app_runner.openapi_url(app),
        "--checks=not_a_server_error",
        phases="coverage",
        exit_code=ExitCode.FAILURES,
    )
    assert html == snapshot_html


def test_html_report_operations_table(ctx, cli, app_runner, report_dir, snapshot_html):
    app, _ = ctx.openapi.make_flask_app(
        {
            "/api/failure": {"get": {"responses": {"200": {"description": "OK"}}}},
            "/api/success": {"get": {"responses": {"200": {"description": "OK"}}}},
            "/api/broken": INVALID_BODY,
        }
    )

    @app.route("/api/failure")
    def failure():
        return "", 500

    @app.route("/api/success")
    def success():
        return "{}"

    html = run_with_report(cli, report_dir, app_runner.openapi_url(app), exit_code=ExitCode.FAILURES)
    assert html == snapshot_html


def test_html_report_permalinks(ctx, cli, report_dir):
    api = ctx.openapi.apps.success_and_failure()
    html = run_with_report(cli, report_dir, api.schema_url, exit_code=ExitCode.FAILURES)
    assert re.findall(
        r'<tr class="op-row[^"]*" id="([^"]+)">.*?<a class="permalink" href="#([^"]+)" ([^>]*)>#</a>', html
    ) == [
        ("op-get-api-failure", "op-get-api-failure", 'aria-label="Copy link to GET /api/failure"'),
        ("op-get-api-success", "op-get-api-success", 'aria-label="Copy link to GET /api/success"'),
    ]
    cards = re.findall(
        r'<article class="case" id="case-(\w+)"[^>]*>.*?<a class="case-id permalink-id" href="#case-(\w+)"[^>]*>(\w+)</a>',
        html,
        flags=re.DOTALL,
    )
    assert cards
    assert all(article == href == text for article, href, text in cards)
    assert '<div id="nav-live" class="vh" role="status" aria-live="polite"></div>' in html


def top_failures_items(html):
    tf_list = re.search(r'<ul class="tf-list">(.*?)</ul>', html).group(1)
    return re.findall(
        r'<li>(?:<span class="tf-n">\d+</span><(?:span|a) class="tf-name"[^>]*>([^<]+)</(?:span|a)>|<a[^>]*>([^<]+)</a>)</li>',
        tf_list,
    )


@pytest.mark.parametrize(
    ("with_schema_failure", "expected"),
    [
        (
            False,
            [
                "Missing required response headers",
                "Server error",
                "Undocumented Content-Type",
                "Undocumented status code",
            ],
        ),
        (
            True,
            [
                "Missing required response headers",
                "Response violates schema",
                "Server error",
                "+2 more",
            ],
        ),
    ],
    ids=["one-hidden-shown", "two-hidden-collapsed"],
)
def test_html_report_top_failures_count_hidden_kinds(ctx, cli, app_runner, report_dir, with_schema_failure, expected):
    header = {"X-Token": {"required": True, "schema": {"type": "string"}}}
    json_body = {"application/json": {"schema": {"type": "object"}}}
    paths = {
        "/api/failure": {"get": {"responses": {"200": {"description": "OK"}}}},
        "/api/text": {"get": {"responses": {"200": {"description": "OK", "headers": header, "content": json_body}}}},
    }
    if with_schema_failure:
        paths["/api/array"] = {"get": {"responses": {"200": {"description": "OK", "content": json_body}}}}
    app, _ = ctx.openapi.make_flask_app(paths)

    @app.route("/api/failure")
    def failure():
        return "", 500

    @app.route("/api/text")
    def text():
        return Response("plain", content_type="text/plain")

    @app.route("/api/array")
    def array():
        return jsonify([])

    html = run_with_report(cli, report_dir, app_runner.openapi_url(app), exit_code=ExitCode.FAILURES)
    items = ["".join(item) for item in top_failures_items(html)]
    assert items == expected


def test_html_report_continue_on_failure(ctx, cli, report_dir, snapshot_html):
    # Failed operations keep running past their first failure, so their case counts need no explanation.
    api = ctx.openapi.apps.failure()
    html = run_with_report(cli, report_dir, api.schema_url, "--continue-on-failure", exit_code=ExitCode.FAILURES)
    assert html == snapshot_html


def test_html_report_skipped_operations(ctx, cli, app_runner, report_dir, snapshot_html):
    parameter = {"name": "q", "in": "query", "required": True, "schema": {"type": "string"}}
    app, _ = ctx.openapi.make_flask_app(
        {
            "/api/with-example": {
                "get": {"parameters": [{**parameter, "example": "pets"}], "responses": {"200": {"description": "OK"}}}
            },
            "/api/without-example": {"get": {"parameters": [parameter], "responses": {"200": {"description": "OK"}}}},
        }
    )

    @app.route("/api/with-example")
    def with_example():
        return "{}"

    assert run_with_report(cli, report_dir, app_runner.openapi_url(app), phases="examples") == snapshot_html


def test_html_report_fuzz_failures(ctx, cli, report_dir, snapshot_html):
    api = ctx.openapi.apps.success_and_failure()
    result = cli.main(
        "fuzz",
        api.schema_url,
        "--max-failures=1",
        "--max-time=5",
        "--seed=42",
        f"--report-html-path={report_dir}",
    )
    assert result.exit_code == ExitCode.FAILURES, result.stdout
    html = read_report(report_dir)
    # Fuzzing runs operations concurrently, so only the failed row is stable.
    assert '<tr class="op-row row-failed has-details" id="op-get-api-failure">' in html
    assert '<span class="method get">GET</span><span class="path">/api<wbr>/failure</span>' in html
    assert '<tr class="detail-row detail-failed" id="op-get-api-failure-details">' in html
    assert re.findall(r'tl-lane-name">(\w+)</span><span class="tl-sub">', html) == ["Fuzzing"]


# `/api/items-0` and `/api/items/0` share an anchor slug; `/api/failure/details` collides with a detail-row id.
def test_html_report_large_api(ctx, cli, app_runner, report_dir, snapshot_html):
    paths = {f"/api/items/{index}": {"get": {"responses": {"200": {"description": "OK"}}}} for index in range(26)}
    paths["/api/failure"] = {"get": {"responses": {"200": {"description": "OK"}}}}
    paths["/api/failure/details"] = {"get": {"responses": {"200": {"description": "OK"}}}}
    paths["/api/items-0"] = {"get": {"responses": {"200": {"description": "OK"}}}}
    app, _ = ctx.openapi.make_flask_app(paths)

    @app.route("/api/items/<index>")
    def item(index):
        return "{}"

    @app.route("/api/items-0")
    def item_dash():
        return "{}"

    @app.route("/api/failure")
    def failure():
        return "", 500

    @app.route("/api/failure/details")
    def failure_details():
        return "", 500

    assert run_with_report(cli, report_dir, app_runner.openapi_url(app), exit_code=ExitCode.FAILURES) == snapshot_html


def test_html_report_filter_bar(ctx, cli, app_runner, report_dir, snapshot_html):
    ok = {"responses": {"200": {"description": "OK"}}}
    paths = {"/api/items/{index}": {"get": ok}}
    paths["/api/failure"] = {"post": ok}
    paths["/api/secret"] = {"get": ok}
    app, _ = ctx.openapi.make_flask_app(paths)

    @app.route("/api/items/<index>")
    def item(index):
        return "{}"

    @app.route("/api/failure", methods=["POST"])
    def failure():
        return "", 500

    @app.route("/api/secret")
    def secret():
        return jsonify({"detail": "Not authenticated"}), 401

    html = run_with_report(cli, report_dir, app_runner.openapi_url(app), exit_code=ExitCode.FAILURES)
    assert filter_controls(html) == {
        "fields": ["fb-q", "fb-k", "fb-m"],
        "methods": ["get", "post"],
        "announced": ["fb-count"],
    }
    assert html == snapshot_html


def filter_controls(html):
    bar = re.search(r'<div class="filter-bar[^>]*>.*?</p></div>', html, flags=re.DOTALL).group(0)
    return {
        "fields": re.findall(r'<(?:input|select|div)[^>]* id="(fb-[^"]+)"', bar),
        "methods": re.findall(r'data-m="([^"]+)"', bar),
        "announced": re.findall(r'<[^>]* id="(fb-[^"]+)"[^>]* role="status"', bar),
    }


@pytest.fixture
def checks_with_similar_names():
    @schemathesis.check
    def bad_body(ctx, response, case):
        raise AssertionError("bad")

    @schemathesis.check
    def bad__body(ctx, response, case):
        raise AssertionError("bad")

    yield
    CHECKS.unregister("bad_body")
    CHECKS.unregister("bad__body")


@pytest.mark.usefixtures("checks_with_similar_names")
def test_html_report_failure_kinds_with_similar_titles(ctx, cli, report_dir):
    api = ctx.openapi.apps.success()
    html = run_with_report(cli, report_dir, api.schema_url, exit_code=ExitCode.FAILURES)
    slugs = re.findall(r'data-slug="([^"]+)"', html)
    assert sorted(slugs) == ["custom-check-failed-bad-body", "custom-check-failed-bad-body-2"]
    assert sorted(re.findall(r'href="#kind=([^"]+)"', html)) == sorted(slugs)


# Methods the schema does not declare stay out of the method toggles.
def test_html_report_filter_bar_methods_from_schema(ctx, cli, app_runner, report_dir):
    paths = {"/api/items/{index}": {"get": {"responses": {"200": {"description": "OK"}}}}}
    app, _ = ctx.openapi.make_flask_app(paths)

    @app.route("/api/items/<index>", methods=["GET", "TRACE"])
    def item(index):
        return jsonify({})

    html = run_with_report(
        cli,
        report_dir,
        app_runner.openapi_url(app),
        "--checks=unsupported_method",
        phases="coverage",
        exit_code=ExitCode.FAILURES,
    )
    assert "TRACE" in html
    assert filter_controls(html) == {"fields": ["fb-q", "fb-k", "fb-m"], "methods": ["get"], "announced": ["fb-count"]}


def test_html_report_operations_in_natural_order(ctx, cli, app_runner, report_dir):
    ok = {"responses": {"200": {"description": "OK"}}}
    paths = {f"/number/{index}": {"get": ok} for index in (12, 4, 0, 16, 8)}
    paths["/number/4/items"] = {"get": ok, "post": ok}
    app, _ = ctx.openapi.make_flask_app(paths)

    @app.route("/number/<index>", methods=["GET"])
    def number(index):
        return "{}"

    @app.route("/number/<index>/items", methods=["GET", "POST"])
    def items(index):
        return "{}"

    html = run_with_report(cli, report_dir, app_runner.openapi_url(app))
    assert re.findall(r'<tr class="op-row[^"]*" id="([^"]+)"', html) == [
        "op-get-number-0",
        "op-get-number-4",
        "op-get-number-4-items",
        "op-post-number-4-items",
        "op-get-number-8",
        "op-get-number-12",
        "op-get-number-16",
    ]


def failed_rows(html):
    return [
        (anchor, [first, *re.findall(r"<li>([^<]+)</li>", rest)])
        for anchor, first, rest in re.findall(
            r'<tr class="op-row row-failed[^"]*" id="([^"]+)">.*?<span class="failure-title">([^<]+)</span>(.*?)</td>',
            html,
        )
    ]


def test_html_report_failures_ordered_by_severity(ctx, cli, app_runner, report_dir):
    header = {"X-Token": {"required": True, "schema": {"type": "string"}}}
    json_body = {"application/json": {"schema": {"type": "object"}}}
    app, _ = ctx.openapi.make_flask_app(
        {
            "/api/array": {
                "get": {"responses": {"200": {"description": "OK", "headers": header, "content": json_body}}}
            },
            "/api/crash": {"get": {"responses": {"200": {"description": "OK"}}}},
            "/api/text": {
                "get": {"responses": {"200": {"description": "OK", "headers": header, "content": json_body}}}
            },
        }
    )

    @app.route("/api/array")
    def array():
        return jsonify([])

    @app.route("/api/crash")
    def crash():
        return "", 500

    @app.route("/api/text")
    def text():
        return Response("plain", content_type="text/plain")

    html = run_with_report(cli, report_dir, app_runner.openapi_url(app), exit_code=ExitCode.FAILURES)
    assert failed_rows(html) == [
        ("op-get-api-crash", ["Server error", "Undocumented status code"]),
        ("op-get-api-array", ["Response violates schema", "Missing required response headers"]),
        ("op-get-api-text", ["Missing required response headers", "Undocumented Content-Type"]),
    ]


def test_html_report_graphql(ctx, cli, report_dir, snapshot_html):
    api = ctx.graphql.apps.books()
    assert run_with_report(cli, report_dir, api.schema_url) == snapshot_html


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [(1.23, "1.2s"), (59.96, "1m 0s"), (125.0, "2m 5s"), (7325.0, "2h 2m")],
    ids=["seconds", "rounds-into-minutes", "minutes", "hours"],
)
def test_humanize_duration(seconds, expected):
    assert humanize_duration(seconds) == expected


def test_html_report_fuzz_interrupted(ctx, cli, report_dir, snapshot_html):
    api = ctx.openapi.apps.success()
    module = ctx.write_pymodule(
        """
@schemathesis.hook
def before_call(ctx, case, **kwargs):
    raise KeyboardInterrupt
"""
    )
    result = cli.main(
        "fuzz",
        api.schema_url,
        "--max-time=2",
        "--seed=42",
        f"--report-html-path={report_dir}",
        hooks=module,
    )
    assert result.exit_code == ExitCode.INTERRUPTED, result.stdout
    assert read_report(report_dir) == snapshot_html


def test_html_report_baseline_not_written(ctx, cli, tmp_path, report_dir, snapshot_html):
    api = ctx.openapi.apps.success()
    # The baseline's parent is a regular file, so saving the baseline fails after the run.
    parent = tmp_path / "file"
    parent.write_text("", encoding="utf-8")
    baseline = str(parent / "baseline.yaml")
    html = run_with_report(
        cli, report_dir, api.schema_url, "--baseline-update", exit_code=ExitCode.ERROR, config={"baseline": baseline}
    )
    # The message quotes the path with `repr`, which doubles Windows backslashes.
    assert html.replace(repr(baseline)[1:-1], "<BASELINE>") == snapshot_html


def test_html_report_no_checks_ran(ctx, cli, report_dir, snapshot_html):
    api = ctx.openapi.apps.success()
    html = run_with_report(cli, report_dir, api.schema_url, config={"checks": {"enabled": False}})
    assert html == snapshot_html


def test_html_report_failure_details(ctx, cli, app_runner, report_dir, snapshot_html):
    app, _ = ctx.openapi.make_flask_app(
        {
            "/api/items": {
                "get": {
                    "responses": {
                        "200": {
                            "description": "OK",
                            "content": {
                                "application/json": {"schema": {"type": "array", "items": {"type": "integer"}}}
                            },
                        }
                    }
                }
            },
            "/api/failure": {"get": {"responses": {"200": {"description": "OK"}}}},
        }
    )

    @app.route("/api/items")
    def items():
        return jsonify([{"id": index, "name": f"item-{index}"} for index in range(20)])

    @app.route("/api/failure")
    def failure():
        return "Internal error\nRequest id: 42", 500

    html = run_with_report(cli, report_dir, app_runner.openapi_url(app), exit_code=ExitCode.FAILURES)
    assert html == snapshot_html


def test_html_report_schema_violation_message_truncates_large_value(ctx, cli, app_runner, report_dir):
    app, _ = ctx.openapi.make_flask_app(
        {
            "/api/items": {
                "get": {
                    "responses": {
                        "200": {"description": "OK", "content": {"application/json": {"schema": {"type": "object"}}}}
                    }
                }
            },
        }
    )

    @app.route("/api/items")
    def items():
        return jsonify([{"id": index, "name": f"item-{index}"} for index in range(1000)])

    result = cli.run_and_assert(
        app_runner.openapi_url(app),
        "--max-examples=1",
        "--phases=fuzzing",
        "--checks=response_schema_conformance",
        f"--report-html-path={report_dir}",
        exit_code=ExitCode.FAILURES,
    )
    expected = (
        '[{"id":0,"name":"item-0"},{"id":1,"name":"item-1"},{"id":2,"name":"item-2"}, ... is not of type "object"'
    )
    assert [line.strip() for line in result.stdout.splitlines() if line.endswith('is not of type "object"')] == [
        expected
    ]
    messages = re.findall(r'<pre class="msg">([^<]*)</pre>', read_report(report_dir))
    assert [message.split("\n", 1)[0] for message in messages] == [escape(expected)]


# Under an unknown shell, curl commands for invalid bodies end with warning lines.
def test_html_report_stateful_failure(ctx, cli, app_runner, report_dir, snapshot_html, monkeypatch):
    monkeypatch.setenv("SHELL", "/bin/unknown")
    item_id = {"name": "item_id", "in": "path", "required": True, "schema": {"type": "integer"}}
    app, _ = ctx.openapi.make_flask_app(
        {
            "/api/items": {
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {"name": {"type": "string", "enum": ["apple"]}},
                                    "required": ["name"],
                                }
                            }
                        },
                    },
                    "responses": {
                        "201": {
                            "description": "Created",
                            "links": {
                                "GetItem": {
                                    "operationId": "getItem",
                                    "parameters": {"item_id": "$response.body#/id"},
                                }
                            },
                        }
                    },
                }
            },
            "/api/items/{item_id}": {
                "get": {
                    "operationId": "getItem",
                    "parameters": [item_id],
                    "responses": {"200": {"description": "OK"}, "404": {"description": "Not found"}},
                }
            },
        }
    )
    created = []

    @app.route("/api/items", methods=["POST"])
    def create_item():
        created.append(len(created) + 1)
        return jsonify({"id": created[-1]}), 201

    @app.route("/api/items/<int:item_id>")
    def get_item(item_id):
        if item_id in created:
            return "", 500
        return jsonify({"detail": "Not found"}), 404

    html = run_with_report(
        cli,
        report_dir,
        app_runner.openapi_url(app),
        "--checks=not_a_server_error",
        phases="stateful",
        exit_code=ExitCode.FAILURES,
    )
    assert html == snapshot_html
    assert "until first failure" not in html


def copied_texts(html):
    # A copy button copies the value shown next to it; in a reproduction, its commands and then the replay command.
    copied = {
        label: unescape(re.sub(r"<[^>]+>", "", value))
        for value, label in re.findall(
            r'<span class="tv-text">(.*?)</span><button type="button" class="copy-btn" aria-label="Copy ([^"]+)">', html
        )
    }
    for content, replay in re.findall(
        r'<div class="repro">(.*?)<button[^>]*>Copy</button></div>(?:<p class="replay">or <code>(.*?)</code></p>)?',
        html,
        flags=re.DOTALL,
    ):
        commands = re.findall(r'<pre class="cmd" tabindex="0">(.*?)</pre>', content, flags=re.DOTALL)
        text = "\n".join(unescape(command) for command in commands)
        copied.setdefault("reproduction", []).append(text + f"\n\n{unescape(replay)}" if replay else text)
    return copied


def test_html_report_copy_buttons_copy_shown_text(ctx, cli, app_runner, report_dir, monkeypatch):
    note = "X-Note: a&b  <c>\"d'e"
    monkeypatch.setattr(sys, "argv", ["/venv/bin/st", "run", "--header", note])
    name = "a&b<c>\"d'e"
    app, _ = ctx.openapi.make_flask_app(
        {
            "/api/items": {
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {"name": {"type": "string", "enum": [name]}},
                                    "required": ["name"],
                                    "additionalProperties": False,
                                }
                            }
                        },
                    },
                    "responses": {
                        "201": {
                            "description": "Created",
                            "links": {
                                "GetItem": {
                                    "operationId": "getItem",
                                    "parameters": {"item_id": "$response.body#/id"},
                                }
                            },
                        }
                    },
                }
            },
            "/api/items/{item_id}": {
                "get": {
                    "operationId": "getItem",
                    "parameters": [{"name": "item_id", "in": "path", "required": True, "schema": {"type": "integer"}}],
                    "responses": {"200": {"description": "OK"}},
                }
            },
        }
    )

    @app.route("/api/items", methods=["POST"])
    def create_item():
        return jsonify({"id": 1}), 201

    @app.route("/api/items/<int:item_id>")
    def get_item(item_id):
        return "", 500

    base_url = app_runner.openapi_url(app, path="")
    html = run_with_report(
        cli,
        report_dir,
        f"{base_url}/openapi.json",
        "--checks=not_a_server_error",
        "--mode=positive",
        phases="stateful",
        exit_code=ExitCode.FAILURES,
    )
    replay = re.search(r'<p class="replay">or <code>(st replay \w+)</code></p>', html).group(1)
    assert "data-copy" not in html
    assert copied_texts(html) == {
        "base URL": f"{base_url}/",
        "spec": f"{base_url}/openapi.json",
        "command": f"st run --header {note}",
        "seed": "42",
        "reproduction": [
            f"curl -X POST -H 'Content-Type: application/json' -d '{{\"name\": \"a&b<c>\\\"d'\"'\"'e\"}}' "
            f"{base_url}/api/items\n"
            f"curl -X GET {base_url}/api/items/1\n\n{replay}"
        ],
    }


def test_html_report_error_details(ctx, cli, report_dir, snapshot_html):
    api = ctx.openapi.apps.success_and_failure()
    module = ctx.write_pymodule(
        """
@schemathesis.hook
def before_call(ctx, case, **kwargs):
    if case.path == "/api/failure":
        raise ValueError("Broken hook")
"""
    )
    html = run_with_report(cli, report_dir, api.schema_url, exit_code=ExitCode.FAILURES, hooks=module)
    assert html == snapshot_html


def test_html_report_network_error_details(ctx, cli, report_dir, snapshot_html):
    api = ctx.openapi.apps.success()
    html = run_with_report(cli, report_dir, api.schema_url, "--url=http://127.0.0.1:1", exit_code=ExitCode.FAILURES)
    assert html == snapshot_html


def test_html_report_run_level_failure(ctx, cli, report_dir, snapshot_html, ensure_reachability_module):
    api = ctx.openapi.apps.success_and_failure()
    html = run_with_report(
        cli,
        report_dir,
        api.schema_url,
        "-c",
        "EnsureReachability",
        exit_code=ExitCode.FAILURES,
        hooks=ensure_reachability_module,
    )
    assert html == snapshot_html


# The check's extra request without credentials is the only one in the reproduction.
def test_html_report_ignored_auth(ctx, cli, app_runner, report_dir, snapshot_html):
    app, _ = ctx.openapi.make_flask_app(
        {"/api/me": {"get": {"security": [{"bearer": []}], "responses": {"200": {"description": "OK"}}}}},
        components={"securitySchemes": {"bearer": {"type": "http", "scheme": "bearer"}}},
    )

    @app.route("/api/me")
    def me():
        return jsonify({"user": "alice"})

    html = run_with_report(
        cli,
        report_dir,
        app_runner.openapi_url(app),
        "-c",
        "ignored_auth",
        "-H",
        "Authorization: Bearer secret",
        exit_code=ExitCode.FAILURES,
    )
    assert html == snapshot_html


def test_html_report_timeout_error(ctx, cli, app_runner, report_dir, snapshot_html):
    app, _ = ctx.openapi.make_flask_app({"/api/slow": {"get": {"responses": {"200": {"description": "OK"}}}}})

    @app.route("/api/slow")
    def slow():
        time.sleep(1)
        return jsonify({})

    html = run_with_report(
        cli, report_dir, app_runner.openapi_url(app), "--request-timeout=0.1", exit_code=ExitCode.FAILURES
    )
    assert html == snapshot_html


# The unsatisfiable body errors only on positive cases, reached by continuing past the first failure.
def test_html_report_error_tip(ctx, cli, app_runner, report_dir, snapshot_html):
    body = {"allOf": [{"type": "string"}, {"type": "integer"}]}
    app, _ = ctx.openapi.make_flask_app(
        {
            "/api/conflict": {
                "post": {
                    "requestBody": {"required": True, "content": {"application/json": {"schema": body}}},
                    "responses": {"200": {"description": "OK"}},
                }
            }
        }
    )
    html = run_with_report(
        cli, report_dir, app_runner.openapi_url(app), "--continue-on-failure", exit_code=ExitCode.FAILURES
    )
    assert html == snapshot_html


def test_html_report_multiline_warning_tip(ctx, cli, report_dir, snapshot_html):
    api = ctx.openapi.apps.sign_up_and_login("login-rejects")
    html = run_with_report(cli, report_dir, api.schema_url, "--checks=not_a_server_error", phases="examples,fuzzing")
    assert html == snapshot_html
    assert "white-space:pre-wrap" in re.search(r"\.w-tip\{([^}]*)\}", html).group(1)


@pytest.fixture
def book_id_scalar():
    schemathesis.graphql.scalar("BookID", st.uuids().map(str).map(nodes.String))
    yield
    CUSTOM_SCALARS.clear()


# Generated titles differ between runs, so only the step labels are compared.
@pytest.mark.usefixtures("book_id_scalar")
def test_html_report_graphql_stateful_steps(ctx, cli, report_dir):
    api = ctx.graphql.apps.use_after_create()
    cli.run_and_assert(
        api.schema_url,
        "--max-examples=20",
        "--phases=stateful",
        "--mode=positive",
        "--checks=not_a_server_error",
        "--seed=42",
        f"--report-html-path={report_dir}",
        exit_code=ExitCode.FAILURES,
    )
    html = read_report(report_dir)
    steps = re.findall(r'<span class="step-req">([^<]*)', html)
    assert steps
    assert all(re.fullmatch(r"(Query|Mutation)\.\w+", step) for step in steps), steps
    assert '<span class="step-req">Query.book<span class="step-failed">failed here</span>' in html


def test_html_report_graphql_unknown_scalar_tip(ctx, cli, report_dir, snapshot_html):
    api = ctx.graphql.apps.use_after_create()
    assert run_with_report(cli, report_dir, api.schema_url, exit_code=ExitCode.FAILURES) == snapshot_html


# Each phase times out on different parameters; timing varies the case count, so only error cards are compared.
def test_html_report_repeated_error_shown_once(ctx, cli, app_runner, report_dir):
    parameter = {"name": "q", "in": "query", "required": True, "schema": {"type": "integer"}}
    app, _ = ctx.openapi.make_flask_app(
        {"/api/slow": {"get": {"parameters": [parameter], "responses": {"200": {"description": "OK"}}}}}
    )

    @app.route("/api/slow")
    def slow():
        time.sleep(0.2)
        return jsonify({})

    html = run_with_report(
        cli,
        report_dir,
        app_runner.openapi_url(app),
        "--request-timeout=0.02",
        phases="examples,coverage,fuzzing",
        exit_code=ExitCode.FAILURES,
    )
    assert html.count('<article class="case case-error">') == 1


def two_failing_operations(ctx):
    app, _ = ctx.openapi.make_flask_app(
        {
            "/api/users": {"get": {"responses": {"200": {"description": "OK"}}}},
            "/api/orders": {"get": {"responses": {"200": {"description": "OK"}}}},
        }
    )

    @app.route("/api/users")
    def users():
        return "", 500

    @app.route("/api/orders")
    def orders():
        return "", 500

    return app


# Each operation's two failures arrive with one response, so every tick carries a count badge.
# Responses are stamped at the epoch, so every failure is found at the run's start however slow the machine is.
def test_html_report_timeline_quiet_tail(ctx, cli, app_runner, report_dir, monkeypatch):
    monkeypatch.setattr("schemathesis.engine.recorder.time", SimpleNamespace(time=lambda: 0.0))
    html = run_with_report(
        cli,
        report_dir,
        app_runner.openapi_url(two_failing_operations(ctx)),
        phases="coverage,fuzzing",
        exit_code=ExitCode.FAILURES,
    )
    assert '<section class="timeline"' in html
    assert re.search(
        r'<div class="tl-quiet" style="left:[\d.]+%"><span><span class="tl-q-long">No new failures for</span>'
        r'<span class="tl-q-short">Quiet</span> [\d.]+m?s</span></div>',
        html,
    )
    counts = re.findall(
        r'<a class="tl-mark" href="#case-\w+"[^>]*>(?:<span class="tl-count" aria-hidden="true">(\d+)</span>)?',
        html,
    )
    assert "" not in counts
    assert sum(map(int, counts)) == 4


# Passes end on their own until the server breaks, so fuzzing and stateful take turns however slow the machine is.
def test_html_report_timeline_draws_each_pass(ctx, cli, report_dir):
    api = ctx.openapi.apps.stateful_users(FailAfterRequests(40))
    html = run_until_server_breaks(cli, report_dir, api.schema_url, phases="fuzzing,stateful")
    tracks = re.findall(r'<div class="tl-track tl-lane">(.*?)</div></div>', html)
    assert [track.count('class="tl-span') > 1 for track in tracks] == [True, True]


# Back-to-back fuzzing passes with nothing in between read as one stretch of fuzzing.
def test_html_report_timeline_merges_consecutive_passes(ctx, cli, report_dir):
    api = ctx.openapi.apps.stateful_users(FailAfterRequests(20))
    html = run_until_server_breaks(cli, report_dir, api.schema_url, phases="fuzzing")
    (details, track) = re.findall(
        r'<span class="tl-sub">([^<]*)</span></div><div class="tl-track tl-lane">(.*?)</div></div>', html
    )[0]
    assert track.count('class="tl-span') == 1
    assert "passes" not in details


def run_until_server_breaks(cli, report_dir, schema_url, *, phases):
    # The time limit only keeps phases repeating; the first failure ends the run.
    return run_with_report(
        cli,
        report_dir,
        schema_url,
        "--max-time=600",
        "--max-failures=1",
        "--mode=positive",
        phases=phases,
        exit_code=ExitCode.FAILURES,
    )


def marker(seconds, phase="Fuzzing", count=1):
    return Marker(seconds, phase, "case-1", "GET /api/users: Server error", count)


def test_timeline_ticks_merge_by_rendered_distance():
    groups = tick_groups([marker(at) for at in (0, 0.5, 3, 3.5, 6, 30)], 100.0)
    assert [[item.found_at for item in group] for group in groups] == [[0, 0.5], [3, 3.5], [6], [30]]


# 1.2s apart in a 20s run is about 60px on the desktop track.
def test_timeline_ticks_keep_distinct_times_apart():
    groups = tick_groups([marker(3.7, count=2), marker(4.9, count=2)], 20.0)
    assert [[item.found_at for item in group] for group in groups] == [[3.7], [4.9]]


# A tick with a count badge keeps the next tick clear of the badge; a plain tick keeps a small gap.
def test_timeline_ticks_never_overlap():
    groups = tick_groups([marker(index * 0.5) for index in range(200)], 100.0)
    for previous, current in itertools.pairwise(groups):
        gap = (current[0].found_at - previous[0].found_at) / 100.0 * TRACK_PX
        assert gap >= (BADGE_PX if len(previous) > 1 else TICK_GAP_PX)


def test_timeline_ticks_drawn_for_many_failures_found_close_together():
    lane = Lane("Fuzzing", [], [marker(index * 0.01) for index in range(200)])
    assert [len(groups) for groups in lane_ticks([lane], 100.0)] == [1]


def test_timeline_ticks_hidden_when_too_many_to_draw():
    lanes = [Lane(phase, [], [marker(index * 3.0, phase) for index in range(30)]) for phase in ("Coverage", "Fuzzing")]
    assert lane_ticks(lanes, 100.0) == [[], []]


def curve_points(path):
    points = []
    x, y = 0.0, 100.0
    for command, value in re.findall(r"([HV])([\d.]+)", path.removeprefix("M0,100")):
        if command == "H":
            x = float(value)
        else:
            y = float(value)
            points.extend((x, y))
    return points


def test_timeline_curve_merges_failures_drawn_at_one_point():
    markers = [marker(10.0), marker(10.00001), marker(20.0)]
    assert curve_path(markers, 100.0) == "M0,100H100V38.7H200V8H1000"


def test_timeline_curve_skips_steps_too_small_to_draw():
    markers = [marker(10.0), marker(20.0), marker(30.0, count=9998)]
    assert curve_path(markers, 100.0) == "M0,100H300V8H1000"


# Each drawn step shows the count at the end of its stretch, so the curve never rises before a failure is found.
def test_timeline_curve_keeps_its_shape_for_many_failures():
    markers = [marker(index * 0.1) for index in range(1000)]
    expected = [0.0, 99.9]
    for step in range(1, 501):
        expected.extend((2.0 * step, 100 - min(2 * step + 1, 1000) / 1000 * 92))
    assert curve_points(curve_path(markers, 100.0)) == pytest.approx(expected, abs=0.051)


def test_timeline_blocks_split_only_where_another_phase_ran():
    spans = [
        PhaseSpan("Fuzzing", 0.0, 1.0),
        PhaseSpan("Fuzzing", 1.0, 2.0),
        PhaseSpan("Stateful", 2.0, 3.0),
        PhaseSpan("Fuzzing", 3.0, 4.0),
        PhaseSpan("Fuzzing", 4.0, 5.0),
    ]
    assert phase_blocks(spans) == {"Fuzzing": [(0.0, 2.0), (3.0, 5.0)], "Stateful": [(2.0, 3.0)]}


def test_timeline_lane_details_sum_passes():
    assert Lane("Fuzzing", [(0.0, 1.0), (2.0, 3.5)], [marker(0.5)]).details() == "2.5s in 2 passes, 1 found"


def test_timeline_axis_for_long_runs():
    assert len(axis_ticks(30 * 3600)) <= MAX_TICKS


def test_timeline_axis_for_short_runs():
    assert axis_ticks(0.089) == pytest.approx([0, 0.025, 0.05, 0.075])


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [(10.0, ""), (85.0, " tl-tip-end"), (98.0, " tl-tip-end tl-mark-end")],
    ids=["start", "past-80", "past-97"],
)
def test_timeline_mark_classes(seconds, expected):
    assert mark_classes(seconds, 100.0) == expected


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [(0.25, "250ms"), (12.0, "12s"), (120.0, "2m"), (125.4, "2m 5s"), (7200.0, "2h"), (7380.0, "2h 3m")],
    ids=["milliseconds", "seconds", "minutes", "minutes-and-seconds", "hours", "hours-and-minutes"],
)
def test_timeline_short_duration(seconds, expected):
    assert short_duration(seconds) == expected


def test_html_report_run_level_error(ctx, cli, app_runner, report_dir, snapshot_html):
    app, _ = ctx.openapi.make_flask_app(
        {
            "/api/items": {
                "post": {
                    "responses": {
                        "201": {
                            "description": "Created",
                            "links": {
                                "Missing": {"operationId": "missing", "parameters": {"id": "$response.body#/id"}}
                            },
                        }
                    }
                }
            },
        }
    )

    @app.route("/api/items", methods=["POST"])
    def create_item():
        return jsonify({"id": 1}), 201

    html = run_with_report(
        cli, report_dir, app_runner.openapi_url(app), phases="fuzzing,stateful", exit_code=ExitCode.FAILURES
    )
    assert html == snapshot_html


def test_html_report_lanes_count_only_new_failures(ctx, cli, app_runner, report_dir):
    item = {"type": "object", "properties": {"id": {"type": "integer"}}}
    app, _ = ctx.openapi.make_flask_app(
        {
            "/api/items": {
                "post": {
                    "responses": {
                        "201": {
                            "description": "Created",
                            "content": {"application/json": {"schema": item}},
                            "links": {"Get": {"operationId": "getItem", "parameters": {"id": "$response.body#/id"}}},
                        }
                    }
                }
            },
            "/api/items/{id}": {
                "get": {
                    "operationId": "getItem",
                    "parameters": [{"name": "id", "in": "path", "required": True, "schema": {"type": "integer"}}],
                    "responses": {"200": {"description": "OK"}},
                }
            },
        }
    )
    app.add_url_rule("/api/items", "create", lambda: ({"id": 1}, 201), methods=["POST"])
    app.add_url_rule("/api/items/<int:id>", "get", lambda id: ({"detail": "boom"}, 500))

    html = run_with_report(
        cli,
        report_dir,
        app_runner.openapi_url(app),
        "--checks=not_a_server_error",
        "--continue-on-failure",
        phases="fuzzing,stateful",
        exit_code=ExitCode.FAILURES,
    )
    lanes = dict(re.findall(r'<span class="tl-lane-name">([^<]+)</span><span class="tl-sub">([^<]*)</span>', html))
    # The duration comes first; only the counts are stable.
    assert {name: details.split(", ")[1:] for name, details in lanes.items()} == {
        "Fuzzing": ["1 found"],
        "Stateful": [],
    }


def test_html_report_warning_on_passed_run(ctx, cli, report_dir, snapshot_html):
    api = ctx.openapi.apps.basic()
    html = run_with_report(cli, report_dir, api.schema_url, "--checks=not_a_server_error", "--mode=positive")
    assert html == snapshot_html


def test_html_report_warning_grouped_by_cause(ctx, cli, report_dir, snapshot_html):
    api = ctx.openapi.apps.users_crud()
    html = run_with_report(
        cli,
        report_dir,
        api.schema_url,
        f"--url={api.base_url}/v4/",
        "--checks=not_a_server_error",
        "--mode=positive",
        phases="fuzzing,stateful",
    )
    assert html == snapshot_html


def test_html_report_warning_with_details(ctx, cli, app_runner, report_dir, snapshot_html):
    body = {"type": "array", "items": {"type": "string", "pattern": r"\p{Tibetan}"}, "maxItems": 3}
    app, _ = ctx.openapi.make_flask_app(
        {
            "/api/tags": {
                "post": {
                    "requestBody": {"required": True, "content": {"application/json": {"schema": body}}},
                    "responses": {"200": {"description": "OK"}},
                }
            }
        }
    )

    @app.route("/api/tags", methods=["POST"])
    def tags():
        return jsonify({})

    html = run_with_report(cli, report_dir, app_runner.openapi_url(app), "--checks=not_a_server_error")
    assert html == snapshot_html


def test_html_report_unmatched_filter_warning(ctx, cli, report_dir, snapshot_html):
    api = ctx.openapi.apps.success()
    html = run_with_report(
        cli, report_dir, api.schema_url, "--include-name=GET /api/success", "--include-name=GET /api/missing"
    )
    assert html == snapshot_html


def test_html_report_startup_warning(ctx, cli, report_dir, snapshot_html):
    api = ctx.openapi.apps.success()
    assert run_with_report(cli, report_dir, api.schema_url, "--request-timeout=2000") == snapshot_html


# Startup warnings come first, then warnings that affect the verdict, then setup notes.
def test_html_report_warnings_order(ctx, cli, app_runner, report_dir):
    order_id = {"name": "order_id", "in": "path", "required": True, "schema": {"type": "integer"}}
    app, _ = ctx.openapi.make_flask_app(
        {"/api/orders/{order_id}": {"get": {"parameters": [order_id], "responses": {"200": {"description": "OK"}}}}}
    )
    calls = itertools.count()

    @app.route("/api/orders/<order_id>")
    def order(order_id):
        if next(calls) % 10 == 0:
            return jsonify({"id": order_id})
        return jsonify({"detail": "Not found"}), 404

    cli.run_and_assert(
        app_runner.openapi_url(app),
        "--checks=not_a_server_error",
        "--mode=positive",
        "--max-examples=10",
        "--phases=fuzzing",
        "--seed=42",
        "--request-timeout=2000",
        "--include-path-regex=^/api/orders",
        "--include-name=GET /api/missing",
        "--warnings=low_valid_rate,unmatched_filter,timeout_units",
        f"--report-html-path={report_dir}",
    )
    html = read_report(report_dir)
    entries = re.findall(r'<li class="(warning[^"]*)">(?:<span>|<details id="([^"]+)")', html)
    assert entries == [
        ("warning startup", ""),
        ("warning", "w-low-valid-input-rate"),
        ("warning note", "w-unmatched-filters"),
    ]
    assert re.findall(r'<a class="hs-caveat"[^>]*>([^<]*)</a>', html) == ["1 warning affects results"]


def test_html_report_caveat_counts_affecting_warnings(ctx, cli, app_runner, report_dir):
    order_id = {"name": "order_id", "in": "path", "required": True, "schema": {"type": "integer"}}
    app, _ = ctx.openapi.make_flask_app(
        {
            "/api/orders/{order_id}": {"get": {"parameters": [order_id], "responses": {"200": {"description": "OK"}}}},
            "/api/users": {"get": {"responses": {"200": {"description": "OK"}}}},
        }
    )

    @app.route("/api/orders/<order_id>")
    def order(order_id):
        return jsonify({"detail": "Not found"}), 404

    @app.route("/api/users")
    def users():
        return jsonify({"detail": "Unauthorized"}), 401

    html = run_with_report(
        cli,
        report_dir,
        app_runner.openapi_url(app),
        "--checks=not_a_server_error",
        "--mode=positive",
    )
    assert re.findall(r'<a class="hs-caveat"[^>]*>([^<]*)</a>', html) == ["2 warnings affect results"]


def test_html_report_long_warning_shows_first_operations(ctx, cli, app_runner, report_dir):
    app, _ = ctx.openapi.make_flask_app(
        {f"/api/users/{name}": {"get": {"responses": {"200": {"description": "OK"}}}} for name in "abcdefghijkl"}
    )

    @app.route("/api/users/<name>")
    def users(name):
        return jsonify({"detail": "Unauthorized"}), 401

    html = run_with_report(
        cli, report_dir, app_runner.openapi_url(app), "--checks=not_a_server_error", "--mode=positive"
    )
    warning = re.search(r'<details id="w-authentication-failed".*?</details></li>', html, flags=re.DOTALL).group(0)
    shown, hidden = warning.split('<details class="fails w-more">')
    path = r'<span class="path">/api/users/(\w)</span>'
    assert re.findall(path, shown) == list("abcdefghij")
    assert re.findall(path, hidden) == ["k", "l"]
    assert '<span class="m-closed">+2 more</span>' in hidden
    assert html.index('<section class="target-block">') < html.index('<section class="warnings"')


# An unrecognised entrypoint hides the Command row instead of showing an uncopyable placeholder.
def test_html_report_phases_and_checks(ctx, cli, report_dir, snapshot_html, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["/venv/bin/python"])
    api = ctx.openapi.apps.success()
    html = run_with_report(
        cli,
        report_dir,
        api.schema_url,
        "--checks=not_a_server_error,status_code_conformance",
        phases="examples,fuzzing",
    )
    assert html == snapshot_html


def test_html_report_known_failures_from_baseline(ctx, cli, tmp_path, report_dir, snapshot_html):
    api = ctx.openapi.apps.failure()
    baseline = str(tmp_path / "baseline.yaml")
    run_with_report(
        cli, report_dir, api.schema_url, "--baseline-update", exit_code=ExitCode.FAILURES, config={"baseline": baseline}
    )
    assert run_with_report(cli, report_dir, api.schema_url, config={"baseline": baseline}) == snapshot_html


def test_html_report_baseline_pruned_unobserved_expired_and_max_response_time(
    ctx, cli, tmp_path, report_dir, snapshot_html
):
    server_error = {"check": "not_a_server_error", "failure": "ServerError", "signature": "500"}
    entries = [
        {**server_error, "operation": "GET /api/success"},
        {**server_error, "operation": "GET /api/missing", "expires": "2020-01-01"},
    ]
    baseline = tmp_path / "baseline.json"
    baseline.write_text(json.dumps({"format_version": 1, "entries": entries}), encoding="utf-8")
    html = run_with_report(
        cli,
        report_dir,
        ctx.openapi.apps.success().schema_url,
        "--checks=not_a_server_error",
        "--max-response-time=100",
        "--baseline-prune",
        config={"baseline": str(baseline)},
    )
    assert html == snapshot_html


def test_html_report_filtered_failures(ctx, cli, report_dir, snapshot_html):
    api = ctx.openapi.apps.failure()
    module = ctx.write_pymodule(
        """
@schemathesis.hook
def filter_failure(context, failure, case, response):
    return response.status_code != 500
"""
    )
    html = run_with_report(cli, report_dir, api.schema_url, "--checks=not_a_server_error", hooks=module)
    assert html == snapshot_html


def test_html_report_extraction_failures(ctx, cli, app_runner, report_dir, snapshot_html):
    app, _ = ctx.openapi.make_flask_app(
        {
            "/api/items": {
                "post": {
                    "operationId": "createItem",
                    "responses": {
                        "201": {
                            "description": "Created",
                            "content": {"application/json": {"schema": {"type": "object"}}},
                            "links": {
                                "GetItem": {"operationId": "getItem", "parameters": {"id": "$response.body#/id"}}
                            },
                        }
                    },
                }
            },
            "/api/items/{id}": {
                "get": {
                    "operationId": "getItem",
                    "parameters": [{"name": "id", "in": "path", "required": True, "schema": {"type": "integer"}}],
                    "responses": {"200": {"description": "OK"}},
                }
            },
        }
    )

    @app.route("/api/items", methods=["POST"])
    def create_item():
        return Response(b"not json", status=201, content_type="application/json")

    @app.route("/api/items/<int:item_id>")
    def get_item(item_id):
        return jsonify({})

    html = run_with_report(
        cli, report_dir, app_runner.openapi_url(app), "--checks=not_a_server_error", phases="stateful"
    )
    assert html == snapshot_html


# Warnings configured to fail the run make the exit code non-zero while every test passes.
def test_html_report_warnings_fail_the_run(ctx, cli, report_dir, snapshot_html):
    api = ctx.openapi.apps.basic()
    html = run_with_report(
        cli,
        report_dir,
        api.schema_url,
        "--checks=not_a_server_error",
        "--mode=positive",
        exit_code=ExitCode.FAILURES,
        config={"warnings": {"fail-on": True}},
    )
    assert html == snapshot_html


def test_html_report_startup_warning_fails_the_run(ctx, cli, report_dir, snapshot_html):
    api = ctx.openapi.apps.success()
    html = run_with_report(
        cli,
        report_dir,
        api.schema_url,
        "--request-timeout=5000",
        exit_code=ExitCode.FAILURES,
        config={"warnings": {"fail-on": ["timeout_units"]}},
    )
    assert html == snapshot_html


def test_html_report_handler_error_after_passed_run(ctx, cli, report_dir, snapshot_html):
    module = ctx.write_pymodule(
        """
from schemathesis import cli, engine


@cli.handler()
class BrokenHandler(cli.EventHandler):
    def handle_event(self, ctx, event):
        if isinstance(event, engine.events.EngineFinished):
            raise RuntimeError("broken handler")
"""
    )
    api = ctx.openapi.apps.success()
    html = run_with_report(cli, report_dir, api.schema_url, exit_code=ExitCode.ERROR, hooks=module)
    assert html == snapshot_html


def test_html_report_default_location(ctx, cli, tmp_path):
    api = ctx.openapi.apps.success()
    result = cli.run(
        api.schema_url, "--report=html", f"--report-dir={tmp_path}", "--max-examples=1", "--phases=fuzzing"
    )
    assert result.exit_code == ExitCode.OK, result.stdout
    (index,) = tmp_path.glob("html-*/index.html")
    assert list(index.parent.iterdir()) == [index]
    assert f"HTML: {index}" in result.stdout


def test_html_report_via_config(ctx, cli, report_dir):
    api = ctx.openapi.apps.success()
    cli.run_and_assert(
        api.schema_url,
        "--max-examples=1",
        "--phases=fuzzing",
        config={"reports": {"html": {"path": str(report_dir)}}},
    )
    assert (report_dir / "index.html").is_file()


def test_html_report_fuzz_operation_errored(ctx, cli, app_runner, report_dir, snapshot_html):
    app, _ = ctx.openapi.make_flask_app({"/api/broken": INVALID_BODY})
    result = cli.main(
        "fuzz", app_runner.openapi_url(app), "--max-time=1", "--seed=42", f"--report-html-path={report_dir}"
    )
    assert result.exit_code == ExitCode.FAILURES, result.stdout
    assert read_report(report_dir) == snapshot_html


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_html_report_unwritable_path(ctx, cli, tmp_path, snapshot_cli):
    api = ctx.openapi.apps.success()
    parent = tmp_path / "file"
    parent.write_text("", encoding="utf-8")
    assert cli.run(api.schema_url, f"--report-html-path={parent / 'report'}") == snapshot_cli


def test_html_report_path_printed_when_run_aborts_before_testing(ctx, cli, tmp_path, report_dir, snapshot_cli):
    api = ctx.openapi.apps.success()
    assert (
        cli.run(
            api.schema_url,
            f"--report-html-path={report_dir}",
            f"--report-junit-path={tmp_path / 'junit.xml'}",
            config={"auth": {"wfc": {"path": str(tmp_path / "auth.json")}}},
        )
        == snapshot_cli
    )
    assert (report_dir / "index.html").is_file()


def test_html_report_links_other_reports(ctx, cli, tmp_path, report_dir):
    coverage = tmp_path / "coverage.html"

    @schemathesis.cli.handler()
    class CoverageReport(schemathesis.cli.EventHandler):
        def handle_event(self, context, event) -> None:
            if isinstance(event, events.EngineFinished):
                context.add_report("Schema coverage", coverage)

    api = ctx.openapi.apps.success()
    html = run_with_report(cli, report_dir, api.schema_url, f"--report-junit-path={tmp_path / 'junit.xml'}")
    assert re.findall(r'<a href="([^"]+)" target="_blank" rel="noopener">([^<]+)</a>', html) == [
        ("../junit.xml", "JUnit"),
        ("../coverage.html", "Schema coverage"),
    ]
