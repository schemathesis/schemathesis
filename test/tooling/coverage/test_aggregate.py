from __future__ import annotations

from tools.coverage.aggregate import aggregate, render_markdown
from tools.coverage.audit import SchemaResult


def _result(**kwargs):
    return SchemaResult(api=kwargs.pop("api", "x"), corpus=kwargs.pop("corpus", "c"), phase="fuzzing", **kwargs)


def test_aggregate_empty_input_returns_zeroed_summary():
    assert aggregate([]) == {
        "phase": None,
        "apis_total": 0,
        "apis_with_results": 0,
        "apis_errored": 0,
        "cases_generated": 0,
        "duration_seconds": 0.0,
        "rates": {
            "operations": {"covered": 0, "total": 0, "pct": 0.0},
            "keywords_full_only": {"covered": 0, "total": 0, "pct": 0.0},
            "keywords_partial_or_full": {"covered": 0, "total": 0, "pct": 0.0},
            "parameters_partial_or_full": {"covered": 0, "total": 0, "pct": 0.0},
            "examples": {"covered": 0, "total": 0, "pct": 0.0},
        },
        "gap_kinds": [],
        "top_uncovered_paths": [],
        "worst_apis": [],
    }


def test_aggregate_rolls_up_keyword_buckets():
    summary = aggregate(
        [
            _result(api="a", statistic={"keywords": {"full": 3, "partial": 1, "total": 10}}),
            _result(api="b", statistic={"keywords": {"full": 5, "partial": 0, "total": 10}}),
        ]
    )
    assert summary["rates"]["keywords_full_only"] == {"covered": 8, "total": 20, "pct": 40.0}
    assert summary["rates"]["keywords_partial_or_full"] == {"covered": 9, "total": 20, "pct": 45.0}


def test_aggregate_counts_gap_kinds_and_marks_errored_apis():
    summary = aggregate(
        [
            _result(
                statistic={"operations": {"seen": 1, "total": 1}},
                gaps=[
                    {"kind": "operation_unseen"},
                    {"kind": "response_uncovered"},
                    {"kind": "operation_unseen"},
                ],
                errors=["boom"],
            ),
        ]
    )
    assert summary["apis_errored"] == 1
    assert summary["gap_kinds"] == [("operation_unseen", 2), ("response_uncovered", 1)]


def test_aggregate_picks_worst_apis_by_keyword_percent():
    summary = aggregate(
        [
            _result(api="strong", statistic={"keywords": {"full": 9, "total": 10}}),
            _result(api="weak", statistic={"keywords": {"full": 1, "total": 10}}),
            _result(api="medium", statistic={"keywords": {"full": 5, "total": 10}}),
        ]
    )
    assert [entry["api"] for entry in summary["worst_apis"]] == ["weak", "medium", "strong"]


def test_render_markdown_includes_phase_and_metrics():
    summary = aggregate([_result(statistic={"operations": {"seen": 1, "total": 2}})])
    md = render_markdown(summary)
    assert "fuzzing phase" in md
    assert "| operations | 1 | 2 | 50.0% |" in md
