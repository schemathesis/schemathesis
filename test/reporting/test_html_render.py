import re
from importlib.resources import files

import pytest

from schemathesis.cli.commands.run.warnings import WarningData
from schemathesis.engine import Status
from schemathesis.engine.run import PhaseName
from schemathesis.reporting.html.model import (
    CaseEntry,
    ErrorEntry,
    FailureEntry,
    FailureTick,
    OperationEntry,
    ParentStep,
    PhaseCases,
    PhaseTiming,
    ReportData,
    TickItem,
)
from schemathesis.reporting.html.render import render_index, render_operation
from schemathesis.reporting.html.render.components import (
    errors_section,
    esc,
    filter_warnings_for_label,
    humanize_duration,
    method_span,
    page,
    path_span,
    warning_cards,
)
from test.reporting.factories import build_case, build_operation, build_report


def test_esc_escapes_html_metacharacters():
    assert esc('<script>alert("x")</script>') == "&lt;script&gt;alert(&quot;x&quot;)&lt;/script&gt;"


def test_esc_escapes_lone_surrogates():
    assert esc("\ud800") == r"\ud800"


def test_page_shell_references_assets_with_prefix():
    document = page(title="T", body="<p>b</p>", asset_prefix="../")
    assert '<link rel="stylesheet" href="../assets/report.css">' in document
    assert '<script src="../assets/app.js"></script>' in document
    assert "<title>T</title>" in document


def test_page_escapes_title():
    assert "<script>" not in page(title="<script>", body="", asset_prefix="")


def test_method_and_path_spans():
    assert method_span("post") == '<span class="method post">POST</span>'
    assert path_span("/users/{id}") == '<span class="path">/users/{id}</span>'


def test_warning_cards_empty():
    assert warning_cards(WarningData()) == ""


def test_warning_cards_missing_auth_lists_operations():
    html = warning_cards(WarningData(missing_auth={401: {"GET /users"}}))
    assert "Missing authentication" in html
    assert "401" in html
    assert '<span class="path">/users</span>' in html
    assert "—" not in html


def test_warning_cards_escape_labels():
    html = warning_cards(WarningData(missing_test_data={'GET /<script>"x"'}))
    assert "<script>" not in html


def test_filter_warnings_for_label_keeps_only_matching_entries():
    data = WarningData(
        missing_auth={401: {"GET /users", "GET /orders"}},
        missing_test_data={"GET /users"},
        unused_openapi_auth={"apiKey"},
    )
    filtered = filter_warnings_for_label(data, "GET /users")
    assert filtered.missing_auth == {401: {"GET /users"}}
    assert filtered.missing_test_data == {"GET /users"}
    # Schema-level, not tied to any single operation.
    assert filtered.unused_openapi_auth == set()


def test_filter_warnings_for_label_empty_when_label_unaffected():
    data = WarningData(missing_auth={401: {"GET /orders"}})
    filtered = filter_warnings_for_label(data, "GET /users")
    assert filtered.is_empty


def test_errors_section_empty():
    assert errors_section([]) == ""


def test_errors_section_with_traceback():
    html = errors_section(
        [ErrorEntry(label="GET /users", title="RequestError", message="boom <b>", traceback="tb <b>", phase="fuzzing")]
    )
    assert "RequestError" in html
    assert "boom &lt;b&gt;" in html
    assert "tb &lt;b&gt;" in html
    assert "Show traceback" in html


def test_format_duration():
    assert humanize_duration(134.2) == "2m 14s"
    assert humanize_duration(42.13) == "42.1s"
    assert humanize_duration(0.4) == "0.4s"


def test_format_duration_rounds_before_branching_to_minutes():
    # 59.96 rounds to 60.0 at 1-decimal display precision, so it must cross into "1m 0s".
    assert humanize_duration(59.96) == "1m 0s"


