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
        failure_counts={"status_code_conformance": 1},
    )


def test_render_markdown_baseline_snapshot(snapshot):
    assert render_markdown(_sample_run()) == snapshot


def test_render_markdown_with_stateful_snapshot(snapshot):
    run = _sample_run()
    run.stateful = OperationMetrics(
        label="Stateful tests",
        buckets=Bucket(positive_accepted=10, positive_drift=20, server_error=5),
        wasted_by_location={"body": 12, "path": 8},
    )
    assert render_markdown(run) == snapshot


def test_render_markdown_with_5xx_operation_snapshot(snapshot):
    run = _sample_run()
    run.operations["GET /flaky"] = OperationMetrics(
        label="GET /flaky", buckets=Bucket(positive_accepted=2, server_error=8)
    )
    assert render_markdown(run) == snapshot


def test_render_markdown_with_time_consuming_operations_snapshot(snapshot):
    run = _sample_run()
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
    assert render_markdown(run) == snapshot


def test_render_markdown_drift_dominant_headline_snapshot(snapshot):
    run = _sample_run()
    # Force a single dominant signal: 100% positive_drift.
    run.buckets = Bucket(positive_drift=100)
    assert render_markdown(run) == snapshot


def test_render_markdown_schema_quality_callout_snapshot(snapshot):
    run = _sample_run()
    run.buckets.negative_drift = 600  # crosses the 500-count threshold
    assert render_markdown(run) == snapshot


def test_render_markdown_failure_summary_with_two_fingerprints_snapshot(snapshot):
    # Same check class, two distinct failure types -> 2 unique fingerprints.
    run = _sample_run()
    run.failures = [
        FailureRef(
            check_name="negative_data_rejection",
            operation_label="POST /a",
            failure_type="MissingProperty",
            message="x",
        ),
        FailureRef(
            check_name="negative_data_rejection",
            operation_label="POST /a",
            failure_type="InvalidFormat",
            message="y",
        ),
    ]
    run.failure_counts = {"negative_data_rejection": 2}
    assert render_markdown(run) == snapshot


def test_render_markdown_no_top_5xx_section_when_no_5xx():
    run = _sample_run()
    for operation in run.operations.values():
        operation.buckets.server_error = 0
    assert "## Top server-error operations" not in render_markdown(run)


def test_render_markdown_no_stateful_section_when_absent():
    assert "## Stateful summary" not in render_markdown(_sample_run())


def test_render_markdown_no_headline_when_balanced():
    run = _sample_run()
    # Every bucket equal -> nothing crosses the 25% threshold.
    run.buckets = Bucket(
        positive_accepted=10,
        negative_rejected=10,
        positive_drift=10,
        negative_drift=10,
        server_error=10,
        route_rejected=10,
        auth_rejected=10,
        other=10,
    )
    text = render_markdown(run)
    assert "**Dominant signal:**" not in text
    assert "**Dominant signals:**" not in text


def test_render_markdown_no_schema_quality_callout_below_threshold():
    run = _sample_run()
    run.buckets.negative_drift = 1
    run.buckets.positive_accepted = 10000  # forces 1 / total below 5%
    assert "Schema-quality finding" not in render_markdown(run)


def test_render_markdown_no_top_time_section_when_no_timings():
    run = _sample_run()
    run.phases = []
    for operation in run.operations.values():
        operation.generation_seconds = 0.0
        operation.response_seconds = 0.0
    assert "## Top time-consuming operations" not in render_markdown(run)


def test_render_markdown_unaccounted_question_mark_when_negative():
    # Defensive: gen+net somehow exceeds wall (clock skew, double-counting). Render `?`
    # rather than a misleading negative number.
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
    assert " ? | ? |" in render_markdown(run)


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
