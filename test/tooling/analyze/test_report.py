import json

from scripts.analyze.metrics import (
    Bucket,
    FailureRef,
    OperationMetrics,
    PhaseMetrics,
    RunMetrics,
    analyze,
)
from scripts.analyze.report import render_json, render_markdown

_BUCKET_FIELDS = (
    "positive_accepted",
    "negative_rejected",
    "positive_drift",
    "negative_drift",
    "server_error",
    "route_rejected",
    "auth_rejected",
    "other",
)


def _sample_run() -> RunMetrics:
    return RunMetrics(
        schemathesis_version="4.16.1",
        seed=1,
        command="schemathesis run http://example.com/openapi.json",
        duration_seconds=4.5,
        buckets=Bucket(
            positive_accepted=8,
            negative_rejected=4,
            positive_drift=5,
            negative_drift=2,
            server_error=1,
            route_rejected=1,
            auth_rejected=2,
            other=2,
        ),
        status_histogram={200: 8, 422: 5, 400: 4, 503: 1, 401: 2, 404: 1, 302: 2},
        phases=[
            PhaseMetrics(
                name="Coverage",
                duration_seconds=3.0,
                buckets=Bucket(positive_accepted=6, positive_drift=4, negative_rejected=2),
            ),
            PhaseMetrics(
                name="Fuzzing",
                duration_seconds=1.5,
                buckets=Bucket(
                    positive_accepted=2,
                    positive_drift=1,
                    negative_rejected=2,
                    negative_drift=2,
                    server_error=1,
                    auth_rejected=2,
                    route_rejected=1,
                    other=2,
                ),
                truncated=True,
            ),
        ],
        operations={
            "POST /echo-validate": OperationMetrics(
                label="POST /echo-validate",
                buckets=Bucket(positive_accepted=2, positive_drift=4),
                wasted_by_location={"body": 4},
                failures=[
                    FailureRef(
                        check_name="status_code_conformance",
                        operation_label="POST /echo-validate",
                        failure_type="UndefinedStatusCode",
                        message="422",
                    )
                ],
            ),
            "GET /always-200": OperationMetrics(label="GET /always-200", buckets=Bucket(positive_accepted=8)),
        },
        failures=[
            FailureRef(
                check_name="status_code_conformance",
                operation_label="POST /echo-validate",
                failure_type="UndefinedStatusCode",
                message="422",
            )
        ],
    )


def test_render_markdown_sections():
    text = render_markdown(_sample_run())
    for header in (
        "# Schemathesis run report",
        "## Budget utilisation",
        "**Handler-reached:",
        "**Drift:",
        "## Top wasted operations",
        "## Phases",
        "## Status codes (run-wide)",
        "## Failures",
    ):
        assert header in text, f"missing {header!r}"


def test_render_markdown_drift_split_in_summary():
    text = render_markdown(_sample_run())
    # Drift summary line includes both directions explicitly.
    assert "P+4xx 5" in text
    assert "N+2xx 2" in text


def test_render_markdown_truncated_phase_marked():
    assert "1.5s*" in render_markdown(_sample_run())


def test_render_markdown_top_wasted_includes_echo_validate():
    text = render_markdown(_sample_run())
    assert "POST /echo-validate" in text
    assert "body" in text


def test_render_markdown_drift_by_location_section_present():
    assert "### Drift by location" in render_markdown(_sample_run())


def test_render_markdown_top_wasted_breakdown_columns():
    text = render_markdown(_sample_run())
    # New columns expose the WHY of waste at a glance.
    assert "| Operation | Total | Drift | Auth | Route | 5xx | Top loc |" in text


def test_render_markdown_top_5xx_section_present_when_5xx():
    run = _sample_run()
    run.operations["GET /flaky"] = OperationMetrics(
        label="GET /flaky",
        buckets=Bucket(positive_accepted=2, server_error=8),
    )
    text = render_markdown(run)
    assert "## Top server-error operations" in text
    assert "GET /flaky" in text
    assert "8 | 80.0%" in text


def test_render_markdown_no_top_5xx_section_when_no_5xx():
    run = _sample_run()
    # Clear all per-op 5xx
    for op in run.operations.values():
        op.buckets.server_error = 0
    assert "## Top server-error operations" not in render_markdown(run)


def test_render_markdown_headline_for_dominant_drift():
    run = _sample_run()
    # Force positive_drift dominance.
    run.buckets.positive_drift = 100
    run.buckets.positive_accepted = 0
    run.buckets.negative_rejected = 0
    run.buckets.server_error = 0
    run.buckets.negative_drift = 0
    run.buckets.route_rejected = 0
    run.buckets.auth_rejected = 0
    run.buckets.other = 0
    text = render_markdown(run)
    assert "**Dominant signal:** schema/data drift (P+4xx) (100.0%)" in text


def test_render_markdown_headline_skipped_when_balanced():
    run = _sample_run()
    for attr in _BUCKET_FIELDS:
        setattr(run.buckets, attr, 10)
    text = render_markdown(run)
    assert "**Dominant signal:**" not in text
    assert "**Dominant signals:**" not in text


def test_render_markdown_stateful_summary_section_when_present():
    run = _sample_run()
    run.stateful = OperationMetrics(
        label="Stateful tests",
        buckets=Bucket(positive_accepted=10, positive_drift=20, server_error=5),
        wasted_by_location={"body": 12, "path": 8},
    )
    text = render_markdown(run)
    assert "## Stateful summary" in text
    assert "`Stateful tests`" in text
    assert "Drift by location: body 12, path 8" in text


def test_render_markdown_no_stateful_section_when_absent():
    assert "## Stateful summary" not in render_markdown(_sample_run())