def test_assets_ship_with_the_package():
    assets = files("schemathesis.reporting.html") / "assets"
    css = (assets / "report.css").read_text(encoding="utf-8")
    js = (assets / "app.js").read_text(encoding="utf-8")
    # Design tokens and report styles are concatenated into one stylesheet.
    assert "--color-http-get" in css or "--color-brand-danger" in css
    assert "--report-main-info-height" in css
    assert ".hero-strip,\n.op-hero" in css
    assert "icon-logo" in js
    assert "fonts.googleapis.com" not in css


def test_render_index_failed_run():
    failed = build_operation("POST /orders", Status.FAILURE, failing_cases=[build_case("abc123", ["server_error"])])
    passed = build_operation("GET /health", Status.SUCCESS)
    document = render_index(
        build_report([failed, passed]), {"POST /orders": "POST__orders", "GET /health": "GET__health"}
    )
    assert '<div class="hero-status-label">Failed</div>' in document
    assert "1 of 2 operations failed" in document
    assert 'href="operations/POST__orders.html"' in document
    assert "server_error" in document
    assert "Skipped" not in document


def test_render_index_graphql_label_renders_as_path_without_method_badge():
    # GraphQL labels ("Type.field") have no method; they must not be forced into an uppercased method badge.
    entry = build_operation("Query.getBook", Status.SUCCESS)
    document = render_index(build_report([entry]), {"Query.getBook": "Query_getBook"})
    assert '<span class="path">Query.getBook</span>' in document
    assert "QUERY.GETBOOK" not in document
    assert 'class="method query.getbook"' not in document


def test_render_index_all_passed():
    document = render_index(
        build_report([build_operation("GET /health", Status.SUCCESS)]), {"GET /health": "GET__health"}
    )
    assert '<div class="hero-status-label">Passed</div>' in document
    assert "Top failures" not in document
    assert "all passing" in document
    assert "&middot;" not in document
    assert "tested |" not in document


def test_render_index_hero_neutral_when_zero_operations_tested():
    # No operations tested and no fatal error is a distinct state from a real "Passed" run.
    document = render_index(build_report([]), {})
    assert '<div class="hero-status-label">Passed</div>' not in document


def test_render_index_fatal_error_sets_errored_verdict_and_appears_in_errors_section():
    fatal = ErrorEntry(label="", title="Schema Loading Error", message="Connection refused", traceback=None, phase=None)
    document = render_index(build_report([], fatal_errors=[fatal]), {})
    assert '<div class="hero-status-label">Passed</div>' not in document
    assert '<div class="hero-status-label">Errored</div>' in document
    assert "Schema Loading Error" in document
    assert "Connection refused" in document


def test_render_index_fatal_error_shows_top_failures_when_recorded():
    # A fatal crash mid-run may follow real failures already recorded; the hero must show them
    # rather than falling back to the neutral "no failing checks" cell.
    fatal = ErrorEntry(label="", title="Test Execution Error", message="boom", traceback=None, phase=None)
    failed = build_operation("POST /orders", Status.FAILURE, failing_cases=[build_case("abc123", ["server_error"])])
    document = render_index(build_report([failed], fatal_errors=[fatal]), {"POST /orders": "POST__orders"})
    assert "Top failures" in document
    assert "server_error" in document
    assert "no failing checks" not in document


def test_render_index_fatal_error_overrides_passed_verdict_even_with_tested_operations():
    # A mid-run crash after some operations already passed must still read as "Errored", not "Passed".
    fatal = ErrorEntry(label="", title="Test Execution Error", message="boom", traceback=None, phase=None)
    passed = build_operation("GET /health", Status.SUCCESS)
    document = render_index(build_report([passed], fatal_errors=[fatal]), {"GET /health": "GET__health"})
    assert '<div class="hero-status-label">Passed</div>' not in document
    assert '<div class="hero-status-label">Errored</div>' in document


def test_render_index_skipped_rows_have_reason_and_no_link():
    document = render_index(
        build_report([build_operation("GET /internal", Status.SKIP, skip_reason="filtered out")]),
        {"GET /internal": "GET__internal"},
    )
    assert "filtered out" in document
    assert 'href="operations/GET__internal.html"' not in document


