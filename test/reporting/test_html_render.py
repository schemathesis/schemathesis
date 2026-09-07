import pytest

from schemathesis.cli.summary import WarningData
from schemathesis.engine import Status, StopReason
from schemathesis.engine.run import PhaseName
from schemathesis.reporting.html.model import ReportMeta, Verdict
from schemathesis.reporting.html.render import render_index
from schemathesis.reporting.html.render.components import (
    errors_section,
    esc,
    humanize_duration,
    label_html,
    method_span,
    page,
    path_span,
    warning_cards,
    warnings_section,
)
from test.reporting.factories import (
    build_report,
    build_summary,
    empty_report,
    error_entry,
    errored_report,
    failing_report,
    failure_group,
    fatal_report,
    interrupted_report,
    never_finished_report,
    passing_report,
    stateful_only_report,
    stateful_report,
    warnings_report,
)


def test_esc_escapes_html_metacharacters():
    assert esc('<script>alert("x")</script>') == "&lt;script&gt;alert(&quot;x&quot;)&lt;/script&gt;"


def test_esc_escapes_lone_surrogates():
    assert esc("\ud800") == r"\ud800"


def test_page_escapes_title():
    assert "<script>" not in page(title="<script>", body="", asset_prefix="")


def test_page_inlines_icon_sprite_and_no_script():
    document = page(title="T", body="", asset_prefix="")
    assert '<symbol id="icon-logo"' in document
    assert "<script" not in document


def test_method_and_path_spans():
    assert method_span("post") == '<span class="method post">POST</span>'
    assert path_span("/users/{id}") == '<span class="path">/users/{id}</span>'


def test_label_html_graphql_has_no_method_badge():
    assert label_html("Query.getBooks") == '<span class="path">Query.getBooks</span>'


def test_label_html_stateful_label_has_no_method_badge():
    assert label_html("Stateful tests") == '<span class="path">Stateful tests</span>'


def test_warning_cards_empty():
    assert warning_cards(WarningData()) == []


def test_warning_cards_escape_labels():
    assert "<script>" not in "".join(warning_cards(WarningData(missing_test_data={'GET /<script>"x"'})))


def test_warning_cards_render_every_kind():
    html = "".join(warning_cards(warnings_report().summary.warnings))
    for title in (
        "Authentication failed",
        "Base URL may be missing a path",
        "Missing test data",
        "Schema validation mismatch",
        "Schema validation skipped",
        "Unused OpenAPI auth",
        "Unmatched filters",
        "Method Not Allowed",
        "Unsupported regex patterns",
        "Unresolvable references",
        "Constant reuse skipped",
    ):
        assert title in html, title
    assert "http://127.0.0.1/api" in html


def test_warnings_section_eyebrow_count_matches_card_count():
    html = warnings_section(warnings_report().summary.warnings)
    cards = html.count('<article class="mg op-mg"')
    assert f'<span class="num">{cards}</span>' in html


def test_errors_section_empty():
    assert errors_section([], None) == ""


def test_errors_section_renders_detail_count_and_fatal():
    html = errors_section(
        [error_entry("Connection <b>", count=2, message="reset <b>", traceback="tb <b>")],
        error_entry("Crash", operations=[], message="fatal <b>", phase=None),
    )
    assert "Connection &lt;b&gt;" in html
    assert "reset &lt;b&gt;" in html
    assert "2 occurrences" in html
    assert "<summary" in html and "tb &lt;b&gt;" in html
    assert "Crash" in html and "fatal &lt;b&gt;" in html
    assert "<b>" not in html


def test_errors_section_escapes_phase_once():
    html = errors_section([error_entry("Boom", phase="fuzz<b>")], None)
    assert "fuzz&lt;b&gt; phase" in html
    assert "&amp;" not in html


def test_humanize_duration():
    assert humanize_duration(7203) == "2h 0m"
    assert humanize_duration(3660) == "1h 1m"
    assert humanize_duration(134.2) == "2m 14s"
    assert humanize_duration(42.13) == "42.1s"
    assert humanize_duration(0.4) == "0.4s"


def test_humanize_duration_rounds_before_branching_to_minutes():
    # 59.96 rounds to 60.0 at 1-decimal display precision, so it must cross into "1m 0s".
    assert humanize_duration(59.96) == "1m 0s"


@pytest.mark.parametrize(
    "report, verdict",
    [
        (passing_report(), Verdict.PASSED),
        (failing_report(), Verdict.FAILED),
        (errored_report(), Verdict.ERRORED),
        (fatal_report(), Verdict.ERRORED),
        (empty_report(), Verdict.EMPTY),
        (interrupted_report(), Verdict.INTERRUPTED),
        (never_finished_report(), Verdict.INTERRUPTED),
        (build_report(started=False, complete=False, stop_reason=StopReason.INTERRUPTED, exit_code=1), Verdict.ERRORED),
        (build_report(exit_code=1), Verdict.FAILED),
    ],
    ids=[
        "passed",
        "failed",
        "errored",
        "fatal",
        "empty",
        "ctrl-c",
        "never-finished",
        "never-started",
        "fail-on-warning",
    ],
)
def test_verdict(report, verdict):
    assert report.verdict == verdict


def test_failed_operations_is_union_of_failure_groups():
    assert failing_report().failed_operations == ["GET /orders/{id}", "POST /orders"]


def test_failed_operations_excludes_stateful_pseudo_label():
    assert stateful_report().failed_operations == ["POST /orders"]


def test_failed_operations_excludes_unsupported_method_probe_labels():
    report = build_report(
        build_summary(
            failures=[
                failure_group("Server error", "GET /users"),
                failure_group("Unsupported method", "TRACE /users", type="UnsupportedMethodResponse"),
            ]
        )
    )
    assert report.failed_operations == ["GET /users"]