def test_render_markdown_phases_table_has_timing_columns():
    text = render_markdown(_sample_run())
    assert "| Phase | Wall | Calls | Gen total | Gen/case | Net total | Net/case | Unaccounted | Unaccounted% |" in text


def test_render_markdown_drops_empty_phases():
    run = _sample_run()
    run.phases.append(PhaseMetrics(name="Stateful", duration_seconds=0.0, buckets=Bucket()))
    text = render_markdown(run)
    # Empty phase row must not render even though it's present in run.phases.
    phases_section = text.split("## Phases", 1)[1].split("\n\n", 1)[0]
    assert "Stateful" not in phases_section


def test_render_markdown_phases_show_timings_and_unaccounted():
    run = _sample_run()
    # Replace existing phases with one whose timings are easy to verify.
    run.phases = [
        PhaseMetrics(
            name="Fuzzing",
            duration_seconds=100.0,
            buckets=Bucket(positive_accepted=10),
            generation_seconds=20.0,
            response_seconds=30.0,
        )
    ]
    text = render_markdown(run)
    # 100s wall - 20s gen - 30s net = 50s unaccounted, 50.0%
    assert "| Fuzzing | 1m 40s | 10 | 20.0s | 2000.0ms | 30.0s | 3000.0ms | 50.0s | 50.0% |" in text


def test_render_markdown_top_time_consuming_section():
    run = _sample_run()
    # Per-phase fixture: 100s wall, 60 calls, gen 20s, net 30s -> unaccounted 50s.
    run.phases = [
        PhaseMetrics(
            name="Fuzzing",
            duration_seconds=100.0,
            buckets=Bucket(positive_accepted=60),
            generation_seconds=20.0,
            response_seconds=30.0,
        )
    ]
    # One op holds 30/60 of fuzzing's calls; expected unaccounted share = 25.0s.
    run.operations = {
        "POST /slow": OperationMetrics(
            label="POST /slow",
            buckets=Bucket(positive_accepted=30),
            generation_seconds=15.0,
            response_seconds=10.0,
        ),
        "GET /fast": OperationMetrics(
            label="GET /fast",
            buckets=Bucket(positive_accepted=30),
            generation_seconds=5.0,
            response_seconds=20.0,
        ),
    }
    text = render_markdown(run)
    assert "## Top time-consuming operations" in text
    # POST /slow total = 15 + 10 + 25 = 50s, GET /fast total = 5 + 20 + 25 = 50s.
    # Both rows present; POST /slow comes first (tie broken by insertion order in dict).
    assert "| POST /slow | 30 | 15.0s | 10.0s | 25.0s | 50.0s |" in text
    assert "| GET /fast | 30 | 5.0s | 20.0s | 25.0s | 50.0s |" in text


def test_render_markdown_no_top_time_section_when_no_timings():
    run = _sample_run()
    run.phases = []
    for op in run.operations.values():
        op.generation_seconds = 0.0
        op.response_seconds = 0.0
    assert "## Top time-consuming operations" not in render_markdown(run)


def test_render_markdown_unaccounted_question_mark_when_negative():
    # Defensive: gen+net somehow > wall (clock skew, double-counting). Render `?` not a
    # negative number that would mislead the reader.
    run = _sample_run()
    run.phases = [
        PhaseMetrics(
            name="Fuzzing",
            duration_seconds=10.0,
            buckets=Bucket(positive_accepted=1),
            generation_seconds=8.0,
            response_seconds=5.0,
        )
    ]
    text = render_markdown(run)
    assert " ? | ? |" in text


def test_render_markdown_failure_lines_show_occurrences():
    run = _sample_run()
    run.failure_counts = {"status_code_conformance": 50}
    text = render_markdown(run)
    assert "1 unique failures, 50 total occurrences" in text
    assert "`status_code_conformance` — 1 unique, 50 occurrences across 1 op" in text


def test_render_markdown_failure_summary_counts_distinct_fingerprints():
    # Same check class, two distinct failure types -> 2 unique fingerprints. The summary must
    # report 2, matching the per-row "unique" total, not collapse to "1 unique failure class".
    run = _sample_run()
    run.failures = [
        FailureRef(
            check_name="negative_data_rejection", operation_label="POST /a", failure_type="MissingProperty", message="x"
        ),
        FailureRef(
            check_name="negative_data_rejection", operation_label="POST /a", failure_type="InvalidFormat", message="y"
        ),
    ]
    run.failure_counts = {"negative_data_rejection": 2}
    text = render_markdown(run)
    assert "2 unique failures, 2 total occurrences" in text
    assert "`negative_data_rejection` — 2 unique, 2 occurrences across 1 op" in text


def test_render_markdown_schema_quality_callout_fires_above_threshold():
    run = _sample_run()
    run.buckets.negative_drift = 600  # crosses 500-count threshold
    text = render_markdown(run)
    assert "**Schema-quality finding:** server accepted 600 supposedly-invalid payloads" in text


def test_render_markdown_no_schema_quality_callout_below_threshold():
    run = _sample_run()
    run.buckets.negative_drift = 1
    # Force a high total so 1 / total is below 5%.
    run.buckets.positive_accepted = 10000
    text = render_markdown(run)
    assert "Schema-quality finding" not in text


def test_render_json_round_trips_run_metrics(analyzer_ndjson):
    data = json.loads(render_json(analyze(analyzer_ndjson)))
    assert set(data) >= {"schemathesis_version", "buckets", "operations", "phases", "failures", "status_histogram"}
    assert set(data["buckets"]) >= {
        "positive_accepted",
        "negative_rejected",
        "positive_drift",
        "negative_drift",
        "server_error",
        "route_rejected",
        "auth_rejected",
        "other",
    }