def test_render_index_escapes_operation_paths():
    document = render_index(
        build_report([build_operation("GET /<img src=x onerror=alert(1)>", Status.SUCCESS)]),
        {"GET /<img src=x onerror=alert(1)>": "GET__img"},
    )
    assert "<img src=x" not in document


def test_render_index_timeline_positions():
    failed = build_operation("POST /orders", Status.FAILURE, failing_cases=[build_case("abc123", ["server_error"])])
    data = build_report(
        [failed],
        phases={
            PhaseName.EXAMPLES: PhaseTiming(started_at=100.0, finished_at=110.0),
            PhaseName.FUZZING: PhaseTiming(started_at=110.0, finished_at=140.0),
        },
        ticks=[FailureTick(at=120.0, items=[TickItem("server_error", "POST /orders", "abc123")])],
    )
    document = render_index(data, {"POST /orders": "POST__orders"})
    # Examples: 10s of 40s total -> 25%; tick at 120s -> 50% of the run.
    assert 'style="width: 25.0%"' in document
    assert 'style="left: 50.0%"' in document
    assert 'href="operations/POST__orders.html#case-abc123"' in document
    assert "lrule" not in document
    assert "rt-legend" not in document


def test_render_index_timeline_absent_without_phase_timings():
    document = render_index(
        build_report([build_operation("GET /health", Status.SUCCESS)]), {"GET /health": "GET__health"}
    )
    assert "run-timeline" not in document


def test_render_index_timeline_omits_tiny_phase_labels():
    failed = build_operation("POST /orders", Status.FAILURE, failing_cases=[build_case("abc123", ["server_error"])])
    data = build_report(
        [failed],
        phases={
            PhaseName.EXAMPLES: PhaseTiming(started_at=100.0, finished_at=100.1),
            PhaseName.COVERAGE: PhaseTiming(started_at=100.1, finished_at=100.2),
            PhaseName.FUZZING: PhaseTiming(started_at=100.2, finished_at=110.0),
        },
    )
    document = render_index(data, {"POST /orders": "POST__orders"})

    assert '<span class="rt-phase-label">Examples</span>' not in document
    assert '<span class="rt-phase-label">Coverage</span>' not in document
    assert '<span class="rt-phase-label">Fuzzing</span>' in document


def test_render_index_failed_status_row_without_failing_checks():
    # Normalized ERROR/INTERRUPTED entries may have zero failing checks; the row must still read as failed.
    entry = build_operation("POST /orders", Status.FAILURE)
    document = render_index(build_report([entry]), {"POST /orders": "POST__orders"})
    assert 'class="op-row row-failed"' in document
    assert 'data-status="failed"' in document


def test_render_index_hero_hides_top_failures_cell_when_none_recorded():
    # A FAILURE entry with no failing_cases (e.g. ERROR/INTERRUPTED) leaves top_failures empty;
    # the hero must not show an empty "Top failures" list.
    entry = build_operation("POST /orders", Status.FAILURE)
    document = render_index(build_report([entry]), {"POST /orders": "POST__orders"})
    assert "Top failures" not in document
    assert '<ul class="tf-list"></ul>' not in document


def test_render_index_hero_shows_stop_reason_when_stopped_early():
    entry = build_operation("GET /health", Status.SUCCESS)
    document = render_index(build_report([entry], stop_reason="failure_limit"), {"GET /health": "GET__health"})
    assert "hs-stop-note" in document
    assert "failure limit" in document.lower()


@pytest.mark.parametrize("stop_reason", [None, "completed"])
def test_render_index_hero_omits_stop_reason_on_normal_completion(stop_reason):
    entry = build_operation("GET /health", Status.SUCCESS)
    document = render_index(build_report([entry], stop_reason=stop_reason), {"GET /health": "GET__health"})
    assert "hs-stop-note" not in document


def test_render_operation_failed_status_without_failing_checks_shows_note():
    entry = build_operation("POST /orders", Status.FAILURE, error_count=2)
    document = render_operation(build_report([entry]), entry)
    assert "pass-banner" not in document
    assert "op-note-title" in document
    assert "No check failures recorded" in document
    assert "2 non-fatal errors" in document