def test_top_failures_sorted_by_count_then_title():
    report = build_report(
        build_summary(
            failures=[failure_group("B", "GET /b"), failure_group("A", "GET /a"), failure_group("C", "GET /c", count=5)]
        )
    )
    assert [group.title for group in report.top_failures] == ["C", "A", "B"]


def test_executed_phases_excludes_internal_and_skipped():
    phases = {
        PhaseName.PROBING: (Status.SUCCESS, None),
        PhaseName.SCHEMA_ANALYSIS: (Status.SUCCESS, None),
        PhaseName.EXAMPLES: (Status.SKIP, None),
        PhaseName.COVERAGE: (Status.ERROR, None),
        PhaseName.FUZZING: (Status.SUCCESS, None),
    }
    assert build_report(build_summary(phases=phases)).executed_phases == 2


def test_render_index_failed_run():
    html = render_index(failing_report())
    assert '<h1 class="hero-status-label">Failed</h1>' in html
    assert "2 of 3 operations failed" in html
    assert "Server error" in html
    assert '<span class="path">/orders/{id}</span>' in html
    assert "Connection error" in html
    assert "<script" not in html


def test_render_index_all_passed():
    html = render_index(passing_report())
    assert '<h1 class="hero-status-label">Passed</h1>' in html
    assert "1 operation passed" in html
    assert "all passing" in html
    assert "failures-section" not in html
    assert "errors-section" not in html
    assert "warnings-section" not in html


def test_render_index_hero_neutral_when_zero_operations_tested():
    html = render_index(empty_report())
    assert '<h1 class="hero-status-label">No tests ran</h1>' in html
    assert "fail-mix-bar" not in html


def test_render_index_fatal_error_sets_errored_verdict_and_appears_in_errors_section():
    html = render_index(fatal_report())
    assert '<h1 class="hero-status-label">Errored</h1>' in html
    assert "Schema loading error" in html
    assert "boom &lt;b&gt;" in html
    assert "hs-stop-note" not in html


def test_render_index_fatal_error_shows_top_failures_when_recorded():
    report = build_report(
        build_summary(failures=[failure_group("Server error", "GET /a")]),
        fatal_error=error_entry("Crash", operations=[], phase=None),
        stop_reason=StopReason.INTERRUPTED,
        complete=False,
        exit_code=1,
    )
    html = render_index(report)
    assert '<h1 class="hero-status-label">Errored</h1>' in html
    assert "Top failures" in html


def test_render_index_run_failed_without_failures_shows_no_failing_checks():
    html = render_index(build_report(exit_code=1))
    assert "Run failed" in html
    assert "no failing checks" in html
    assert "failures-section" not in html


def test_render_index_stateful_only_failures_have_their_own_subtitle():
    html = render_index(stateful_only_report())
    assert "Failures not attributed to a tested operation" in html
    assert "fail-mix-bar" not in html
    assert '<span class="path">Stateful tests</span>' in html


def test_render_index_interrupted_verdict_has_no_redundant_note():
    html = render_index(interrupted_report())
    assert '<h1 class="hero-status-label">Interrupted</h1>' in html
    assert "hs-stop-note" not in html


def test_render_index_hero_omits_stop_reason_on_normal_completion():
    assert "hs-stop-note" not in render_index(passing_report())


@pytest.mark.parametrize(
    "stop_reason, text",
    [(StopReason.FAILURE_LIMIT, "Failure limit reached"), (StopReason.MAX_TIME, "Time limit reached")],
)
def test_render_index_hero_shows_stop_reason_when_stopped_early(stop_reason, text):
    assert f'<div class="hs-stop-note">{text}</div>' in render_index(build_report(stop_reason=stop_reason))


def test_render_index_failed_count_ignores_unsupported_method_probes():
    # Probes for methods the schema never lists are keyed by the probed method, not by a tested operation.
    report = build_report(
        build_summary(
            tested=10,
            failures=[
                failure_group("Server error", "GET /users"),
                failure_group("Unsupported method", "TRACE /users", "TRACE /orders", type="UnsupportedMethodResponse"),
            ],
        ),
        exit_code=1,
    )
    assert "1 of 10 operations failed" in render_index(report)


def test_render_index_hero_omits_passed_segment_when_every_tested_operation_failed():
    report = build_report(build_summary(tested=1, failures=[failure_group("Server error", "GET /users")]), exit_code=1)
    html = render_index(report)
    assert "seg-failed" in html
    assert "seg-passed" not in html


def test_render_index_escapes_operation_paths():
    report = build_report(build_summary(failures=[failure_group("Server error", 'GET /<script>"x"')]), exit_code=1)
    html = render_index(report)
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_render_index_operations_cell_lists_skipped_and_errored():
    html = render_index(build_report(build_summary(tested=11, skipped=2, errored=1)))
    assert (
        '<span class="m-label">Operations</span><span class="m-value">11</span>'
        '<span class="m-sub">2 skipped · 1 errored</span>'
    ) in html


def test_render_index_target_block_has_exit_code_and_omits_missing_meta():
    report = build_report(
        meta=ReportMeta(generated_at="x", location=None, base_url=None, command="st run", seed=None), exit_code=1
    )
    html = render_index(report)
    assert "Base URL" not in html
    assert "Seed" not in html
    assert "st run" in html
    assert '<span class="tk">Exit code</span><span class="tv"><code>1</code></span>' in html


def test_render_index_failures_precede_target_block():
    html = render_index(failing_report())
    assert html.index("failures-section") < html.index("target-block")


def test_render_index_visible_strings_use_ascii_punctuation():
    for report in (failing_report(), passing_report(), warnings_report(), fatal_report()):
        assert "—" not in render_index(report)