def test_render_operation_failed():
    case = build_case("abc123", ["server_error"])
    case.curl = "curl -X POST 'http://x/orders' -d '{}'"
    case.response_body = "NullPointerException <script>"
    entry = build_operation("POST /orders", Status.FAILURE, failing_cases=[case])
    document = render_operation(build_report([entry]), entry)
    assert '<span class="method post">POST</span>' in document
    assert 'id="case-abc123"' in document
    assert "server_error" in document
    assert "copy curl" not in document
    assert 'aria-label="Copy request"' in document
    assert "NullPointerException &lt;script&gt;" in document
    assert "pass-banner" not in document
    assert "Checks fired" not in document
    assert "across 1 case" not in document


def test_render_operation_formats_negative_failure_message():
    case = build_case("abc123", ["negative_data_rejection"])
    case.failures[0].message = (
        "Invalid data should have been rejected\n"
        "Expected: 400, 401, 403, 404, 405, 406, 409, 422, 428, 5xx\n"
        "Invalid component: parameter `id` in query - violates `type` at /properties/id "
        "(was integer, became string)"
    )
    entry = build_operation("GET /items", Status.FAILURE, failing_cases=[case])
    document = render_operation(build_report([entry]), entry)
    assert "cf-detailed" in document
    assert "cf-detail-row" in document
    assert "Expected" in document
    assert "Invalid component" in document
    assert "parameter `id` in query" in document


def test_render_operation_passing_shows_banner():
    entry = build_operation("GET /items", Status.SUCCESS)
    document = render_operation(build_report([entry]), entry)
    assert "pass-banner" in document
    assert "All 10 cases passed" in document
    assert "case-card" not in document


def test_render_operation_phase_strip_has_no_separator_glyph():
    entry = build_operation(
        "GET /items",
        Status.FAILURE,
        failing_cases=[build_case("abc123", ["server_error"])],
        cases_per_phase={
            PhaseName.COVERAGE: PhaseCases(total=1, failed=1),
            PhaseName.FUZZING: PhaseCases(total=2, failed=0),
        },
    )
    document = render_operation(build_report([entry]), entry)
    assert "ps-sep" not in document


def test_render_operation_check_docs_links():
    entry = build_operation(
        "POST /orders", Status.FAILURE, failing_cases=[build_case("abc123", ["response_schema_conformance"])]
    )
    document = render_operation(build_report([entry]), entry)
    assert "reference/checks/#response_schema_conformance" in document


def test_render_operation_parent_trace():
    case = build_case("abc123", ["server_error"])
    case.parent_steps = [
        ParentStep(
            method="POST",
            path="/users",
            status_code=200,
            status_message="OK",
            elapsed_ms=88,
            detail='POST /users\n\n{"id":"7c91"}',
        )
    ]
    entry = build_operation("POST /orders", Status.FAILURE, failing_cases=[case])
    document = render_operation(build_report([entry]), entry)
    assert "parent-trace" in document
    assert "200 OK" in document
    assert "failed (this case)" in document


def test_render_operation_errors_for_this_label_only():
    entry = build_operation("POST /orders", Status.FAILURE, failing_cases=[build_case("abc123", ["server_error"])])
    other = build_operation("GET /health", Status.SUCCESS)
    data = build_report(
        [entry, other],
        errors=[
            ErrorEntry(label="POST /orders", title="RequestError", message="boom", traceback=None, phase="fuzzing"),
            ErrorEntry(label="GET /health", title="Other", message="nope", traceback=None, phase=None),
        ],
    )
    document = render_operation(data, entry)
    assert "RequestError" in document
    assert "nope" not in document


def test_render_operation_shows_warning_card_for_affected_label():
    entry = build_operation("GET /users", Status.SUCCESS)
    data = build_report([entry], warnings=WarningData(missing_auth={401: {"GET /users"}}))
    document = render_operation(data, entry)
    assert "Missing authentication" in document


def test_render_operation_hides_warning_card_for_unaffected_label():
    entry = build_operation("GET /users", Status.SUCCESS)
    other = build_operation("GET /orders", Status.SUCCESS)
    data = build_report([entry, other], warnings=WarningData(missing_auth={401: {"GET /orders"}}))
    document = render_operation(data, entry)
    assert "Missing authentication" not in document


def test_render_operation_no_warnings_section_without_warning_data():
    entry = build_operation("GET /users", Status.SUCCESS)
    document = render_operation(build_report([entry]), entry)
    assert "Warnings" not in document


def test_render_index_warnings_unaffected_by_per_operation_filtering():
    entry = build_operation("GET /users", Status.SUCCESS)
    data = build_report([entry], warnings=WarningData(missing_auth={401: {"GET /users"}}))
    document = render_index(data, {"GET /users": "GET__users"})
    assert "Missing authentication" in document


def test_render_operation_schema_definition_escaped():
    entry = build_operation("POST /orders", Status.FAILURE, failing_cases=[build_case("abc123", ["server_error"])])
    entry.definition = '{"summary": "<script>x</script>"}'
    document = render_operation(build_report([entry]), entry)
    assert "schema-snippet" in document
    assert "<script>x</script>" not in document


def test_render_index_matches_app_js_dom_contract():
    # app.js queries these hooks directly; a renderer/app.js drift breaks the report silently
    # in the browser with no test failure unless this is pinned.
    js = (files("schemathesis.reporting.html") / "assets" / "app.js").read_text(encoding="utf-8")
    sprite_ids = set(re.findall(r'<symbol id="(icon-[\w-]+)"', js))
    failed = build_operation("POST /orders", Status.FAILURE, failing_cases=[build_case("abc123", ["server_error"])])
    data = build_report([failed], warnings=WarningData(missing_auth={401: {"POST /orders"}}))
    document = render_index(data, {"POST /orders": "POST__orders"})

    assert 'data-filter="search"' in document
    assert 'class="filter-result"' in document
    assert re.search(r'<tr class="op-row row-failed"[^>]*\bdata-search-text="[^"]+"', document)

    copy_targets = re.findall(r'data-copy-target="#([\w-]+)"', document)
    assert copy_targets
    for target in copy_targets:
        assert f'id="{target}"' in document

    referenced_icons = set(re.findall(r'href="#(icon-[\w-]+)"', document))
    assert referenced_icons
    assert referenced_icons <= sprite_ids


def test_render_operation_matches_app_js_dom_contract():
    js = (files("schemathesis.reporting.html") / "assets" / "app.js").read_text(encoding="utf-8")
    sprite_ids = set(re.findall(r'<symbol id="(icon-[\w-]+)"', js))
    case = build_case("abc123", ["server_error"])
    case.curl = "curl -X POST 'http://x/orders' -d '{}'"
    entry = build_operation("POST /orders", Status.FAILURE, failing_cases=[case])
    document = render_operation(build_report([entry]), entry)

    assert re.search(r'<div class="op-hero-row">.*?<span class="path">', document)

    copy_targets = re.findall(r'data-copy-target="#([\w-]+)"', document)
    assert copy_targets
    for target in copy_targets:
        assert f'id="{target}"' in document

    referenced_icons = set(re.findall(r'href="#(icon-[\w-]+)"', document))
    assert referenced_icons
    assert referenced_icons <= sprite_ids


def test_render_operation_schema_section_is_collapsed_by_default():
    entry = build_operation("POST /orders", Status.FAILURE, failing_cases=[build_case("abc123", ["server_error"])])
    entry.definition = '{"summary": "<script>x</script>"}'
    document = render_operation(build_report([entry]), entry)
    assert '<details class="schema-details">' in document
    assert '<details class="schema-details" open' not in document
    assert "Schema" in document
    assert "sd-meta" not in document
    assert "paths./orders.post" not in document
    assert "<script>x</script>" not in document


def test_render_operation_visible_strings_use_ascii_punctuation():
    entry = build_operation("POST /orders", Status.FAILURE, error_count=1)
    document = render_operation(build_report([entry]), entry)
    assert "—" not in document
    assert "&middot;" not in document
    assert "&#9662;" not in document
